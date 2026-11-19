"""Founder Track concierge onboarding (FOUNDER_CONCIERGE_PLAN.md steps 1-6).

Schema and NULL-owner ventures, settings, the state machine and its audit log,
the per-admin permission, encrypted-at-rest uploads with structural content
checks, the /admin/ventures area (queue, create, upload, fee override), the
evaluation run with the pre-fill extraction (faked Claude), edit history and
confirm. In-memory SQLite; requests is blocked.

    python test_founder_concierge.py
"""
import glob
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import zipfile
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = ['app/services/concierge_service.py', 'app/blueprints/founder/concierge_admin.py']

PLAN_TEXT = ('Acme Robotics startup plan. Problem: small warehouses lose hours every day to manual picking. '
             'Customer: owners of warehouses with 5 to 50 staff. Solution: a rentable picking robot that '
             'learns the shelf layout in a day. Business model: monthly rental per robot. Market: regional '
             'third-party logistics firms. Team: Jane Roe, CEO, robotics engineer; Sam Poe, COO, operations.')


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def make_pdf(text, **save):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 560, 800), text, fontsize=10)
    if save.pop('embed', False):
        doc.embfile_add('secret.txt', b'hidden payload')
    data = doc.tobytes(**save)
    doc.close()
    return data


def make_docx(text, extra_parts=None):
    from docx import Document
    d = Document()
    for para in text.split('. '):
        d.add_paragraph(para)
    buf = io.BytesIO()
    d.save(buf)
    if not extra_parts:
        return buf.getvalue()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as src, zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        for name, body in extra_parts.items():
            dst.writestr(name, body)
    return out.getvalue()


def js_pdf(text):
    """A PDF whose catalog runs JavaScript on open."""
    import fitz
    doc = fitz.open(stream=make_pdf(text), filetype='pdf')
    js = doc.get_new_xref()
    doc.update_object(js, '<< /Type /Action /S /JavaScript /JS (app.alert\\(1\\)) >>')
    doc.xref_set_key(doc.pdf_catalog(), 'OpenAction', '%d 0 R' % js)
    data = doc.tobytes(garbage=0, deflate=True, use_objstms=1) if hasattr(doc, 'tobytes') else doc.write()
    doc.close()
    return data


def upload(data, name):
    from werkzeug.datastructures import FileStorage
    return FileStorage(stream=io.BytesIO(data), filename=name)


class FakeClaude(object):
    """evaluate_dimension: every dimension covered, one finding quoting the
    document. extract_venture_facts (the pre-fill): name, one-liner and team
    with quotes, stage not found."""

    def __init__(self):
        self.calls, self.lock, self.fail = [], threading.Lock(), False
        self.messages = self
        self.prefill = None

    def create(self, **kw):
        if self.fail:
            raise RuntimeError('api down')
        tool = (kw.get('tools') or [{}])[0].get('name')
        with self.lock:
            self.calls.append(tool)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if tool == 'evaluate_dimension':
            first = kw['messages'][0]['content']
            dim = re.search(r'dimension "([a-z_]+)"', first[1]['text']).group(1)
            out = {'covered': True, 'score': 60, 'findings': []}
            if dim == 'team':
                out['findings'] = [{'ftype': 'gap', 'severity': 'high', 'title': 'No one sells',
                                    'detail': 'Nobody on the team owns sales.',
                                    'citation_text': 'robotics engineer', 'confidence': 0.8}]
            if dim == 'problem':
                out['findings'] = [{'ftype': 'strength', 'severity': 'low', 'title': 'Pain is concrete',
                                    'detail': 'Warehouses lose hours.',
                                    'citation_text': 'small warehouses lose hours every day', 'confidence': 0.7}]
            block = SimpleNamespace(type='tool_use', id='tu', name=tool, input=out)
            return SimpleNamespace(content=[block], usage=usage, model='fake-sonnet')
        doc = kw['messages'][0]['content'] if isinstance(kw['messages'][0]['content'], str) else \
            ' '.join(b.get('text', '') for b in kw['messages'][0]['content'])
        out = self.prefill or {
            'name': {'value': 'Acme Robotics', 'citation': 'Acme Robotics startup plan'},
            'one_liner': {'value': 'Rentable picking robots for small warehouses',
                          'citation': 'a rentable picking robot that learns the shelf layout in a day'},
            'stage': {'value': None, 'citation': None},
            'team': [{'name': 'Jane Roe', 'role': 'CEO', 'citation': 'Jane Roe, CEO'},
                     {'name': 'Ghost Person', 'role': 'CTO', 'citation': 'Ghost Person, CTO'}],
        }
        self.last_doc = doc
        block = SimpleNamespace(type='tool_use', id='tu', name=tool, input=out)
        return SimpleNamespace(content=[block], usage=usage, model='fake-haiku')


def sql_columns(path):
    """{table: {column: not_null}} from the CREATE TABLE statements in a migration."""
    out = {}
    txt = io.open(path, encoding='utf-8').read()
    for m in re.finditer(r'CREATE TABLE (\w+) \((.*?)\n\) ENGINE', txt, re.S):
        cols = {}
        for line in m.group(2).splitlines():
            line = line.strip()
            mm = re.match(r'(\w+)\s+([A-Z]+)', line)
            if not mm or mm.group(1) in ('UNIQUE', 'KEY', 'CONSTRAINT', 'PRIMARY', 'COMMENT'):
                continue
            cols[mm.group(1)] = 'NOT NULL' in line or 'PRIMARY KEY' in line
        out[m.group(1)] = cols
    return out


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))

    print('\n1. New files are Python 3.9 safe')
    missing = [f for f in NEW_PY if os.path.exists(os.path.join(ROOT, f)) and
               'from __future__ import annotations' not in io.open(os.path.join(ROOT, f), encoding='utf-8').read()]
    check('every new .py file has "from __future__ import annotations"', not missing, missing)

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    from cryptography.fernet import Fernet
    app = create_app('testing')
    tmp_root = tempfile.mkdtemp(prefix='fnd_cc_')
    upload_root = os.path.join(tmp_root, 'uploads')
    os.makedirs(upload_root)
    KEY = Fernet.generate_key().decode()
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False, UPLOAD_FOLDER=upload_root,
                      ENCRYPTION_KEY=KEY)
    try:
        with app.app_context():
            db.create_all()
            run_all(app, db, generate_id, tmp_root)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


def run_all(app, db, generate_id, tmp_root):
    from flask import g
    from app.models.founder import ConciergeCase, ConciergeEdit, ConciergeMessage, FounderVenture
    from app.models.user import User
    now = datetime.utcnow()

    def user(email, admin=False):
        u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x',
                 is_admin=admin, email_verified=True, onboarding_completed_at=now)
        db.session.add(u)
        db.session.commit()
        return u

    ctx = SimpleNamespace(app=app, db=db, tmp_root=tmp_root)
    ctx.admin = user('cc-admin@x.com', admin=True)
    ctx.admin2 = user('plain-admin@x.com', admin=True)
    ctx.member = user('member@x.com')
    ctx.founder = user('founder@x.com')

    client = app.test_client()
    for verb in ('get', 'post', 'put', 'delete'):
        orig = getattr(client, verb)
        setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

    def login(uid):
        with client.session_transaction() as s:
            if uid:
                s['_user_id'], s['_fresh'] = uid, True
            else:
                s.pop('_user_id', None)
    ctx.client, ctx.login = client, login

    step1_schema(ctx)
    step2_settings(ctx)
    step3_state_machine(ctx)
    step4_encryption(ctx)
    step5_admin_area(ctx)
    step6_evaluation(ctx)


# ---------------------------------------------------------------------------
# Step 1: schema, NULL owner
# ---------------------------------------------------------------------------

def step1_schema(ctx):
    db = ctx.db
    from app.models.founder import FounderVenture, VentureArtifact
    print('\n2. Step 1: migration 079 matches the models; ventures may have no owner')
    sql = sql_columns(os.path.join(ROOT, 'sql', '079_founder_concierge.sql'))
    for t in ('concierge_case', 'concierge_edit', 'concierge_message'):
        table = db.metadata.tables[t]
        model = {c.name: not c.nullable for c in table.columns}
        check('%s: SQL and model have the same columns and nullability' % t, sql.get(t) == model,
              (set(model) ^ set(sql.get(t) or {}), [c for c in model if sql.get(t, {}).get(c) != model[c]]))
        check('%s.id is CHAR(9)' % t, re.search(r'CREATE TABLE %s \(\s*id\s+CHAR\(9\)' % t,
                                               io.open(os.path.join(ROOT, 'sql', '079_founder_concierge.sql'),
                                                       encoding='utf-8').read()) is not None)
    txt = io.open(os.path.join(ROOT, 'sql', '079_founder_concierge.sql'), encoding='utf-8').read()
    check('079 makes founder_venture.owner_user_id nullable', 'MODIFY owner_user_id VARCHAR(9) NULL' in txt)
    check('079 adds venture_artifact.encrypted TINYINT(1) NOT NULL DEFAULT 0',
          'ADD COLUMN encrypted TINYINT(1) NOT NULL DEFAULT 0' in txt)
    check('model: owner_user_id nullable, encrypted not null',
          FounderVenture.__table__.c.owner_user_id.nullable and not VentureArtifact.__table__.c.encrypted.nullable)

    v = FounderVenture(owner_user_id=None, name='Unowned Co', stage='idea', status='concierge', track='concierge')
    db.session.add(v)
    db.session.commit()
    check('a venture with no owner can be saved', db.session.get(FounderVenture, v.id).owner_user_id is None)
    from app.services import founder_ventures
    check('an unowned venture is in nobody\'s venture list',
          v.id not in [x.id for x in founder_ventures.ventures_for_user(ctx.founder.id)])

    ctx.login(ctx.admin.id)
    r = ctx.client.get('/admin/founder?tab=ventures')
    html = r.get_data(as_text=True)
    check('/admin/founder Ventures tab renders with an unowned venture', r.status_code == 200, r.status_code)
    check('the unowned venture shows "Unassigned (concierge)"', 'Unassigned (concierge)' in html)

    from app.services import founder_obs_service as fob
    try:
        fob.venture_trace(v, ctx.admin.id)
        ok = True
    except Exception as exc:
        ok = exc
    check('the observability venture trace tolerates a NULL owner', ok is True, ok)

    sqltxt = io.open(os.path.join(ROOT, 'sql', 'tools', 'delete_user.sql'), encoding='utf-8').read()
    check('delete_user.sql deletes concierge rows of the user\'s ventures',
          'DELETE FROM concierge_case WHERE venture_id IN' in sqltxt and 'DELETE FROM concierge_edit' in sqltxt)
    check('delete_user.sql nulls admins named on concierge cases',
          'UPDATE concierge_case SET created_by = NULL' in sqltxt
          and 'UPDATE concierge_case SET confirmed_by = NULL' in sqltxt)
    db.session.delete(v)
    db.session.commit()


# ---------------------------------------------------------------------------
# Step 2: settings
# ---------------------------------------------------------------------------

FEE = 13579          # a fee no code or template may carry


def step2_settings(ctx):
    from app.services import founder_config as fc
    print('\n3. Step 2: concierge settings, safe defaults, validation, admin UI')
    d = fc.DEFAULTS
    check('defaults: fee 0 (not offered), inactive, usd, no Stripe price id',
          (d['fnd_concierge_fee_cents'], d['fnd_concierge_active'], d['fnd_concierge_currency'],
           d['fnd_concierge_stripe_price_id']) == (0, False, 'usd', ''))
    check('defaults: window 30, reminders [7, 21, -3], extend 14, pdf+docx, 20 MB',
          (d['fnd_cc_window_days'], d['fnd_cc_reminder_days'], d['fnd_cc_extend_days'], d['fnd_cc_allowed_types'],
           d['fnd_cc_max_upload_mb']) == (30, [7, 21, -3], 14, ['pdf', 'docx'], 20))
    E = fc.FounderConfigError

    def refused(k, v):
        try:
            fc.set(k, v, ctx.admin.id)
            return False
        except E:
            ctx.db.session.rollback()
            return True
    bad = [('fnd_cc_allowed_types', ['pptx']), ('fnd_cc_allowed_types', []), ('fnd_cc_reminder_days', [0]),
           ('fnd_cc_reminder_days', ['x']), ('fnd_cc_max_upload_mb', 21), ('fnd_cc_window_days', 0),
           ('fnd_concierge_currency', 'dollars'), ('fnd_concierge_stripe_price_id', 'prod_123'),
           ('fnd_concierge_fee_cents', -1), ('fnd_concierge_active', 'maybe')]
    let = [(k, v) for k, v in bad if not refused(k, v)]
    check('invalid concierge settings are refused (unreadable types, bad reminders, caps)', not let, let)

    c = ctx.client
    ctx.login(ctx.admin.id)
    r = c.put('/api/admin/founder/settings', json={'settings': {'fnd_concierge_active': True}})
    check('the fee cannot be turned on while it is 0', r.status_code == 400, r.get_data(as_text=True)[:200])
    r = c.put('/api/admin/founder/settings', json={'settings': {'fnd_concierge_fee_cents': 20}})
    check('a fee under the card minimum is refused', r.status_code == 400)
    r = c.put('/api/admin/founder/settings', json={'settings': {'fnd_cc_reminder_days': [7, 40]}})
    check('a reminder outside the window is refused', r.status_code == 400)
    r = c.put('/api/admin/founder/settings', json={'settings': {
        'fnd_concierge_fee_cents': FEE, 'fnd_concierge_active': True, 'fnd_concierge_currency': 'usd',
        'fnd_cc_reminder_days': [5, -2], 'fnd_cc_allowed_types': ['pdf', 'docx'], 'fnd_cc_max_upload_mb': 10}})
    check('admin saves the fee and concierge settings', r.status_code == 200, r.get_data(as_text=True)[:300])
    p = fc.concierge_pricing()
    check('concierge_pricing() follows the settings', p['fee_cents'] == FEE and p['active'] and p['currency'] == 'usd')
    check('reminders and upload cap are stored typed',
          fc.get('fnd_cc_reminder_days') == [5, -2] and fc.get('fnd_cc_max_upload_mb') == 10)
    from app.models.audit_log import AuditLog
    check('each setting change is audit-logged',
          len([a for a in AuditLog.query.filter_by(action='founder_setting').all()
               if str((a.extra or {}).get('key', '')).startswith(('fnd_cc_', 'fnd_concierge_'))]) >= 6)
    html = c.get('/admin/founder?tab=pricing').get_data(as_text=True)
    check('Pricing tab has the consultation fee fields',
          'data-cents="fnd_concierge_fee_cents"' in html and 'data-bool="fnd_concierge_active"' in html
          and 'fnd_concierge_stripe_price_id' in html and '%.2f' % (FEE / 100.0) in html)
    html = c.get('/admin/founder?tab=concierge').get_data(as_text=True)
    check('a Concierge tab carries window, reminders, extension, types and size',
          all(k in html for k in ('fnd_cc_window_days', 'fnd_cc_reminder_days', 'fnd_cc_extend_days',
                                  'fnd_cc_allowed_types', 'fnd_cc_max_upload_mb')) and 'id="panel-concierge"' in html)
    ctx.login(ctx.member.id)
    r = c.put('/api/admin/founder/settings', json={'settings': {'fnd_concierge_fee_cents': 100}})
    check('a member cannot change the fee', r.status_code == 403)
    fc.set('fnd_cc_max_upload_mb', 20, ctx.admin.id)


# ---------------------------------------------------------------------------
# Step 3: state machine, audit, permission
# ---------------------------------------------------------------------------

EXPECTED_TRANSITIONS = {
    'draft': {'documents_uploaded'},
    'documents_uploaded': {'evaluating'},
    'evaluating': {'ready_for_review', 'documents_uploaded'},
    'ready_for_review': {'confirmed', 'evaluating'},
    'confirmed': {'shared', 'evaluating'},
    'shared': {'viewed', 'expired'},
    'viewed': {'accepted_paid', 'revision_requested', 'declined', 'expired'},
    'revision_requested': {'ready_for_review', 'declined', 'expired'},
    'accepted_paid': {'active'},
    'active': set(),
    'declined': {'deleted'},
    'expired': {'deleted'},
    'deleted': set(),
}


def step3_state_machine(ctx):
    db = ctx.db
    from app.models.audit_log import AuditLog
    from app.models.founder import ConciergeCase, FounderVenture
    from app.services import concierge_service as cs, founder_feature as ff
    print('\n4. Step 3: state machine (every allowed and refused transition), audit, permission')
    check('STATES are the plan\'s thirteen states', set(cs.STATES) == set(EXPECTED_TRANSITIONS)
          and len(cs.STATES) == 13)
    check('TRANSITIONS are exactly plan section 3.6',
          {k: set(v) for k, v in cs.TRANSITIONS.items()} == EXPECTED_TRANSITIONS)
    v = FounderVenture(owner_user_id=None, name='Machine Co', stage='idea', status='concierge', track='concierge')
    db.session.add(v)
    db.session.commit()
    case = ConciergeCase(venture_id=v.id, state='draft', primary_name='P', primary_email='p@x.com')
    db.session.add(case)
    db.session.commit()
    wrong, refused_ok, audit_ok = [], [], []
    for frm in cs.STATES:
        for to in cs.STATES:
            case.state = frm
            db.session.commit()
            before = AuditLog.query.filter_by(action='concierge_' + to).count()
            try:
                cs.transition(case, to, ctx.admin, note='test')
                moved = True
            except cs.ConciergeError:
                moved = False
            db.session.expire_all()
            c2 = db.session.get(ConciergeCase, case.id)
            after = AuditLog.query.filter_by(action='concierge_' + to).count()
            should = to in EXPECTED_TRANSITIONS[frm]
            if moved != should or (c2.state == to) != should and frm != to:
                wrong.append((frm, to, moved))
            if not should and (c2.state != frm or after != before):
                refused_ok.append((frm, to))
            if should:
                rows = [a for a in AuditLog.query.filter_by(action='concierge_' + to).all()
                        if (a.extra or {}).get('from') == frm] if after == before + 1 else []
                row = rows[0] if len(rows) == 1 else None
                meta = getattr(row, 'extra', None) or {}
                if not (row and meta.get('from') == frm and meta.get('to') == to and meta.get('venture') == v.id
                        and meta.get('actor') == ctx.admin.id and row.user_id == ctx.admin.id):
                    audit_ok.append((frm, to, meta))
            case = c2
    n_allowed = sum(len(x) for x in EXPECTED_TRANSITIONS.values())
    check('all %d allowed transitions succeed and %d refused ones raise' % (n_allowed, 13 * 13 - n_allowed),
          not wrong, wrong[:10])
    check('a refused transition leaves the state and the audit log untouched', not refused_ok, refused_ok[:10])
    check('every allowed transition writes concierge_<to_state> with from, to, venture and actor',
          not audit_ok, audit_ok[:5])
    case.state = 'ready_for_review'
    db.session.commit()
    cs.transition(case, 'confirmed', ctx.admin)
    check('confirming stamps confirmed_at and confirmed_by',
          case.confirmed_at is not None and case.confirmed_by == ctx.admin.id)
    try:
        cs.transition(case, 'nonsense', ctx.admin)
        bad = False
    except cs.ConciergeError:
        bad = True
    check('an unknown state is refused', bad)
    db.session.delete(case)
    db.session.delete(v)
    db.session.commit()

    # Permission: admin + explicit allow; members never; not 'on'.
    check('an admin without the allowlist entry cannot concierge', not cs.can_concierge(ctx.admin))
    check('a member cannot concierge', not cs.can_concierge(ctx.member))
    ff.set_user_access([ctx.admin.id, ctx.member.id], 'allowed', ctx.admin.id, key=ff.CONCIERGE)
    check('an allowlisted admin can concierge', cs.can_concierge(ctx.admin))
    check('an allowlisted member still cannot (admins only)', not cs.can_concierge(ctx.member))
    check('another admin, not allowlisted, cannot', not cs.can_concierge(ctx.admin2))
    check('the concierge permission does not depend on the founder_track switch',
          ff.mode('founder_track') == 'off' and cs.can_concierge(ctx.admin))
    try:
        ff.set_mode(ff.CONCIERGE, 'on', ctx.admin.id)
        on_refused = False
    except ff.FounderError:
        db.session.rollback()
        on_refused = True
    check('the concierge permission cannot be set to "on for everyone"', on_refused)
    ff.set_mode(ff.CONCIERGE, 'off', ctx.admin.id)
    check('mode off closes it even for allowlisted admins', not cs.can_concierge(ctx.admin))
    ff.set_mode(ff.CONCIERGE, 'allowlist', ctx.admin.id)
    check('the existing switches keep their defaults', (ff.mode('founder_pitch'), ff.mode('founder_investor'))
          == ('on', 'off'))
    ctx.login(ctx.admin.id)
    html = ctx.client.get('/admin/founder').get_data(as_text=True)
    check('the Overview lists the concierge permission with its allowlist',
          'data-switch="founder_concierge"' in html and 'cc-admin@x.com (allowed)' in html)


# ---------------------------------------------------------------------------
# Step 4: encrypted storage and content checks
# ---------------------------------------------------------------------------

def step4_encryption(ctx):
    db, app = ctx.db, ctx.app
    from sqlalchemy import text as sqltext
    from app.models.founder import EvaluationRun, FounderVenture, VentureArtifact
    from app.services import founder_config as fc, founder_intake_service as fis
    from app.services import founder_evaluation_service as fes
    print('\n5. Step 4: concierge uploads are encrypted at rest; structural content checks')
    v = FounderVenture(owner_user_id=None, name='Crypt Co', stage='idea', status='concierge', track='concierge')
    db.session.add(v)
    db.session.commit()
    pdf = make_pdf(PLAN_TEXT)
    a = fis.save_concierge_upload(v, upload(pdf, 'plan.pdf'), 'startup_plan', ctx.admin)
    check('a concierge PDF is stored with encrypted=1', a.encrypted is True and a.kind == 'startup_plan')
    on_disk = open(a.file_path, 'rb').read()
    check('the file on disk is not the original and holds none of its text',
          on_disk != pdf and b'%PDF' not in on_disk and b'Acme' not in on_disk and a.file_path.endswith('.enc'))
    check('decrypting the file gives the original bytes', fis.artifact_file_bytes(a) == pdf)
    raw = db.session.execute(sqltext('SELECT text_content FROM venture_artifact WHERE id = :i'), {'i': a.id}).scalar()
    check('text_content in the database is not plain text', raw and 'Acme' not in raw and 'warehouse' not in raw)
    check('artifact_text() decrypts it', 'Acme Robotics startup plan' in fis.artifact_text(a))
    check('char_count is the plain text length', a.char_count == len(fis.artifact_text(a)))
    folder = os.path.dirname(a.file_path)
    check('no plain copy of the upload is left in the venture folder',
          all(n.endswith('.enc') for n in os.listdir(folder)), os.listdir(folder))
    d = fis.save_concierge_upload(v, upload(make_docx(PLAN_TEXT), 'summary.docx'), 'exec_summary', ctx.admin)
    check('a plain DOCX is accepted and encrypted', d.encrypted and 'Jane Roe' in fis.artifact_text(d))

    def refused(data, name, kind='ops_plan', status=422):
        try:
            fis.save_concierge_upload(v, upload(data, name), kind, ctx.admin)
            return False
        except fis.IntakeError as exc:
            db.session.rollback()
            return exc.status == status or exc.status
    before = VentureArtifact.query.filter_by(venture_id=v.id).count()
    check('a macro-enabled DOCX (vbaProject.bin) is refused',
          refused(make_docx(PLAN_TEXT, {'word/vbaProject.bin': b'\x00macro'}), 'm.docx') is True)
    check('a DOCX with an embedded OLE object is refused',
          refused(make_docx(PLAN_TEXT, {'word/embeddings/oleObject1.bin': b'\xd0\xcf\x11\xe0'}), 'o.docx') is True)
    check('a DOCX carrying macro data (vbaData.xml) is refused',
          refused(make_docx(PLAN_TEXT, {'customXml/x.xml': b'<x/>', 'word/vbaData.xml': b'<v/>'}), 'v.docx') is True)
    check('a PDF with JavaScript is refused', refused(js_pdf(PLAN_TEXT), 'js.pdf') is True)
    import fitz
    check('an encrypted PDF is refused', refused(make_pdf(PLAN_TEXT, encryption=fitz.PDF_ENCRYPT_AES_256,
                                                          owner_pw='owner', user_pw=''), 'enc.pdf') is True)
    check('a PDF with an embedded file is refused', refused(make_pdf(PLAN_TEXT, embed=True), 'emb.pdf') is True)
    check('a PDF with a launch action is refused',
          refused(make_pdf(PLAN_TEXT) + b'\n% /Launch /F (calc.exe)\n', 'l.pdf') is True)
    check('a renamed non-PDF is refused', refused(b'MZ\x90\x00 not a pdf' * 50, 'x.pdf') is True)
    fc.set('fnd_cc_allowed_types', ['pdf'], ctx.admin.id)
    check('a type not in the allowed-types setting is refused',
          refused(make_docx(PLAN_TEXT), 'ok.docx', status=400) is True)
    fc.set('fnd_cc_allowed_types', ['pdf', 'docx'], ctx.admin.id)
    fc.set('fnd_cc_max_upload_mb', 1, ctx.admin.id)
    check('a file over the size setting is refused (413)',
          refused(b'%PDF-1.4\n' + b'0' * (1024 * 1024 + 10), 'big.pdf', status=413) is True)
    fc.set('fnd_cc_max_upload_mb', 20, ctx.admin.id)
    check('refused uploads add no artifact rows', VentureArtifact.query.filter_by(venture_id=v.id).count() == before)

    key = app.config['ENCRYPTION_KEY']
    app.config['ENCRYPTION_KEY'] = None
    try:
        try:
            fis.save_concierge_upload(v, upload(make_pdf(PLAN_TEXT), 'p.pdf'), 'ops_plan', ctx.admin)
            no_key = False
        except fis.EncryptionUnavailable:
            no_key = True
        check('without ENCRYPTION_KEY the upload is refused (no plain-text fallback)',
              no_key and VentureArtifact.query.filter_by(venture_id=v.id).count() == before)
        try:
            fis.artifact_text(a)
            read_refused = False
        except fis.EncryptionUnavailable:
            read_refused = True
        check('without ENCRYPTION_KEY an encrypted text is not returned', read_refused)
    finally:
        app.config['ENCRYPTION_KEY'] = key

    fake = FakeClaude()
    fes.AI_CLIENT = fake
    run = fes.run(v, ctx.admin)
    check('the evaluation runs on encrypted documents', run.status == 'done', run.status)
    from app.models.founder import EvaluationFinding
    titles = {f.title for f in EvaluationFinding.query.filter_by(evaluation_run_id=run.id).all()}
    check('findings quote the decrypted text (citations verified)', 'No one sells' in titles
          and 'Pain is concrete' in titles, titles)
    check('the model was sent the plain text, not the ciphertext',
          len([c for c in fake.calls if c == 'evaluate_dimension']) == 18)
    plain = VentureArtifact(venture_id=v.id, kind='ops_plan', version=1, is_current=True, filename='o.txt',
                            text_content='Plain self-serve text', char_count=21)
    db.session.add(plain)
    db.session.commit()
    check('a self-serve artifact (encrypted=0) reads as before',
          plain.encrypted is False and fis.artifact_text(plain) == 'Plain self-serve text')
    db.session.delete(plain)
    db.session.commit()
    ctx.crypt_venture = v


# ---------------------------------------------------------------------------
# Step 5: /admin/ventures
# ---------------------------------------------------------------------------

def step5_admin_area(ctx):
    db, c = ctx.db, ctx.client
    from app.models.audit_log import AuditLog
    from app.models.founder import ConciergeCase, FounderVenture, VentureArtifact
    from app.services import concierge_service as cs, legal_text
    print('\n6. Step 5: /admin/ventures queue, create, uploads, fee override, permission')

    ctx.login(ctx.admin2.id)        # admin, not allowlisted
    check('a non-allowlisted admin gets 404 on the queue', c.get('/admin/ventures').status_code == 404)
    check('... and on the create page', c.get('/admin/ventures/new').status_code == 404)
    check('... and on the create API', c.post('/api/admin/ventures', json={}).status_code == 404)
    ctx.login(ctx.member.id)        # allowlisted in step 3, but not an admin
    check('a member gets 403 on the queue', c.get('/admin/ventures').status_code == 403)
    check('a member gets 403 on the create API', c.post('/api/admin/ventures', json={}).status_code == 403)
    ctx.login(None)
    r = c.get('/admin/ventures')
    check('anonymous is sent to sign in', r.status_code in (302, 401), r.status_code)

    ctx.login(ctx.admin.id)
    r = c.get('/admin/ventures/new')
    html = r.get_data(as_text=True)
    check('the create form renders with the consent text and labels from legal_text',
          r.status_code == 200 and 'authorized SimiSI to receive these documents' in html
          and legal_text.get('concierge_consent_source_label') in html and legal_text.get('concierge_nda_label') in html
          and legal_text.get('concierge_confidentiality')[:60] in html)
    base = {'primary_name': 'Jane Roe', 'primary_email': 'Jane@Founder.io', 'cofounder_emails': 'sam@founder.io, x@y.com',
            'source': 'accelerator', 'referrer': 'Pat Ref', 'notes': 'Met at demo day', 'consent_given_by': 'Pat Ref',
            'consent': True, 'nda': True}
    r = c.post('/api/admin/ventures', json=dict(base, consent=False))
    check('consent is required (400)', r.status_code == 400 and 'consent' in r.get_json()['message'].lower())
    r = c.post('/api/admin/ventures', json=dict(base, consent_given_by=''))
    check('who gave consent is required', r.status_code == 400)
    r = c.post('/api/admin/ventures', json=dict(base, primary_email='not-an-email'))
    check('a bad primary email is refused', r.status_code == 400)
    r = c.post('/api/admin/ventures', json=dict(base, primary_name=''))
    check('the primary name is required', r.status_code == 400)
    n_before = FounderVenture.query.count()
    r = c.post('/api/admin/ventures', json=base)
    check('an allowlisted admin creates a concierge venture', r.status_code == 201, r.get_data(as_text=True)[:200])
    vid = r.get_json()['venture_id']
    v = db.session.get(FounderVenture, vid)
    case = cs.case_for(vid)
    check('the venture has no owner, status and track concierge',
          v.owner_user_id is None and v.status == 'concierge' and v.track == 'concierge'
          and FounderVenture.query.count() == n_before + 1)
    check('the case starts in draft with the founder, co-founders, consent and NDA',
          case.state == 'draft' and case.primary_email == 'jane@founder.io' and case.cofounder_emails ==
          ['sam@founder.io', 'x@y.com'] and case.consent_given_by == 'Pat Ref' and case.consent_at is not None
          and case.nda is True and case.created_by == ctx.admin.id and case.consent_version.startswith('concierge_consent@v'))
    check('the case id is a 9-character uppercase key', re.match(r'^[A-Z0-9]{9}$', case.id) is not None, case.id)
    row = AuditLog.query.filter_by(action='concierge_draft', resource_id=vid).first()
    check('creation is audited with the consent text actually shown',
          row is not None and 'Pat Ref authorized SimiSI' in (row.extra or {}).get('consent_text', '')
          and row.user_id == ctx.admin.id)
    from app.services import founder_ventures
    check('the founder (by email) does not see the venture before acceptance',
          vid not in [x.id for x in founder_ventures.ventures_for_user(ctx.founder.id)])

    html = c.get('/admin/ventures').get_data(as_text=True)
    check('the queue lists the venture, primary email and state', vid in html and 'jane@founder.io' in html
          and 'Draft' in html)
    check('the queue is a CSS grid, not a table', '<table' not in html and 'class="cc-list"' in html)
    html = c.get('/admin/ventures?state=confirmed').get_data(as_text=True)
    check('the state filter hides other states', 'jane@founder.io' not in html)

    r = c.get('/admin/ventures/%s' % vid)
    html = r.get_data(as_text=True)
    check('the venture page renders with five upload slots', r.status_code == 200 and html.count('class="cc-upload"') == 5)
    check('a non-concierge venture id is 404', c.get('/admin/ventures/%s' % ctx.crypt_venture.id).status_code == 404)

    data = {'kind': 'startup_plan', 'file': (io.BytesIO(make_pdf(PLAN_TEXT)), 'plan.pdf')}
    r = c.post('/api/admin/ventures/%s/upload' % vid, data=data, content_type='multipart/form-data')
    check('uploading a document works', r.status_code == 200, r.get_data(as_text=True)[:200])
    db.session.expire_all()
    case = cs.case_for(vid)
    check('the first document moves the case to documents_uploaded', case.state == 'documents_uploaded')
    check('...through transition() (audited)', AuditLog.query.filter_by(action='concierge_documents_uploaded',
                                                                        resource_id=vid).count() == 1)
    data = {'kind': 'exec_summary', 'file': (io.BytesIO(make_docx(PLAN_TEXT)), 'summary.docx')}
    r = c.post('/api/admin/ventures/%s/upload' % vid, data=data, content_type='multipart/form-data')
    check('a second kind uploads; the state stays', r.status_code == 200 and cs.case_for(vid).state ==
          'documents_uploaded')
    data = {'kind': 'pitch_deck', 'file': (io.BytesIO(make_docx(PLAN_TEXT, {'word/vbaProject.bin': b'x'})), 'd.docx')}
    r = c.post('/api/admin/ventures/%s/upload' % vid, data=data, content_type='multipart/form-data')
    check('a macro document is refused through the API (422)', r.status_code == 422)
    art = VentureArtifact.query.filter_by(venture_id=vid, kind='startup_plan').first()
    ctx.login(ctx.admin2.id)
    check('a non-allowlisted admin cannot download a document',
          c.get('/admin/ventures/%s/documents/%s' % (vid, art.id)).status_code == 404)
    ctx.login(ctx.admin.id)
    r = c.get('/admin/ventures/%s/documents/%s' % (vid, art.id))
    check('the download is the decrypted original, as an attachment',
          r.status_code == 200 and r.data.startswith(b'%PDF') and 'attachment' in r.headers.get('Content-Disposition', '')
          and r.headers.get('X-Content-Type-Options') == 'nosniff')
    check('the download is audited', AuditLog.query.filter_by(action='concierge_document_download',
                                                              resource_id=vid).count() == 1)

    r = c.post('/api/admin/ventures/%s/fee' % vid, json={'fee_cents': 9900})
    check('a fee override needs a reason', r.status_code == 400)
    r = c.post('/api/admin/ventures/%s/fee' % vid, json={'fee_cents': 9900, 'reason': 'Pilot cohort rate'})
    check('the override applies', r.status_code == 200 and cs.effective_fee_cents(cs.case_for(vid)) == 9900)
    a = AuditLog.query.filter_by(action='concierge_fee_override', resource_id=vid).first()
    check('the override is audited with before, after and reason',
          a is not None and a.extra.get('after') == 9900 and a.extra.get('reason') == 'Pilot cohort rate')
    html = c.get('/admin/ventures').get_data(as_text=True)
    check('the queue shows the override', 'USD 99.00' in html and 'override' in html)
    r = c.post('/api/admin/ventures/%s/fee' % vid, json={'clear': True, 'reason': 'Back to standard'})
    check('clearing the override returns to the Pricing fee', r.status_code == 200
          and cs.effective_fee_cents(cs.case_for(vid)) == FEE)
    html = c.get('/admin/ventures').get_data(as_text=True)
    check('the queue shows the Pricing fee from settings', '{:,.2f}'.format(FEE / 100.0) in html)
    hits = no_price_literal()
    check('no file under app/ carries the consultation fee as a literal', not hits, hits)
    ctx.vid = vid


# ---------------------------------------------------------------------------
# Step 6: evaluation, pre-fill, edits, confirm
# ---------------------------------------------------------------------------

def step6_evaluation(ctx):
    db, c, vid = ctx.db, ctx.client, ctx.vid
    from app.models.audit_log import AuditLog
    from app.models.founder import (AgentBlueprint, ConciergeEdit, EvaluationFinding, EvaluationRun, FounderRunMeta,
                                    FounderVenture)
    from app.services import concierge_service as cs, founder_stream, legal_text
    from app.services import founder_evaluation_service as fes, observability_service as obs
    print('\n7. Step 6: evaluation run, pre-fill with citations, edit history, confirm, cost')
    fake = FakeClaude()
    fes.AI_CLIENT = fake
    cs.RUN_INLINE = True

    ctx.login(ctx.admin2.id)
    check('a non-allowlisted admin cannot start the evaluation',
          c.post('/api/admin/ventures/%s/evaluate' % vid).status_code == 404)
    ctx.login(ctx.member.id)
    check('a member cannot open the venture stream', c.get('/api/admin/ventures/%s/stream' % vid).status_code == 403)
    ctx.login(ctx.admin.id)
    r = c.post('/api/admin/ventures/%s/confirm' % vid)
    check('confirm is refused before review (documents_uploaded)', r.status_code == 409, r.status_code)

    r = c.post('/api/admin/ventures/%s/evaluate' % vid)
    check('"Run evaluation" starts the run (202)', r.status_code == 202, r.get_data(as_text=True)[:200])
    db.session.expire_all()
    case = cs.case_for(vid)
    v = db.session.get(FounderVenture, vid)
    run = fes.latest_run(v)
    check('the run finishes and the case is ready for review', run.status == 'done' and case.state == 'ready_for_review',
          (run.status, case.state))
    states = [ (a.extra or {}).get('to') for a in AuditLog.query.filter(AuditLog.resource_id == vid,
                                                                       AuditLog.action.like('concierge_%')).all()]
    check('evaluating and ready_for_review are both audited transitions',
          'evaluating' in states and 'ready_for_review' in states)
    check('one pre-fill call per run', fake.calls.count('extract_venture_facts') == 1, fake.calls.count('extract_venture_facts'))
    check('the pre-fill call was sent decrypted text', 'Acme Robotics startup plan' in getattr(fake, 'last_doc', ''))
    pf = case.prefill
    check('name and one-liner are proposed by the AI with their quotes',
          pf['name']['status'] == 'ai' and pf['name']['value'] == 'Acme Robotics' and pf['name']['citation']
          and pf['one_liner']['status'] == 'ai')
    check('a fact the documents do not state (stage) is needs_input, never guessed',
          pf['stage']['status'] == 'needs_input' and pf['stage']['value'] is None)
    check('a team member whose quote is not in the documents is dropped',
          [m['name'] for m in pf['team']['value']] == ['Jane Roe'], pf['team'])
    check('alignment is always needs_input', pf['alignment']['status'] == 'needs_input')
    evs = [founder_stream._event_payload(e)[0] for e in founder_stream.events_since(vid)]
    check('progress is published on the venture stream (SSE)',
          'concierge_evaluating' in evs and 'evaluation_done' in evs and 'concierge_ready' in evs, evs)
    r = c.get('/api/admin/ventures/%s/stream' % vid)
    check('the admin stream endpoint is an SSE response', r.status_code == 200 and r.mimetype == 'text/event-stream')
    r.close()

    from app.models.observability import AgentRun
    if obs.layer_code('F1') == 'F1' and obs.setting('obs_capture_enabled'):
        ar = AgentRun.query.filter_by(agent_key=cs.PREFILL_AGENT_KEY).all()
        check('the pre-fill call is an observability run (F1) with tokens',
              len(ar) == 1 and ar[0].layer == 'F1' and ar[0].prompt_tokens == 1000 and ar[0].completion_tokens == 200,
              [(a.layer, a.prompt_tokens) for a in ar])
        meta = FounderRunMeta.query.filter_by(venture_id=vid, agent_key=cs.PREFILL_AGENT_KEY).all()
        check('... with founder run meta carrying its cost', len(meta) == 1 and meta[0].obs_run_id == ar[0].id)
        ev_meta = FounderRunMeta.query.filter_by(venture_id=vid, agent_key=fes.AGENT_KEY).all()
        check('the evaluation run is in observability too, with cost', ev_meta and run.obs_run_id
              and run.cost_cents > 0)
    else:
        check('observability capture is off in this config (skipped)', True)
    pa = AuditLog.query.filter_by(action='concierge_prefill_run', resource_id=vid).first()
    check('the pre-fill cost is recorded', pa is not None and pa.extra.get('cost_cents', 0) > 0)
    check('total API cost = evaluation runs + pre-fill',
          abs(cs.api_cost_cents(v) - (run.cost_cents + pa.extra['cost_cents'])) < 0.01, cs.api_cost_cents(v))
    check('the recommended team is built from the findings (no owner needed)',
          AgentBlueprint.query.filter_by(venture_id=vid, status='recommended').count() >= 1)

    html = c.get('/admin/ventures/%s' % vid).get_data(as_text=True)
    check('the venture page shows the pre-fill with its quote and the needs-input count',
          'Acme Robotics startup plan' in html and 'Needs founder input' in html and 'need founder input' in html)
    check('the page shows the findings and the API cost next to the fee',
          'No one sells' in html and 'id="ccCost"' in html and "Model (API) cost" in html)
    check('the evaluation carries the required disclaimer', legal_text.get('concierge_not_advice') in html
          and 'Not investment advice and not a promise of funding.' in legal_text.get('concierge_not_advice'))

    # Edits: prefill
    r = c.post('/api/admin/ventures/%s/prefill' % vid, json={'field': 'stage', 'value': 'galactic'})
    check('an unknown stage is refused', r.status_code == 400)
    r = c.post('/api/admin/ventures/%s/prefill' % vid, json={'field': 'alignment', 'value': 'aligned'})
    check('alignment cannot be filled in by an admin', r.status_code == 400)
    r = c.post('/api/admin/ventures/%s/prefill' % vid, json={'field': 'stage', 'value': 'seed'})
    check('the admin fills a needs-input field', r.status_code == 200 and cs.case_for(vid).prefill['stage'] ==
          dict(cs.case_for(vid).prefill['stage'], value='seed', status='admin'))
    c.post('/api/admin/ventures/%s/prefill' % vid, json={'field': 'name', 'value': 'Acme Robotics Inc.'})
    e = ConciergeEdit.query.filter_by(case_id=cs.case_for(vid).id, entity='prefill', field='name').first()
    check('an edit records the AI value, before and after, and who',
          e is not None and e.ai_value == 'Acme Robotics' and e.before_value == 'Acme Robotics'
          and e.after_value == 'Acme Robotics Inc.' and e.by_user_id == ctx.admin.id)
    c.post('/api/admin/ventures/%s/prefill' % vid, json={'field': 'team', 'value': 'Jane Roe, CEO\nSam Poe, COO'})
    check('the team can be edited as lines', [m['name'] for m in cs.case_for(vid).prefill['team']['value']]
          == ['Jane Roe', 'Sam Poe'])
    # Edits: findings
    f = (EvaluationFinding.query.filter_by(venture_id=vid, superseded_at=None, title='No one sells').first())
    r = c.post('/api/admin/ventures/%s/findings/%s' % (vid, f.id), json={'title': 'No one owns sales yet',
                                                                          'severity': 'medium'})
    check('the admin edits a finding', r.status_code == 200 and db.session.get(EvaluationFinding, f.id).title ==
          'No one owns sales yet')
    c.post('/api/admin/ventures/%s/findings/%s' % (vid, f.id), json={'title': 'Sales owner missing'})
    rows = ConciergeEdit.query.filter_by(case_id=cs.case_for(vid).id, entity='finding', entity_id=f.id,
                                         field='title').order_by(ConciergeEdit.created_at).all()
    check('every finding edit keeps the AI original', len(rows) == 2 and all(r.ai_value == 'No one sells' for r in rows)
          and rows[1].before_value == 'No one owns sales yet' and rows[1].after_value == 'Sales owner missing')
    r = c.post('/api/admin/ventures/%s/findings/%s' % (vid, f.id), json={'severity': 'extreme'})
    check('an unknown severity is refused', r.status_code == 400)
    r = c.post('/api/admin/ventures/%s/findings/NOPE00000' % vid, json={'title': 'x'})
    check('a finding of another venture is 404', r.status_code == 404)
    html = c.get('/admin/ventures/%s' % vid).get_data(as_text=True)
    check('the page shows the edit history (AI vs admin)', 'Edit history' in html and 'Sales owner missing' in html
          and 'AI: No one sells' in html and 'AI: Acme Robotics' in html)
    ctx.shot_vid = vid

    # Confirm
    r = c.post('/api/admin/ventures/%s/confirm' % vid)
    db.session.expire_all()
    case = cs.case_for(vid)
    v = db.session.get(FounderVenture, vid)
    check('Confirm moves ready_for_review -> confirmed', r.status_code == 200 and case.state == 'confirmed'
          and case.confirmed_by == ctx.admin.id)
    check('the confirmed name, one-liner and stage are saved on the venture',
          v.name == 'Acme Robotics Inc.' and v.stage == 'seed' and v.one_liner == pf['one_liner']['value'])
    check('confirming twice is refused', c.post('/api/admin/ventures/%s/confirm' % vid).status_code == 409)
    r = c.post('/api/admin/ventures/%s/prefill' % vid, json={'field': 'stage', 'value': 'idea'})
    check('edits are refused once confirmed', r.status_code == 409)
    check('the venture still has no owner', v.owner_user_id is None)

    # New documents, re-run, failure, needs_input
    data = {'kind': 'pitch_deck', 'file': (io.BytesIO(make_pdf(PLAN_TEXT + ' Deck.')), 'deck.pdf')}
    r = c.post('/api/admin/ventures/%s/upload' % vid, data=data, content_type='multipart/form-data')
    check('a new document can be added after confirming', r.status_code == 200 and cs.case_for(vid).state == 'confirmed')
    fake.fail = True
    r = c.post('/api/admin/ventures/%s/evaluate' % vid)
    db.session.expire_all()
    check('a failed run returns the case to documents_uploaded', r.status_code == 202
          and cs.case_for(vid).state == 'documents_uploaded', cs.case_for(vid).state)
    html = c.get('/admin/ventures/%s' % vid).get_data(as_text=True)
    check('the failure reason is shown on the venture page', 'The last run failed' in html)
    note = [(a.extra or {}).get('note', '') for a in AuditLog.query.filter_by(action='concierge_documents_uploaded',
                                                                           resource_id=vid).all()]
    check('the failure is audited with its reason', any(n.startswith('evaluation failed') for n in note), note)
    fake.fail = False
    fake.prefill = {'name': {'value': 'Invented Co', 'citation': 'a sentence that is not in any document'},
                    'one_liner': {'value': None, 'citation': None},
                    'stage': {'value': 'seed', 'citation': None}, 'team': []}
    r = c.post('/api/admin/ventures/%s/evaluate' % vid)
    db.session.expire_all()
    case = cs.case_for(vid)
    check('a re-run after new documents reaches review again', case.state == 'ready_for_review', case.state)
    check('values without a verifiable quote are needs_input',
          all(case.prefill[k]['status'] == 'needs_input' for k in ('name', 'one_liner', 'stage', 'team', 'alignment')))
    check('the earlier edit history is kept', ConciergeEdit.query.filter_by(case_id=case.id).count() >= 5)
    cs.RUN_INLINE = False
    fes.AI_CLIENT = None


def no_price_literal():
    """The consultation fee lives only in settings: no file under app/ carries it."""
    hits = []
    needles = (str(FEE), '%.2f' % (FEE / 100.0), '{:,.2f}'.format(FEE / 100.0))
    for path in glob.glob(os.path.join(ROOT, 'app', '**', '*.*'), recursive=True):
        if not path.endswith(('.py', '.html', '.js', '.json')):
            continue
        txt = io.open(path, encoding='utf-8', errors='replace').read()
        if any(n in txt for n in needles):
            hits.append(os.path.relpath(path, ROOT))
    return hits


if __name__ == '__main__':
    sys.exit(main())
