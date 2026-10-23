"""Bio page source-data readiness.

The bug: a user could skip the resume step, have a 400-800 word "factual"
biography generated from their name alone, and publish it. Career history was
then extracted from that generated text, so the invented employers came back as
"data".

Covers the boundaries that close it: a bio is generated and published only with
a real resume, LinkedIn import or self-entered career history behind it;
scanned/blank uploads are refused at upload; generated text never counts as
source; a generated bio is checked against its source and unsupported claims
are removed; unpublishing takes the legacy profile page down too; and public
bios that predate the gate are flagged for an admin to keep or take down.

Against in-memory SQLite with a temp upload folder and a scripted fake model
client. No network.

    python test_bio_readiness.py
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


def section(title):
    print('\n' + title)


GOOD_RESUME = """Jordan Rivera
jordan.rivera@example.com | +1 415 555 0134 | linkedin.com/in/jordanrivera

PROFESSIONAL EXPERIENCE

Senior Product Manager, Northwind Logistics          Jan 2020 - Present
Led the routing platform rebuild across three regional hubs. Reduced late deliveries
by 18% over two quarters and owned the roadmap for a team of eleven engineers.

Product Manager, Contoso Health                      Mar 2016 - Dec 2019
Shipped the patient scheduling app used by 40 clinics. Ran discovery interviews and
partnered with clinical operations on the rollout plan.

Associate Consultant, Fabrikam Advisory               2013 - 2016
Supported pricing and operations engagements for mid-market retail clients.

EDUCATION
MBA, University of Michigan, 2013
BA Economics, Ohio State University, 2010
"""

COVER_LETTER = """Dear Hiring Manager,

I am writing to express my strong interest in the open role at your company. I have always
admired the way your team approaches difficult problems and I believe I would be a great fit
for the culture. Throughout my career I have learned that collaboration and curiosity matter
more than anything else, and I try to bring both to everything I do. I am a quick learner and
I enjoy working with people from many different backgrounds. I would welcome the chance to
discuss how my experience could contribute to your goals over the coming year. Thank you for
your time and consideration, and I look forward to hearing from you at your convenience.
Sincerely, a hopeful applicant who would very much like to join your growing organisation.
"""

# Fabricated, of the kind the old flow produced from a name alone.
FABRICATED_BIO = (
    'Pat Nobody is a strategy executive associated with Globex Corporation and '
    'Initech. **Career** Nobody joined Globex Corporation in 2009 and led its '
    'expansion into twelve markets.'
)


class _Resp:
    def __init__(self, text):
        self.content = [type('Block', (), {'text': text})()]


class FakeModel:
    """Scripted stand-in for the Anthropic client, keyed on which prompt it receives."""

    def __init__(self):
        self.calls = []
        self.bio = 'BIO'
        self.unsupported = []
        self.rewrite = 'REWRITTEN'
        self.verifier_raises = False
        self.forbid = False

    @property
    def messages(self):
        return self

    def create(self, model=None, max_tokens=None, system=None, messages=None):
        prompt = messages[0]['content']
        kind = ('verify' if prompt.startswith('You are a fact-checker')
                else 'rewrite' if prompt.startswith('Rewrite the BIO')
                else 'generate')
        self.calls.append((kind, prompt))
        if self.forbid:
            raise AssertionError('model called on a path that must not call it')
        if kind == 'verify':
            if self.verifier_raises:
                raise RuntimeError('verifier unavailable')
            import json
            return _Resp(json.dumps({'unsupported': self.unsupported}))
        if kind == 'rewrite':
            return _Resp(self.rewrite)
        return _Resp(self.bio)

    def kinds(self):
        return [k for k, _ in self.calls]


def make_docx_bytes(text):
    from docx import Document
    doc = Document()
    for para in text.split('\n'):
        doc.add_paragraph(para)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def main():
    from app import create_app
    from app.extensions import db, bcrypt
    from utils.id_gen import generate_id

    upload_dir = tempfile.mkdtemp(prefix='bio_ready_test_')
    app = create_app('testing')
    app.config.update(
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        UPLOAD_FOLDER=upload_dir,
        WTF_CSRF_ENABLED=False,
    )

    try:
        with app.app_context():
            from app.models.bio_page import BioPage
            from app.models.profile import UserProfile
            from app.models.resume import Resume
            from app.models.user import User
            from app.services import bio_readiness as br
            from app.services import bio_service

            db.create_all()
            fake = FakeModel()
            bio_service._client = lambda: fake

            now = datetime.utcnow()

            def make_user(email, name, admin=False):
                user = User(id=generate_id(), email=email, full_name=name,
                            password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                            is_admin=admin, email_verified=True, onboarding_completed_at=now)
                db.session.add(user)
                db.session.commit()
                db.session.add(UserProfile(id=generate_id(), user_id=user.id,
                                           username=name.lower().replace(' ', ''),
                                           display_name=name))
                db.session.commit()
                return user

            def add_resume(user, text, source='upload'):
                r = Resume(id=generate_id(), user_id=user.id, label='cv', parsed_text=text,
                           source=source, file_type='pdf' if source == 'upload' else None)
                db.session.add(r)
                db.session.commit()
                return r

            def profile_of(user):
                return UserProfile.query.filter_by(user_id=user.id).first()

            client = app.test_client()

            def login(user):
                client.post('/api/auth/logout')
                res = client.post('/api/auth/login',
                                  json={'email': user.email, 'password': 'pw12345678'})
                assert res.status_code == 200, (user.email, res.status_code)

            # ── Resume text signals ──────────────────────────────────────────
            section('Resume text signals')
            good = br.assess_resume_text(GOOD_RESUME)
            check('a real resume is sufficient', good['sufficient'], good['reasons'])
            check('date ranges are counted per role', good['date_ranges'] == 3, good['date_ranges'])
            check('all four structure signals are found', good['signal_count'] == 4, good['signals'])

            letter = br.assess_resume_text(COVER_LETTER)
            check('a cover letter is not sufficient', not letter['sufficient'])
            check('a cover letter is flagged for having no dated roles',
                  any('dated roles' in r for r in letter['reasons']), letter['reasons'])
            check("a cover letter is flagged as not looking like a resume",
                  any("doesn't look like a resume" in r for r in letter['reasons']))

            blank = br.assess_resume_text('   \n  John Smith  \n ')
            check('an almost-empty parse is unreadable', blank['unreadable'] and not blank['sufficient'])
            check('unreadable gets one clear reason, not a pile',
                  len(blank['reasons']) == 1 and 'scanned' in blank['reasons'][0], blank['reasons'])

            short = br.assess_resume_text(
                'Analyst, Acme Corp 2019 - Present\nEXPERIENCE\nEducation: BA, State University\n'
                'me@example.com ' + 'did analysis work ' * 12)
            check('a readable but short resume is insufficient on length',
                  not short['sufficient'] and any('too short' in r for r in short['reasons']),
                  short['reasons'])

            for spelling in ('2019 – 2022', 'Jan 2019 - Present', '03/2018 to 06/2021',
                             'September 2015 – Current', '2012-2014'):
                check('date range recognised: {}'.format(spelling),
                      br.resume_signals('Role ' + spelling)['date_ranges'] == 1)
            check('a lone year is not a role',
                  br.resume_signals('Graduated in 2012.')['date_ranges'] == 0)

            # ── Career provenance ─────────────────────────────────────────────
            section('Career history provenance')
            stored = [{'company_name': 'Globex', 'role': 'VP', 'source': 'bio'},
                      {'company_name': 'Northwind', 'role': 'PM', 'source': 'resume'}]
            incoming = [{'company_name': 'globex ', 'role': 'vp'},           # editor dropped the tag
                        {'company_name': 'Northwind', 'role': 'PM'},
                        {'company_name': 'New Co', 'role': 'Lead'},           # typed by the user
                        {'company_name': 'X', 'role': 'Y', 'source': 'hacked'}]
            merged = br.merge_career_provenance(stored, incoming)
            check('a bio-derived entry keeps its tag through an editor save',
                  merged[0]['source'] == 'bio', merged[0])
            check('a resume-derived entry keeps its tag', merged[1]['source'] == 'resume')
            check('a newly typed entry is manual', merged[2]['source'] == 'manual')
            check('an unknown source value is not trusted', merged[3]['source'] == 'manual')
            check('tag_entries stamps the source',
                  br.tag_entries([{'company_name': 'A', 'role': 'B'}], 'bio')[0]['source'] == 'bio')

            # ── Readiness ─────────────────────────────────────────────────────
            section('Readiness')
            nobody = make_user('nobody@t.local', 'Pat Nobody')
            state = br.readiness(nobody.id)
            check('no source at all is not ready', not state['ready'] and not state['can_publish'])
            check('the reasons say what is missing',
                  any('No resume' in r for r in state['reasons']), state['reasons'])
            check('the checklist offers both routes',
                  [c['key'] for c in state['checklist']] == ['resume', 'manual'])
            check('source text never leaves the server',
                  'source_text' not in br.public(state))

            letter_user = make_user('letter@t.local', 'Casey Letter')
            add_resume(letter_user, COVER_LETTER)
            state = br.readiness(letter_user.id)
            check('a cover letter uploaded as a resume is not ready', not state['ready'])
            check("the reasons quote what's wrong with that upload",
                  any(r.startswith('Your resume:') for r in state['reasons']), state['reasons'])

            real = make_user('real@t.local', 'Jordan Rivera')
            add_resume(real, GOOD_RESUME)
            state = br.readiness(real.id)
            check('a real resume makes a user ready', state['ready'] and state['source'] == 'resume')
            check('the generator gets the resume text', 'Northwind Logistics' in state['source_text'])

            older_good = make_user('older@t.local', 'Older Good')
            add_resume(older_good, GOOD_RESUME)
            add_resume(older_good, 'blank')   # newer, unusable
            check('an older sufficient resume still counts when the newest is unusable',
                  br.readiness(older_good.id)['ready'])

            li = make_user('li@t.local', 'Linked In')
            add_resume(li, GOOD_RESUME, source='linkedin')
            check('a LinkedIn import counts as source',
                  br.readiness(li.id)['source'] == 'linkedin')

            manual = make_user('manual@t.local', 'Manual Entry')
            prof = profile_of(manual)
            prof.career_history = [
                {'company_name': 'Acme', 'role': 'Engineer', 'start_date': '2018-01',
                 'end_date': 'Present', 'source': 'manual'},
            ]
            db.session.commit()
            check('one self-entered role is below the default of two',
                  not br.readiness(manual.id)['ready'])
            prof.career_history = prof.career_history + [
                {'company_name': 'Initrode', 'role': 'Intern', 'source': 'manual'}]
            prof.education = [{'institution_name': 'State University', 'degree': 'BS',
                               'field': 'Physics', 'year': '2017'}]
            db.session.commit()
            state = br.readiness(manual.id)
            check('two self-entered roles make a user ready', state['source'] == 'manual')
            check('manual source text carries career and education',
                  'Engineer, Acme' in state['source_text']
                  and 'State University' in state['source_text'], state['source_text'])

            laundered = make_user('laundered@t.local', 'Pat Laundered')
            prof = profile_of(laundered)
            prof.bio = FABRICATED_BIO
            prof.career_history = [
                {'company_name': 'Globex Corporation', 'role': 'VP Strategy', 'source': 'bio'},
                {'company_name': 'Initech', 'role': 'Director', 'source': 'bio'}]
            db.session.commit()
            check('career entries extracted from a generated bio never count',
                  not br.readiness(laundered.id)['ready'])

            legacy = make_user('legacy@t.local', 'Pat Legacy')
            prof = profile_of(legacy)
            prof.bio = FABRICATED_BIO
            # Saved before tagging existed: no `source` key at all.
            prof.career_history = [{'company_name': 'Globex Corporation', 'role': 'VP'},
                                   {'company_name': 'Initech', 'role': 'Director'}]
            db.session.commit()
            check('untagged legacy entries that appear in the bio are treated as bio-derived',
                  not br.readiness(legacy.id)['ready'])

            legacy_ok = make_user('legacyok@t.local', 'Legacy Typed')
            prof = profile_of(legacy_ok)
            prof.bio = 'A short bio the person wrote about consulting work.'
            prof.career_history = [{'company_name': 'Umbrella', 'role': 'Analyst'},
                                   {'company_name': 'Vandelay', 'role': 'Buyer'}]
            db.session.commit()
            check('untagged legacy entries absent from the bio count as typed by the user',
                  br.readiness(legacy_ok.id)['source'] == 'manual')

            # ── Settings ───────────────────────────────────────────────────────
            section('Thresholds (platform_settings bio_ready_*)')
            check('every threshold is exposed',
                  {s['key'] for s in br.all_settings()} == set(br.SETTINGS_SPEC))
            br.save_setting('bio_ready_min_manual_roles', 3)
            check('raising the manual minimum unreadies a two-entry user',
                  not br.readiness(manual.id)['ready'])
            br.save_setting('bio_ready_min_manual_roles', 2)
            for key, bad in (('bio_ready_min_signals', 5), ('bio_ready_min_chars', -1),
                             ('bio_ready_min_roles', 'many'), ('not_a_key', 1)):
                try:
                    br.save_setting(key, bad)
                    check('{} rejects {!r}'.format(key, bad), False)
                except ValueError:
                    check('{} rejects {!r}'.format(key, bad), True)

            # ── Generation gate ───────────────────────────────────────────────
            section('Generation (/api/settings/profile/bio/generate)')
            login(nobody)
            fake.forbid = True
            res = client.post('/api/settings/profile/bio/generate', json={})
            check('no source: generation is refused with 422', res.status_code == 422, res.status_code)
            check('no source: the model is never called', fake.calls == [], fake.kinds())
            check('no source: the response carries the checklist',
                  res.get_json().get('readiness', {}).get('checklist'))
            fake.forbid = False

            login(real)
            fake.calls.clear()
            fake.bio = 'Jordan Rivera is a product manager at Northwind Logistics since 2020.'
            fake.unsupported = []
            res = client.post('/api/settings/profile/bio/generate', json={})
            check('with a resume: a bio is generated', res.status_code == 200, res.get_json())
            check('with a resume: the bio is checked against the source',
                  fake.kinds() == ['generate', 'verify'], fake.kinds())
            check('with a resume: the prompt is built from the resume',
                  'Northwind Logistics' in fake.calls[0][1])
            check('a clean bio is reported as verified',
                  res.get_json()['grounding']['status'] == 'verified')

            fake.calls.clear()
            fake.bio = ('Jordan Rivera is a product manager at Northwind Logistics. '
                        'Rivera won the 2021 Logistics Innovator Award.')
            fake.unsupported = ['Rivera won the 2021 Logistics Innovator Award']
            fake.rewrite = 'Jordan Rivera is a product manager at Northwind Logistics.'
            res = client.post('/api/settings/profile/bio/generate', json={})
            body = res.get_json()
            check('an unsupported claim triggers one rewrite',
                  fake.kinds() == ['generate', 'verify', 'rewrite'], fake.kinds())
            check('the returned bio is the corrected one',
                  body['bio'] == fake.rewrite and 'Award' not in body['bio'], body)
            check('the response says what was removed',
                  body['grounding']['status'] == 'corrected'
                  and body['grounding']['removed'] == fake.unsupported, body['grounding'])

            fake.calls.clear()
            fake.bio = 'Jordan Rivera has led product at Northwind Logistics since 1998.'
            fake.unsupported = []
            fake.rewrite = 'Jordan Rivera leads product at Northwind Logistics.'
            res = client.post('/api/settings/profile/bio/generate', json={})
            check('a year absent from the source is caught even if the verifier misses it',
                  fake.kinds() == ['generate', 'verify', 'rewrite']
                  and res.get_json()['bio'] == fake.rewrite, (fake.kinds(), res.get_json()))

            fake.calls.clear()
            fake.verifier_raises = True
            fake.bio = 'Jordan Rivera joined Northwind Logistics in 1998.'
            res = client.post('/api/settings/profile/bio/generate', json={})
            check('verifier down + an unsupported year: the bio is refused, not shipped',
                  res.status_code == 422 and 'unsupported' in res.get_json(), res.get_json())

            fake.calls.clear()
            fake.bio = 'Jordan Rivera leads product at Northwind Logistics since 2020.'
            res = client.post('/api/settings/profile/bio/generate', json={})
            check('verifier down + nothing detectably wrong: shipped and marked unverified',
                  res.status_code == 200 and res.get_json()['grounding']['status'] == 'unverified',
                  res.get_json())
            fake.verifier_raises = False

            br.save_setting('bio_ready_grounding_check', False)
            fake.calls.clear()
            client.post('/api/settings/profile/bio/generate', json={})
            check('the grounding check can be switched off', fake.kinds() == ['generate'])
            br.save_setting('bio_ready_grounding_check', True)

            fake.calls.clear()
            client.post('/api/settings/profile/bio/generate',
                        json={'existing_bio': FABRICATED_BIO})
            check('a refine treats the existing bio as a draft, not evidence',
                  'NOT evidence' in fake.calls[0][1])
            check('the verifier checks against the resume, not the existing bio',
                  'Globex' not in fake.calls[1][1].split('BIO:')[0])

            # ── Career import tagging ─────────────────────────────────────────
            section('Career import provenance')
            from app.blueprints.profile import routes as profile_routes
            profile_routes._run_career_extraction = lambda text: [
                {'company_name': 'Globex', 'role': 'VP', 'start_date': '', 'end_date': '',
                 'description': '', 'company_url': ''}]
            login(nobody)
            res = client.post('/api/settings/career/import', json={'bio': FABRICATED_BIO})
            check('entries imported from bio text are tagged bio',
                  res.get_json()['entries'][0]['source'] == 'bio', res.get_json())
            client.put('/api/settings/profile', json={'career_history': res.get_json()['entries']})
            client.put('/api/settings/profile', json={
                'career_history': [{'company_name': 'Globex', 'role': 'VP'}]})  # editor re-save
            check('the bio tag survives a re-save that drops it',
                  profile_of(nobody).career_history[0]['source'] == 'bio',
                  profile_of(nobody).career_history)
            check('so the user is still not ready', not br.readiness(nobody.id)['ready'])

            resume_user = make_user('resume@t.local', 'Resume Importer')
            r = add_resume(resume_user, GOOD_RESUME)
            login(resume_user)
            client.post('/api/settings/career/import-from-resume/' + r.id)
            check('entries imported from a resume are tagged resume',
                  profile_of(resume_user).career_history[0]['source'] == 'resume')

            # ── Publish gate ──────────────────────────────────────────────────
            section('Publish gate (/api/bio/publish)')
            login(nobody)
            res = client.post('/api/bio/publish')
            check('no source: publish is refused with 422', res.status_code == 422, res.status_code)
            check('no source: the page stays unpublished',
                  BioPage.query.filter_by(user_id=nobody.id).first().status != 'published')
            check('no source: the legacy flag is not set', not profile_of(nobody).is_published)
            res = client.put('/api/settings/visibility/publish', json={'is_published': True})
            check('no source: the legacy profile toggle is refused too',
                  res.status_code == 422 and not profile_of(nobody).is_published)
            res = client.put('/api/settings/visibility/publish', json={'is_published': False})
            check('turning the legacy toggle OFF is always allowed', res.status_code == 200)

            login(real)
            res = client.post('/api/bio/publish')
            check('with a resume: publish succeeds', res.status_code == 200, res.get_json())

            login(manual)
            check('with self-entered history: publish succeeds',
                  client.post('/api/bio/publish').status_code == 200)

            res = client.get('/api/bio/readiness')
            check('/api/bio/readiness reports the source to the owner',
                  res.status_code == 200 and res.get_json()['source'] == 'manual')
            check('/api/bio/readiness does not leak source text',
                  'source_text' not in res.get_json())

            # ── Unpublish closes the legacy page ──────────────────────────────
            section('Unpublish')
            client.post('/api/bio/unpublish')
            check('unpublishing clears the legacy publish flag', not profile_of(manual).is_published)
            client.post('/api/auth/logout')
            page = client.get('/u/' + profile_of(manual).username).get_data(as_text=True)
            prof = profile_of(manual)
            prof.bio = 'UNIQUE-BIO-MARKER text'
            db.session.commit()
            page = client.get('/u/' + prof.username).get_data(as_text=True)
            check('after unpublish a stranger cannot read the bio at /u/<slug>',
                  'UNIQUE-BIO-MARKER' not in page)

            # ── Upload ─────────────────────────────────────────────────────────
            section('Resume upload (/api/resumes/upload)')
            uploader = make_user('uploader@t.local', 'Up Loader')
            login(uploader)

            def upload(text, name):
                return client.post('/api/resumes/upload', data={
                    'file': (io.BytesIO(make_docx_bytes(text)), name),
                    'checkbox_1': 'true', 'checkbox_2': 'true',
                    'tos_version': '1.0', 'privacy_version': '1.0',
                }, content_type='multipart/form-data')

            res = upload('', 'scan.docx')
            check('an unreadable upload is refused with 422',
                  res.status_code == 422 and res.get_json().get('unreadable'), res.get_json())
            check('an unreadable upload leaves no resume row',
                  Resume.query.filter_by(user_id=uploader.id).count() == 0)
            check('an unreadable upload leaves no file on disk', not os.listdir(upload_dir))

            res = upload(COVER_LETTER, 'letter.docx')
            check('a readable non-resume is accepted (simulations can use it)',
                  res.status_code == 201, res.status_code)
            check('...but the response warns it cannot support a bio',
                  res.get_json()['bio_ready'] is False and res.get_json()['bio_warnings'])

            res = upload(GOOD_RESUME, 'cv.docx')
            check('a real resume uploads and is reported bio-ready',
                  res.status_code == 201 and res.get_json()['bio_ready'] is True, res.get_json())
            check('the uploader is now ready to publish', br.readiness(uploader.id)['ready'])

            # ── Admin review ──────────────────────────────────────────────────
            section('Admin review of bios already public')
            admin = make_user('admin@t.local', 'Root Admin', admin=True)

            # A page published before the gate existed, with nothing behind it.
            stale_bp = BioPage(id=generate_id(), user_id=legacy.id, slug=profile_of(legacy).username,
                               status='published', published_at=now)
            db.session.add(stale_bp)
            profile_of(legacy).is_published = True
            # A legacy-only profile: no BioPage, but the old flag is on.
            legacy_only = make_user('legacyonly@t.local', 'Legacy Only')
            profile_of(legacy_only).is_published = True
            profile_of(legacy_only).bio = 'Made up.'
            db.session.commit()

            login(nobody)
            check('a non-admin cannot read the queue',
                  client.get('/api/admin/bio-review').status_code == 403)
            check('a non-admin cannot keep a page live',
                  client.post('/api/admin/bio-review/{}/keep'.format(legacy.id)).status_code == 403)

            login(admin)
            queue = client.get('/api/admin/bio-review').get_json()
            flagged = {r['user_id']: r for r in queue['flagged']}
            check('a thin published bio page is flagged', legacy.id in flagged, list(flagged))
            check('a legacy-only public profile is flagged too',
                  legacy_only.id in flagged and flagged[legacy_only.id]['via'] == 'legacy profile page')
            check('a page backed by a resume is not flagged', real.id not in flagged)
            check('flagging does not take anything down',
                  BioPage.query.get(stale_bp.id).status == 'published')

            login(legacy)
            body = client.get('/u/' + profile_of(legacy).username).get_data(as_text=True)
            check("the owner sees a notice on their live page",
                  'bio-readiness-banner' in body and 'is live, but' in body)
            body = client.get('/u/{}/edit'.format(profile_of(legacy).username)).get_data(as_text=True)
            check('the owner sees the notice in the editor', 'bio-readiness-banner' in body)

            login(admin)
            res = client.post('/api/admin/bio-review/{}/keep'.format(legacy.id),
                              json={'note': 'Checked against their LinkedIn by hand.'})
            check('an admin can keep a page live', res.status_code == 200, res.get_json())
            queue = client.get('/api/admin/bio-review').get_json()
            check('a kept page leaves the review list',
                  legacy.id not in {r['user_id'] for r in queue['flagged']}
                  and legacy.id in {r['user_id'] for r in queue['kept']})
            check('the keep note is recorded',
                  queue['kept'][0]['override']['note'].startswith('Checked'), queue['kept'])
            state = br.readiness(legacy.id)
            check('a kept page can be republished but still cannot generate',
                  state['can_publish'] and not state['ready'])
            login(legacy)
            body = client.get('/u/' + profile_of(legacy).username).get_data(as_text=True)
            check('the owner no longer sees the notice once kept', 'bio-readiness-banner' not in body)

            login(admin)
            client.delete('/api/admin/bio-review/{}/override'.format(legacy.id))
            check('clearing the override returns it to review',
                  legacy.id in {r['user_id'] for r in
                                client.get('/api/admin/bio-review').get_json()['flagged']})

            res = client.post('/api/admin/bio-review/{}/unpublish'.format(legacy.id))
            check('an admin can unpublish a flagged page', res.status_code == 200)
            check('admin unpublish takes the bio page down',
                  BioPage.query.get(stale_bp.id).status == 'unpublished')
            check('admin unpublish clears the legacy flag too', not profile_of(legacy).is_published)
            client.post('/api/admin/bio-review/{}/unpublish'.format(legacy_only.id))
            check('admin unpublish works on a legacy-only profile',
                  not profile_of(legacy_only).is_published)
            check('the queue is empty after both decisions',
                  client.get('/api/admin/bio-review').get_json()['flagged'] == [])

            login(legacy)
            check('the owner cannot republish an unpublished thin page',
                  client.post('/api/bio/publish').status_code == 422)

            login(admin)
            res = client.put('/api/admin/bio-review/settings',
                             json={'settings': {'bio_ready_min_chars': 900}})
            check('an admin can tune a threshold',
                  res.status_code == 200 and br.setting('bio_ready_min_chars') == 900)
            res = client.put('/api/admin/bio-review/settings',
                             json={'settings': {'bio_ready_min_signals': 9}})
            check('a bad threshold is refused with a readable error',
                  res.status_code == 400 and 'out of four' in res.get_json()['error'])
            br.save_setting('bio_ready_min_chars', 600)

            check('the review page renders for an admin', client.get('/admin/bio-review').status_code == 200)
            login(nobody)
            check('the review page is closed to everyone else',
                  client.get('/admin/bio-review').status_code == 403)

            # ── Templates ───────────────────────────────────────────────────
            section('Templates')
            for name in ('admin/bio_review.html', 'public/_bio_readiness_banner.html',
                         'public/bio_editor.html', 'public/bio_page.html',
                         'onboarding/wizard.html', 'settings/index.html', 'admin/index.html'):
                try:
                    app.jinja_env.get_template(name)
                    check('compiles: ' + name, True)
                except Exception as exc:
                    check('compiles: ' + name, False, exc)
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        print('failed: ' + ', '.join(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
