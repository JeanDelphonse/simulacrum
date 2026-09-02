"""Careers — public job board, application intake, admin review.

Covers the boundary that matters here: an unauthenticated stranger can post one
application per role with a PDF or Word resume and nothing else, and only an admin
can list, preview, retitle or delete what came in. Against in-memory SQLite with a
temp upload folder; no network, no real data.

    python test_careers.py
"""
import io
import os
import shutil
import sys
import tempfile
from datetime import datetime

os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label,
                              ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def make_pdf_bytes(text):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 96), text)
    data = doc.tobytes()
    doc.close()
    return data


def make_docx_bytes(text):
    from docx import Document
    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def main():
    from app import create_app
    from app.extensions import db, bcrypt
    from utils.id_gen import generate_id

    upload_dir = tempfile.mkdtemp(prefix='careers_test_')
    app = create_app('testing')
    app.config.update(
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        UPLOAD_FOLDER=upload_dir,
        WTF_CSRF_ENABLED=False,
    )

    try:
        with app.app_context():
            from app.models.job_application import JobApplication
            from app.models.user import User
            from app.services import careers_service

            db.create_all()

            # onboarding_completed_at is set because the app-wide onboarding gate
            # redirects any half-onboarded login away from HTML pages.
            now = datetime.utcnow()
            admin = User(id=generate_id(), email='admin@t.local', full_name='Admin',
                         password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                         is_admin=True, email_verified=True, onboarding_completed_at=now)
            plain = User(id=generate_id(), email='user@t.local', full_name='Plain',
                         password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                         email_verified=True, onboarding_completed_at=now)
            db.session.add_all([admin, plain])
            db.session.commit()

            jobs = careers_service.list_jobs()
            check('all four roles load from config/jobs.json', len(jobs) == 4,
                  '{} roles'.format(len(jobs)))
            check('every role has the fields the page renders',
                  all(all(j.get(k) for k in ('slug', 'title', 'track', 'tagline', 'owns',
                                             'responsibilities', 'north_star',
                                             'supporting_metrics', 'looking_for'))
                      for j in jobs))
            slug = jobs[0]['slug']

            # ── Public pages ────────────────────────────────────────────────
            client = app.test_client()

            res = client.get('/careers')
            body = res.get_data(as_text=True)
            check('/careers renders', res.status_code == 200, res.status_code)
            from markupsafe import escape
            check('/careers lists every role title',
                  all(str(escape(j['title'])) in body for j in jobs))

            res = client.get('/careers/' + slug)
            detail = res.get_data(as_text=True)
            check('/careers/<slug> renders', res.status_code == 200, res.status_code)
            check('role detail shows the responsibilities from the doc',
                  jobs[0]['responsibilities'][0] in detail)
            check('role detail carries the apply form', 'id="applyForm"' in detail)

            check('unknown role 404s', client.get('/careers/not-a-role').status_code == 404)

            # ── Apply: happy path (PDF) ─────────────────────────────────────
            pdf = make_pdf_bytes('Jane Applicant - 10 years of growth marketing')
            res = client.post('/api/careers/apply', data={
                'job_slug': slug,
                'full_name': 'Jane Applicant',
                'email': 'Jane@Example.com',
                'phone': '(555) 123-4567',
                'resume': (io.BytesIO(pdf), 'jane_resume.pdf'),
            }, content_type='multipart/form-data')
            check('PDF application accepted', res.status_code == 201,
                  '{} {}'.format(res.status_code, res.get_data(as_text=True)[:160]))

            app1 = JobApplication.query.filter_by(email='jane@example.com').first()
            check('email normalized to lowercase', app1 is not None)
            check('role title snapshotted', app1 and app1.job_title == jobs[0]['title'])
            check('resume written to the careers subfolder',
                  app1 and os.path.exists(app1.resume_path)
                  and 'careers' in app1.resume_path.replace('\\', '/'))
            check('PDF text extracted for preview',
                  app1 and app1.resume_text and 'growth marketing' in app1.resume_text)
            check('status starts at new', app1 and app1.status == JobApplication.STATUS_NEW)

            # ── Apply: happy path (Word) ────────────────────────────────────
            docx = make_docx_bytes('Sam Word - ran cohorts for 1200 learners')
            res = client.post('/api/careers/apply', data={
                'job_slug': jobs[1]['slug'],
                'full_name': 'Sam Word',
                'email': 'sam@example.com',
                'phone': '555-987-6543',
                'resume': (io.BytesIO(docx), 'sam resume.docx'),
            }, content_type='multipart/form-data')
            check('Word application accepted', res.status_code == 201, res.status_code)
            app2 = JobApplication.query.filter_by(email='sam@example.com').first()
            check('DOCX text extracted for preview',
                  app2 and app2.resume_text and 'cohorts' in app2.resume_text)

            # ── Apply: rejections ───────────────────────────────────────────
            def post_bad(**over):
                data = {
                    'job_slug': jobs[2]['slug'],
                    'full_name': 'Bad Actor',
                    'email': 'bad@example.com',
                    'phone': '5551234567',
                    'resume': (io.BytesIO(pdf), 'ok.pdf'),
                }
                data.update(over)
                return client.post('/api/careers/apply', data=data,
                                   content_type='multipart/form-data')

            check('unknown job_slug rejected', post_bad(job_slug='ghost-role').status_code == 400)
            check('bad email rejected', post_bad(email='not-an-email').status_code == 400)
            check('bad phone rejected', post_bad(phone='abc').status_code == 400)
            check('empty name rejected', post_bad(full_name=' ').status_code == 400)
            check('executable disguised as resume rejected',
                  post_bad(resume=(io.BytesIO(b'MZ'), 'payload.exe')).status_code == 400)
            check('missing resume rejected',
                  client.post('/api/careers/apply', data={
                      'job_slug': slug, 'full_name': 'No File',
                      'email': 'nofile@example.com', 'phone': '5551234567',
                  }, content_type='multipart/form-data').status_code == 400)

            before = JobApplication.query.count()
            res = client.post('/api/careers/apply', data={
                'job_slug': slug,
                'full_name': 'Jane Applicant',
                'email': 'jane@example.com',
                'phone': '(555) 123-4567',
                'resume': (io.BytesIO(pdf), 'again.pdf'),
            }, content_type='multipart/form-data')
            check('duplicate application for the same role rejected (409)',
                  res.status_code == 409, res.status_code)
            check('duplicate left no extra row', JobApplication.query.count() == before)
            check('duplicate left no orphan file on disk',
                  len(os.listdir(os.path.join(upload_dir, 'careers'))) == before)

            # ── Admin panel is admin-only ───────────────────────────────────
            check('anonymous cannot list applications',
                  client.get('/api/admin/careers/applications').status_code in (302, 401, 403))
            check('anonymous cannot open the admin page',
                  client.get('/admin/careers').status_code in (302, 401, 403))

            user_client = app.test_client()
            user_client.post('/api/auth/login', json={'email': 'user@t.local',
                                                      'password': 'pw12345678'})
            check('non-admin cannot list applications',
                  user_client.get('/api/admin/careers/applications').status_code == 403)
            check('non-admin cannot download a resume',
                  user_client.get('/api/admin/careers/applications/{}/resume'
                                  .format(app1.id)).status_code == 403)
            check('non-admin cannot delete an application',
                  user_client.delete('/api/admin/careers/applications/{}'
                                     .format(app1.id)).status_code == 403)

            # ── Admin review ────────────────────────────────────────────────
            admin_client = app.test_client()
            login = admin_client.post('/api/auth/login', json={'email': 'admin@t.local',
                                                               'password': 'pw12345678'})
            check('admin can log in', login.status_code == 200, login.status_code)

            res = admin_client.get('/admin/careers')
            check('/admin/careers renders for admin', res.status_code == 200, res.status_code)

            res = admin_client.get('/api/admin/careers/applications')
            data = res.get_json()
            check('admin list returns both applications', len(data['applications']) == 2,
                  len(data['applications']))
            check('list is newest first',
                  data['applications'][0]['full_name'] == 'Sam Word')
            check('status counts reported', data['counts']['new'] == 2, data['counts'])
            check('per-role counts reported', data['per_job'].get(slug) == 1, data['per_job'])
            check('list omits the resume text (kept for the detail call)',
                  'resume_text' not in data['applications'][0])

            res = admin_client.get('/api/admin/careers/applications?q=jane')
            check('search by email narrows the list',
                  len(res.get_json()['applications']) == 1)
            res = admin_client.get('/api/admin/careers/applications?job=' + jobs[1]['slug'])
            check('filter by role narrows the list',
                  len(res.get_json()['applications']) == 1)
            res = admin_client.get('/api/admin/careers/applications?status=hired')
            check('filter by status narrows the list',
                  len(res.get_json()['applications']) == 0)

            res = admin_client.get('/api/admin/careers/applications/' + app1.id)
            detail_data = res.get_json()
            check('detail includes the extracted resume text',
                  'growth marketing' in (detail_data.get('resume_text') or ''))
            check('detail flags the file as present', detail_data['file_exists'] is True)
            check('PDF marked previewable', detail_data['previewable'] is True)

            # ── Resume preview / download ───────────────────────────────────
            res = admin_client.get('/api/admin/careers/applications/{}/resume'.format(app1.id))
            check('PDF served inline for the iframe preview',
                  res.status_code == 200
                  and res.mimetype == 'application/pdf'
                  and 'attachment' not in (res.headers.get('Content-Disposition') or ''),
                  res.headers.get('Content-Disposition'))
            check('PDF bytes served intact', res.get_data() == pdf)

            res = admin_client.get('/api/admin/careers/applications/{}/resume?download=1'
                                   .format(app1.id))
            check('?download=1 forces an attachment',
                  'attachment' in (res.headers.get('Content-Disposition') or ''))

            res = admin_client.get('/api/admin/careers/applications/{}/resume'.format(app2.id))
            check('Word resume served as an attachment (browsers cannot render it)',
                  res.status_code == 200
                  and 'attachment' in (res.headers.get('Content-Disposition') or ''))
            check('Word resume keeps its Word mimetype',
                  res.mimetype.endswith('wordprocessingml.document'), res.mimetype)

            check('missing application 404s',
                  admin_client.get('/api/admin/careers/applications/ZZZZZZZZZ').status_code == 404)

            # ── Status + note ───────────────────────────────────────────────
            res = admin_client.patch('/api/admin/careers/applications/' + app1.id,
                                     json={'status': 'shortlisted', 'admin_note': 'Strong SEO'})
            check('admin can set status and note', res.status_code == 200, res.status_code)
            db.session.expire_all()
            refreshed = db.session.get(JobApplication, app1.id)
            check('status persisted', refreshed.status == 'shortlisted', refreshed.status)
            check('note persisted', refreshed.admin_note == 'Strong SEO')
            check('reviewer recorded', refreshed.reviewed_by == admin.id
                  and refreshed.reviewed_at is not None)
            check('invalid status rejected',
                  admin_client.patch('/api/admin/careers/applications/' + app1.id,
                                     json={'status': 'promoted'}).status_code == 400)

            # ── Delete removes the file too ─────────────────────────────────
            path2 = refreshed_path = db.session.get(JobApplication, app2.id).resume_path
            res = admin_client.delete('/api/admin/careers/applications/' + app2.id)
            check('admin can delete an application', res.status_code == 200)
            check('deleted row is gone', db.session.get(JobApplication, app2.id) is None)
            check('deleted resume file is gone from disk', not os.path.exists(path2))
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        print('failed: ' + ', '.join(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
