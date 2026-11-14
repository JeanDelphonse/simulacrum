"""Founder Track intake and pitch (FOUNDER_PLAN.md WP1).

Real tiny PDF/DOCX uploads generated in-test, wrong types and scanned files
refused, per-kind versioning, member-only access, the flag-off 404, the spoken
pitch with a faked Claude (follow-up, restatement, confirm -> transcript
artifact, "yes" confirms, crisis language stops before the model), and the
founder_pitch switch. In-memory SQLite; requests is blocked.

    python test_founder_intake.py
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = [
    'app/services/founder_intake_service.py',
    'app/services/founder_pitch_service.py',
    'app/blueprints/founder/intake.py',
]

PLAN_TEXT = ('Acme Robotics startup plan. Problem: small warehouses lose hours every day to manual picking. '
             'Customer: owners of warehouses with 5 to 50 staff. Solution: a rentable picking robot that '
             'learns the shelf layout in a day. Business model: monthly rental per robot. Market: regional '
             'third-party logistics firms. Team: two founders with robotics and operations backgrounds.')


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def make_pdf(text):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def make_docx(text):
    from docx import Document
    d = Document()
    for para in text.split('. '):
        d.add_paragraph(para)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


class FakeClaude:
    """Returns queued pitch_turn tool inputs; records every call."""

    def __init__(self):
        self.queue, self.calls = [], []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        out = self.queue.pop(0) if self.queue else {'say': 'Tell me more.', 'ready_to_confirm': False, 'missing': []}
        block = SimpleNamespace(type='tool_use', name='pitch_turn', input=out)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=[block], usage=usage)


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))

    print('\n1. New files are Python 3.9 safe; guide_service untouched')
    missing = [f for f in NEW_PY
               if 'from __future__ import annotations' not in io.open(os.path.join(ROOT, f), encoding='utf-8').read()]
    check('every new .py file has "from __future__ import annotations"', not missing, missing)
    try:
        diff = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'app/services/guide_service.py',
                               'app/static/js/guide_console.js'], cwd=ROOT).returncode
    except Exception:
        diff = 0
    check('guide_service.py and guide_console.js are unchanged', diff == 0)

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    tmp_root = tempfile.mkdtemp(prefix='fnd_intake_')
    upload_root = os.path.join(tmp_root, 'uploads')       # the public /avatars root
    files_root = os.path.join(tmp_root, 'founder_private')
    os.makedirs(upload_root)
    from cryptography.fernet import Fernet
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False, UPLOAD_FOLDER=upload_root,
                      ENCRYPTION_KEY=Fernet.generate_key().decode())

    try:
        with app.app_context():
            db.create_all()
            from flask import g
            from app.models.founder import VentureArtifact, VentureMember
            from app.models.user import User
            from app.services import founder_feature, founder_intake_service as fis, founder_pitch_service as fps
            now = datetime.utcnow()

            def user(email, admin=False):
                u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x',
                         is_admin=admin, email_verified=True, onboarding_completed_at=now)
                db.session.add(u)
                db.session.commit()
                return u

            F_ID, A_ID, O_ID, C_ID = (user('founder@x.com').id, user('admin@x.com', admin=True).id,
                                      user('other@x.com').id, user('cofounder@x.com').id)

            client = app.test_client()
            for verb in ('get', 'post'):
                orig = getattr(client, verb)
                setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

            def login(uid):
                with client.session_transaction() as s:
                    if uid:
                        s['_user_id'], s['_fresh'] = uid, True
                    else:
                        s.pop('_user_id', None)

            def upload(vid, kind, data, name):
                return client.post('/api/founder/v/%s/artifacts' % vid, data={'kind': kind, 'file': (io.BytesIO(data), name)},
                                   content_type='multipart/form-data')

            print('\n2. Track off: every intake route 404s')
            login(F_ID)
            for path in ('/founder/start', '/founder/v/NOPE00000/intake', '/api/founder/v/NOPE00000/artifacts'):
                check('GET %s is 404 while off' % path, client.get(path).status_code == 404)
            check('POST /api/founder/ventures is 404 while off',
                  client.post('/api/founder/ventures', json={'name': 'Acme'}).status_code == 404)

            founder_feature.set_mode('founder_track', 'on', A_ID)

            print('\n3. Venture creation')
            r = client.get('/founder/start')
            body = r.get_data(as_text=True)
            check('/founder/start shows the create form', r.status_code == 200 and 'Create venture' in body
                  and 'for="fi-name"' in body, r.status_code)
            r = client.post('/api/founder/ventures', json={'name': '  ', 'stage': 'idea'})
            check('a blank name is refused', r.status_code == 400 and 'name' in r.get_json()['error'].lower())
            r = client.post('/api/founder/ventures', json={'name': 'Acme Robotics', 'one_liner': 'Picking robots',
                                                           'stage': 'pre-seed'})
            check('create returns 201 with the intake url', r.status_code == 201 and '/intake' in r.get_json()['url'])
            VID = r.get_json()['venture_id']
            check('a second venture is refused', client.post('/api/founder/ventures', json={'name': 'Two'}).status_code == 400)
            r = client.get('/founder/start')
            check('/founder/start redirects once you have a venture', r.status_code == 302
                  and r.headers['Location'].endswith('/founder/v/%s/intake' % VID))

            print('\n4. Intake page: gaps, not zeros')
            r = client.get('/founder/v/%s/intake' % VID)
            body = r.get_data(as_text=True)
            check('the intake page renders and extends base.html', r.status_code == 200 and 'ss-nav' in body)
            check('every missing kind shows as a gap', body.count('Not provided yet') == 6, body.count('Not provided yet'))
            check('no zero scores or "0" versions for missing kinds', 'Version 0' not in body)
            check('the pitch panel and evaluate button are there', 'Talk it through' in body and 'Run evaluation' in body
                  and 'founder_pitch.js' in body)
            from app.services import legal_text
            check('the page carries the report disclaimer', legal_text.get('founder_report_disclaimer')[:40] in body)

            print('\n5. Uploads')
            r = upload(VID, 'startup_plan', make_pdf(PLAN_TEXT), 'Acme Plan.pdf')
            d = r.get_json() or {}
            check('a real PDF is accepted as version 1', r.status_code == 201 and d['artifact']['version'] == 1,
                  (r.status_code, d))
            a1 = VentureArtifact.query.filter_by(venture_id=VID, kind='startup_plan').first()
            check('its text was extracted', a1 is not None and 'picking robot' in (a1.text_content or ''))
            check('the default files root is founder_private beside UPLOAD_FOLDER', os.path.normpath(fis.files_root())
                  == os.path.normpath(files_root), fis.files_root())
            check('the file is stored under <files root>/<venture_id>/, outside the public UPLOAD_FOLDER',
                  a1.file_path and os.path.normpath(os.path.join(files_root, VID)) ==
                  os.path.normpath(os.path.dirname(a1.file_path)) and os.path.exists(a1.file_path)
                  and not os.path.exists(os.path.join(upload_root, 'founder')), a1.file_path)
            check('/avatars/ cannot serve a founder file',
                  client.get('/avatars/founder/%s/%s' % (VID, os.path.basename(a1.file_path))).status_code == 404
                  and client.get('/avatars/../founder_private/%s/%s' % (VID, os.path.basename(a1.file_path))).status_code
                  == 404)
            app.config['FOUNDER_FILES_FOLDER'] = os.path.join(upload_root, 'sneaky')
            check('a FOUNDER_FILES_FOLDER inside UPLOAD_FOLDER is refused (falls back)',
                  os.path.normpath(fis.files_root()) == os.path.normpath(files_root), fis.files_root())
            app.config.pop('FOUNDER_FILES_FOLDER')
            check('the stored name is secure_filename-ed', a1.filename == 'Acme_Plan.pdf', a1.filename)
            r = upload(VID, 'startup_plan', make_docx(PLAN_TEXT + ' Version two adds pricing.'), 'plan v2.docx')
            check('a real DOCX is accepted as version 2', r.status_code == 201 and r.get_json()['artifact']['version'] == 2)
            rows = VentureArtifact.query.filter_by(venture_id=VID, kind='startup_plan').order_by(VentureArtifact.version).all()
            check('versioning: v1 kept, only v2 is current', [(x.version, x.is_current) for x in rows] == [(1, False), (2, True)],
                  [(x.version, x.is_current) for x in rows])
            check('v2 text came from the DOCX', 'Version two adds pricing' in rows[1].text_content)
            r = upload(VID, 'pitch_deck', b'hello there, plain text', 'deck.txt')
            check('a .txt upload is refused', r.status_code == 400 and 'PDF' in r.get_json()['error'])
            r = upload(VID, 'pitch_deck', make_pdf(PLAN_TEXT), 'deck.pdf.exe')
            check('a disguised extension is refused', r.status_code == 400)
            r = upload(VID, 'pitch_deck', b'%PDF-1.4 not really a pdf', 'deck.pdf')
            check('a corrupt PDF is refused with 422', r.status_code == 422, r.status_code)
            r = upload(VID, 'pitch_deck', make_docx(PLAN_TEXT), 'deck.pdf')
            check('a DOCX renamed .pdf is refused by content (422)', r.status_code == 422
                  and 'PDF' in r.get_json()['error'], r.status_code)
            r = upload(VID, 'pitch_deck', make_pdf(PLAN_TEXT), 'deck.docx')
            check('a PDF renamed .docx is refused by content (422)', r.status_code == 422, r.status_code)
            import zipfile
            zbuf = io.BytesIO()
            with zipfile.ZipFile(zbuf, 'w') as zf:
                zf.writestr('word/other.xml', 'x')
            r = upload(VID, 'pitch_deck', zbuf.getvalue(), 'deck.docx')
            check('a zip without word/document.xml is refused (422)', r.status_code == 422, r.status_code)
            saved_unz = fis.DOCX_UNPACKED_CAP_BYTES
            fis.DOCX_UNPACKED_CAP_BYTES = 1000
            try:
                r = upload(VID, 'pitch_deck', make_docx(PLAN_TEXT * 10), 'big.docx')
            finally:
                fis.DOCX_UNPACKED_CAP_BYTES = saved_unz
            check('a DOCX that unpacks past the cap is refused before parsing (422)', r.status_code == 422, r.status_code)
            r = upload(VID, 'pitch_deck', make_pdf(''), 'scan.pdf')
            check('a scanned/empty PDF is refused like resume uploads', r.status_code == 422
                  and 'scanned' in r.get_json()['error'], r.get_data(as_text=True)[:120])
            r = upload(VID, 'pitch_transcript', make_pdf(PLAN_TEXT), 'x.pdf')
            check('pitch_transcript cannot be uploaded (it comes from the pitch)', r.status_code == 400)
            r = upload(VID, 'nonsense', make_pdf(PLAN_TEXT), 'x.pdf')
            check('an unknown kind is refused', r.status_code == 400)
            saved_cap = fis.UPLOAD_CAP_BYTES
            fis.UPLOAD_CAP_BYTES = 100
            try:
                r = upload(VID, 'pitch_deck', make_pdf(PLAN_TEXT), 'big.pdf')
            finally:
                fis.UPLOAD_CAP_BYTES = saved_cap
            check('the per-route size cap answers 413', r.status_code == 413, r.status_code)
            check('refused uploads leave no file or row behind',
                  VentureArtifact.query.filter_by(venture_id=VID, kind='pitch_deck').count() == 0
                  and len(os.listdir(os.path.join(files_root, VID))) == 2,
                  os.listdir(os.path.join(files_root, VID)))
            check('the cap is 20 MB', fis.max_upload_bytes() == 20 * 1024 * 1024)
            r = client.get('/api/founder/v/%s/artifacts' % VID)
            kinds = {k['kind']: k for k in r.get_json()['kinds']}
            check('list: current version per kind, missing kinds are null not zero',
                  kinds['startup_plan']['current']['version'] == 2 and kinds['startup_plan']['versions'] == 2
                  and kinds['pitch_deck']['current'] is None and len(r.get_json()['history']) == 2)
            body = client.get('/founder/v/%s/intake' % VID).get_data(as_text=True)
            check('the page shows the current version with its date', 'Version 2 · %s' % now.strftime('%Y-%m-%d') in body)

            print('\n6. Member access only')
            login(O_ID)
            check('a non-member gets 403 on the page', client.get('/founder/v/%s/intake' % VID).status_code == 403)
            check('a non-member gets 403 on the list', client.get('/api/founder/v/%s/artifacts' % VID).status_code == 403)
            check('a non-member cannot upload', upload(VID, 'pitch_deck', make_pdf(PLAN_TEXT), 'd.pdf').status_code == 403)
            check('a non-member cannot pitch',
                  client.post('/api/founder/v/%s/pitch/start' % VID, json={}).status_code == 403)
            check('an unknown venture is 404', client.get('/founder/v/NOPE00000/intake').status_code == 404)
            db.session.add(VentureMember(venture_id=VID, user_id=C_ID, role='cofounder', status='active', joined_at=now))
            db.session.commit()
            login(C_ID)
            r = upload(VID, 'exec_summary', make_pdf(PLAN_TEXT), 'summary.pdf')
            check('an active co-founder can upload', r.status_code == 201)
            check('... and the row records who uploaded it',
                  VentureArtifact.query.filter_by(venture_id=VID, kind='exec_summary').first().uploaded_by == C_ID)
            login(None)
            check('anonymous gets 404', client.get('/founder/v/%s/intake' % VID).status_code == 404)

            print('\n7. Pitch with a faked Claude')
            login(F_ID)
            fake = FakeClaude()
            fps.AI_CLIENT = fake
            from app.models.platform_settings import PlatformSetting
            from app.models.audit_log import AuditLog
            PlatformSetting.set('guide_sessions_per_day', 50)
            db.session.commit()
            r = client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            check('start opens with a question and no model call', r.status_code == 200
                  and '?' in r.get_json()['turn']['say'] and not fake.calls)
            fake.queue.append({'say': 'Who has this problem most?', 'ready_to_confirm': False,
                               'missing': ['customer', 'business_model', 'bogus']})
            r = client.post('/api/founder/v/%s/pitch/turn' % VID,
                            json={'text': 'We build picking robots for small warehouses.', 'input_mode': 'voice'})
            t = r.get_json()['turn']
            check('a turn returns the follow-up question', r.status_code == 200 and t['say'] == 'Who has this problem most?')
            drafts = [os.path.join(files_root, VID, f) for f in os.listdir(os.path.join(files_root, VID))
                      if f.startswith('pitch_draft_')]
            raw = io.open(drafts[0], encoding='utf-8').read() if drafts else ''
            check('the in-progress draft is stored encrypted on disk', raw.startswith('enc')
                  and 'picking robots' not in raw, raw[:3])
            check('missing elements are filtered to the known list', t['missing'] == ['customer', 'business_model'])
            call = fake.calls[-1]
            check('the call uses the pitch_turn tool, forced', call['tools'][0]['name'] == 'pitch_turn'
                  and call['tool_choice'] == {'type': 'tool', 'name': 'pitch_turn'})
            check('the model comes from model_router fnd_pitch (Sonnet fallback)', 'sonnet' in call['model'], call['model'])
            check('the conversation reaches the model as alternating messages',
                  call['messages'][0]['role'] == 'user' and call['messages'][-1]['content'].startswith('We build'))
            check('no unconfirmed transcript is saved yet',
                  VentureArtifact.query.filter_by(venture_id=VID, kind='pitch_transcript').count() == 0)
            r = client.post('/api/founder/v/%s/pitch/confirm' % VID, json={})
            check('confirm before a restatement is refused', r.status_code == 409)
            r = client.post('/api/founder/v/%s/pitch/confirm' % VID, json={'restatement': 'Injected text, never restated.'})
            check('a client-supplied restatement before SimiSI restated is refused', r.status_code == 409
                  and VentureArtifact.query.filter_by(venture_id=VID, kind='pitch_transcript').count() == 0)
            ledger = AuditLog.query.filter_by(user_id=F_ID, action='founder_pitch_turn').all()
            check('every model turn is recorded with its cost in the ledger', len(ledger) == 1
                  and ledger[0].extra.get('cost_cents', 0) > 0, [x.extra for x in ledger])
            fake.queue.append({'say': 'Ready, no restatement', 'ready_to_confirm': True, 'missing': []})
            fake.queue.append({'say': 'Here is what I heard. Did I get it right?', 'ready_to_confirm': True,
                               'restatement': 'Acme rents picking robots to small warehouse owners, monthly.',
                               'missing': ['team']})
            n0 = len(fake.calls)
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'Warehouse owners, monthly rental.'})
            t = r.get_json()['turn']
            check('"ready" without a restatement is regenerated', len(fake.calls) - n0 == 2)
            check('the restatement is offered for confirmation', t['ready_to_confirm'] and 'rents picking robots' in t['restatement'])
            body = client.get('/founder/v/%s/intake' % VID).get_data(as_text=True)
            check('reloading the page restores the pending restatement', 'rents picking robots' in body
                  and 'Warehouse owners, monthly rental.' in body)
            r = client.post('/api/founder/v/%s/pitch/confirm' % VID,
                            json={'restatement': 'Acme rents picking robots to warehouses with 5-50 staff, monthly.'})
            check('confirm creates a pitch_transcript artifact', r.status_code == 201
                  and r.get_json()['artifact']['kind'] == 'pitch_transcript', r.get_data(as_text=True)[:200])
            tr = VentureArtifact.query.filter_by(venture_id=VID, kind='pitch_transcript', is_current=True).first()
            check('the transcript holds the edited restatement and the conversation',
                  tr and 'warehouses with 5-50 staff' in tr.text_content and 'Founder: We build picking robots'
                  in tr.text_content and 'SimiSI: Who has this problem most?' in tr.text_content)
            check("SimiSI's restatement and the founder's edits are separate, labelled sections",
                  tr and 'SimiSI restatement\nAcme rents picking robots to small warehouse owners, monthly.' in tr.text_content
                  and 'Founder edits (the version the founder confirmed)\nAcme rents picking robots to warehouses'
                  in tr.text_content, tr.text_content[:400] if tr else None)
            check('the draft is cleared after confirm', fps.state(db.session.get(
                __import__('app.models.founder', fromlist=['FounderVenture']).FounderVenture, VID),
                db.session.get(User, F_ID))['active'] is False)
            draft_files = [f for f in os.listdir(os.path.join(files_root, VID)) if f.startswith('pitch_draft')]
            check('no draft file is left on disk', not draft_files, draft_files)

            print('\n8. A spoken "yes" confirms; crisis language stops before the model')
            client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            fake.queue.append({'say': 'Did I get it right?', 'ready_to_confirm': True,
                               'restatement': 'Acme rents robots. Version two.', 'missing': []})
            client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'Robots for rent.'})
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': "Yes, that's right.", 'input_mode': 'voice'})
            t = r.get_json()['turn']
            check('"yes" to a pending restatement confirms it', t.get('confirmed') is True and t['artifact']['version'] == 2)
            check('transcript versions: v1 kept, v2 current', [(x.version, x.is_current) for x in VentureArtifact.query.filter_by(
                venture_id=VID, kind='pitch_transcript').order_by(VentureArtifact.version)] == [(1, False), (2, True)])
            tr2 = VentureArtifact.query.filter_by(venture_id=VID, kind='pitch_transcript', is_current=True).first()
            check('an unedited confirm is labelled as SimiSI restatement confirmed without changes',
                  'SimiSI restatement, confirmed by the founder without changes' in tr2.text_content
                  and 'Founder edits' not in tr2.text_content)
            from app.models.founder import FounderVenture

            def cur_state():
                return fps.state(db.session.get(FounderVenture, VID), db.session.get(User, F_ID))

            print('\n8b. A withdrawn restatement is not confirmed by a later "yes"')
            client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            fake.queue.append({'say': 'Did I get it right?', 'ready_to_confirm': True,
                               'restatement': 'Old restatement, uncorrected.', 'missing': []})
            client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'Robots for rent.'})
            fake.queue.append({'say': 'Got it. Do customers sign a yearly contract?', 'ready_to_confirm': False,
                               'missing': ['business_model']})
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'No, it is for warehouses only.'})
            check('a turn without a restatement withdraws the offer', r.get_json()['turn']['ready_to_confirm'] is False)
            n0 = len(fake.calls)
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'Yes.'})
            t = r.get_json()['turn']
            check('"yes" then answers the question; nothing is confirmed', not t.get('confirmed')
                  and len(fake.calls) == n0 + 1 and VentureArtifact.query.filter_by(
                      venture_id=VID, kind='pitch_transcript').count() == 2)
            r = client.post('/api/founder/v/%s/pitch/confirm' % VID, json={'restatement': 'Old restatement, uncorrected.'})
            check('confirm is refused while no restatement is on offer', r.status_code == 409)
            body = client.get('/founder/v/%s/intake' % VID).get_data(as_text=True)
            check('the page no longer shows the withdrawn restatement', 'Old restatement, uncorrected.' not in body)

            print('\n8c. Crisis language pauses the pitch')
            client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            fake.queue.append({'say': 'Did I get it right?', 'ready_to_confirm': True,
                               'restatement': 'Acme rents robots.', 'missing': []})
            client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'Robots for rent.'})
            n0 = len(fake.calls)
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': "Honestly I just want to die some days."})
            t = r.get_json()['turn']
            check('crisis language returns the crisis text and 988', t.get('crisis') and '988' in t['say']
                  and any(x['href'] == 'tel:988' for x in t['resources']))
            check("... in pitch wording, not the guide's goal-setting wording", 'pause the pitch' in t['say']
                  and 'goal-setting' not in t['say'])
            check('... with no model call', len(fake.calls) == n0)
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'Anyway, robots.'})
            check('after a crisis, further turns are refused until a fresh start', r.status_code == 409
                  and 'Start' in r.get_json()['error'] and len(fake.calls) == n0)
            r = client.post('/api/founder/v/%s/pitch/confirm' % VID, json={})
            check('after a crisis, confirm is refused', r.status_code == 409)
            st = cur_state()
            check("the founder's crisis words are not kept in the draft",
                  not any('want to die' in x['text'] for x in st['turns']) and st['crisis'] and not st['ready_to_confirm'])
            body = client.get('/founder/v/%s/intake' % VID).get_data(as_text=True)
            check('reloading shows the crisis reply styled, with tappable 988 and 911 links',
                  'fi-turn crisis' in body and 'href="tel:988"' in body and 'href="tel:911"' in body
                  and '>Start</button>' in body)
            client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            fake.queue.append({'say': 'ok', 'ready_to_confirm': False, 'missing': [], 'wellbeing': 'crisis'})
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'Things are hard.'})
            check('a model-flagged crisis also returns the crisis text', r.get_json()['turn'].get('crisis') is True)
            st = cur_state()
            check('... and the flagged message is not kept', not any(x['text'] == 'Things are hard.' for x in st['turns']))
            check('crisis turns never reach the model history', all(
                fps.CRISIS_TEXT not in m['content'] for m in fps._messages({'turns': st['turns']})))
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': '   '})
            check('an empty turn is refused', r.status_code == 409)

            print('\n8d. Unreadable drafts and concurrent turns')
            v8, u8 = db.session.get(FounderVenture, VID), db.session.get(User, F_ID)
            client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            with io.open(fps._draft_path(v8, u8), 'w', encoding='utf-8') as fh:
                fh.write('enc\nnot-a-valid-token')
            n0 = len(fake.calls)
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'We sell robots.'})
            check('an unreadable draft is reported, not silently replaced', r.status_code == 409
                  and "couldn't be opened" in r.get_json()['error'] and len(fake.calls) == n0)
            check('... and the draft file is left alone', io.open(fps._draft_path(v8, u8), encoding='utf-8').read()
                  == 'enc\nnot-a-valid-token')
            body = client.get('/founder/v/%s/intake' % VID).get_data(as_text=True)
            check('the page tells the founder and offers Start', "couldn't be opened" in body and '>Start</button>' in body)
            client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            lock = fps._draft_path(v8, u8) + '.lock'
            io.open(lock, 'w').close()
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'We sell robots.'})
            check('a turn while another holds the draft lock is refused', r.status_code == 409
                  and 'still answering' in r.get_json()['error'])
            os.utime(lock, (0, 0))
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'We sell robots.'})
            check('a stale lock is taken over', r.status_code == 200 and not os.path.exists(lock))
            before = io.open(fps._draft_path(v8, u8), encoding='utf-8').read()
            old_key = app.config['ENCRYPTION_KEY']
            app.config['ENCRYPTION_KEY'], app.testing = None, False
            try:
                r = client.post('/api/founder/v/%s/pitch/start' % VID, json={})
                refused_plain = r.status_code == 409
                raw = io.open(fps._draft_path(v8, u8), encoding='utf-8').read()
            finally:
                app.config['ENCRYPTION_KEY'], app.testing = old_key, True
            check('outside debug/testing the draft is never written as plain text', refused_plain
                  and raw == before and not raw.startswith('plain'), raw[:5])

            print('\n8e. Daily and monthly pitch limits are durable')
            started = AuditLog.query.filter_by(user_id=F_ID, action='founder_pitch_started').count()
            PlatformSetting.set('guide_sessions_per_day', started)
            db.session.commit()
            r = client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            check("starting over past today's limit is refused", r.status_code == 409 and 'today' in r.get_json()['error'])
            PlatformSetting.set('guide_sessions_per_day', 50)
            db.session.commit()
            client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            spent = sum(x.extra.get('cost_cents', 0) for x in AuditLog.query.filter_by(user_id=F_ID, action='founder_pitch_turn'))
            PlatformSetting.set('guide_cost_cap_month_cents', max(0, int(spent) - 1))
            db.session.commit()
            n0 = len(fake.calls)
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'We sell robots.'})
            check("a fresh conversation can't spend past the monthly cap", r.status_code == 409
                  and 'month' in r.get_json()['error'] and len(fake.calls) == n0, r.get_data(as_text=True)[:160])
            PlatformSetting.set('guide_cost_cap_month_cents', 100000)
            db.session.commit()

            print('\n9. Model failure falls back safely')
            client.post('/api/founder/v/%s/pitch/start' % VID, json={})
            boom = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(RuntimeError('x'))))
            fps.AI_CLIENT = boom
            r = client.post('/api/founder/v/%s/pitch/turn' % VID, json={'text': 'We sell robots.'})
            check('a failing model gives the safe fallback, not a 500', r.status_code == 200
                  and r.get_json()['turn'].get('fallback') is True)
            fps.AI_CLIENT = fake

            print('\n10. founder_pitch switch off')
            founder_feature.set_mode('founder_pitch', 'off', A_ID)
            for ep in ('start', 'turn', 'confirm'):
                rr = client.post('/api/founder/v/%s/pitch/%s' % (VID, ep), json={'text': 'hi'})
                check('POST pitch/%s is 404 with the pitch switch off' % ep, rr.status_code == 404)
            body = client.get('/founder/v/%s/intake' % VID).get_data(as_text=True)
            check('the page hides the pitch panel but keeps uploads',
                  'Talk it through' not in body and 'Your documents' in body)
            v = db.session.get(FounderVenture, VID)
            u = db.session.get(User, F_ID)
            refused = False
            try:
                fps.turn(v, u, 'hello')
            except fps.PitchError:
                refused = True
            check('the service itself refuses while the switch is off', refused)
            founder_feature.set_mode('founder_pitch', 'on', A_ID)

            print('\n11. Track off again: everything 404s')
            founder_feature.set_mode('founder_track', 'off', A_ID)
            check('intake page 404', client.get('/founder/v/%s/intake' % VID).status_code == 404)
            check('upload 404', upload(VID, 'pitch_deck', make_pdf(PLAN_TEXT), 'd.pdf').status_code == 404)
            check('pitch 404', client.post('/api/founder/v/%s/pitch/start' % VID, json={}).status_code == 404)
            login(A_ID)
            check('admin also 404 while off', client.get('/founder/v/%s/intake' % VID).status_code == 404)
            fps.AI_CLIENT = None
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
