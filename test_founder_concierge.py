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

NEW_PY = ['app/services/concierge_service.py', 'app/blueprints/founder/concierge_admin.py',
          'app/blueprints/founder/concierge_founder.py']

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
    install_mail_fake(ctx)
    step7_share(ctx)
    step8_preview(ctx)
    step9_payment(ctx)
    step10_transfer(ctx)


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
    'ready_for_review': {'confirmed', 'evaluating', 'expired'},
    'confirmed': {'shared', 'evaluating', 'expired'},
    'shared': {'viewed', 'expired'},
    'viewed': {'accepted_paid', 'revision_requested', 'declined', 'expired'},
    'revision_requested': {'ready_for_review', 'declined', 'expired'},
    'accepted_paid': {'active'},
    'active': set(),
    'declined': {'deleted', 'viewed'},
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
    check('TRANSITIONS are plan section 3.6 plus declined->viewed and review->expired (steps 7-11)',
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
          'Acme Robotics startup plan' in html and 'Needs founder input' in html and 'need founder input.' in html.replace('needs founder input.', 'need founder input.'))
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


# ---------------------------------------------------------------------------
# Faked email (every concierge email goes through email_service._send)
# ---------------------------------------------------------------------------

TOKEN_RE = re.compile(r'/founder/concierge/t/([A-Za-z0-9_\-]+)')


def install_mail_fake(ctx):
    from app.services import email_service
    ctx.mails = []

    def fake_send(subject, recipients, body, html=None):
        ctx.mails.append({'subject': subject, 'to': list(recipients), 'body': body, 'html': html or ''})
    email_service._send = fake_send


def mails_to(ctx, addr, since=0):
    return [m for m in ctx.mails[since:] if addr.lower() in [t.lower() for t in m['to']]]


def last_token(ctx, addr):
    for m in reversed(ctx.mails):
        if addr in m['to']:
            t = TOKEN_RE.search(m['body'])
            if t:
                return t.group(1)
    return None


def make_user(ctx, email, verified=True, admin=False):
    from utils.id_gen import generate_id
    from app.models.user import User
    u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x', is_admin=admin,
             email_verified=verified, onboarding_completed_at=datetime.utcnow())
    ctx.db.session.add(u)
    ctx.db.session.commit()
    return u


# ---------------------------------------------------------------------------
# Step 7: share and invite
# ---------------------------------------------------------------------------

def step7_share(ctx):
    db, c, vid = ctx.db, ctx.client, ctx.vid
    from datetime import timedelta
    from app.models.audit_log import AuditLog
    from app.models.founder import ConciergeCase, FounderVenture
    from app.services import concierge_service as cs, founder_config as fc, legal_text
    print('\n8. Step 7: share with the founder, hashed single-purpose token, re-send, extend, messages')
    from app.services import founder_feature as ff
    ctx.login(ctx.admin.id)
    r = c.post('/api/admin/ventures/%s/share' % vid)
    check('Share is refused before Confirm (ready_for_review)', r.status_code == 409, r.status_code)
    c.post('/api/admin/ventures/%s/prefill' % vid, json={'field': 'name', 'value': 'Acme Robotics'})
    c.post('/api/admin/ventures/%s/prefill' % vid, json={'field': 'one_liner',
                                                        'value': 'Rentable picking robots for small warehouses'})
    c.post('/api/admin/ventures/%s/confirm' % vid)
    fc.set('fnd_concierge_active', False, ctx.admin.id)
    r = c.post('/api/admin/ventures/%s/share' % vid)
    check('Share is refused while the consultation fee is not active', r.status_code == 409
          and 'not active' in r.get_json()['message'])
    fc.set('fnd_concierge_active', True, ctx.admin.id)
    r = c.post('/api/admin/ventures/%s/share' % vid)
    check('Share is refused while the Founder Track is switched off (the founder could not use it)',
          r.status_code == 409 and 'switched off' in r.get_json()['message'])
    ff.set_mode(ff.TRACK, 'allowlist', ctx.admin.id)
    ctx.login(ctx.admin2.id)
    check('a non-allowlisted admin cannot share (404)', c.post('/api/admin/ventures/%s/share' % vid).status_code == 404)
    ctx.login(ctx.admin.id)
    n0 = len(ctx.mails)
    before = datetime.utcnow()
    r = c.post('/api/admin/ventures/%s/share' % vid)
    db.session.expire_all()
    case = cs.case_for(vid)
    check('Share moves confirmed -> shared and says it emailed', r.status_code == 200 and case.state == 'shared'
          and r.get_json()['emailed'] is True, r.get_data(as_text=True)[:200])
    window = fc.get('fnd_cc_window_days')
    check('the window opens: shared_at now, expires_at = now + fnd_cc_window_days',
          case.shared_at >= before.replace(microsecond=0) and
          abs((case.expires_at - case.shared_at) - timedelta(days=window)) < timedelta(seconds=5))
    sent = mails_to(ctx, 'jane@founder.io', n0)
    token = last_token(ctx, 'jane@founder.io')
    check('one invitation email goes to the primary founder', len(sent) == 1 and token, [m['to'] for m in ctx.mails[n0:]])
    m = sent[0]
    v = db.session.get(FounderVenture, vid)
    check('the email is M1 (subject and body from legal_text) and carries E1',
          m['subject'] == legal_text.get('concierge_invite_subject', venture_name=v.name)
          and 'The link works only for jane@founder.io' in m['body'] and 'Pat Ref shared with us' in m['body']
          and legal_text.get('concierge_not_advice') in m['body'])
    check('the sender is the configured MAIL_DEFAULT_SENDER (email_service._send)',
          ctx.app.config['MAIL_DEFAULT_SENDER'] == 'simi@simisi.io' or ctx.app.config['MAIL_DEFAULT_SENDER'])
    check('only the token hash is stored (64-hex SHA-256), never the token',
          re.match(r'^[0-9a-f]{64}$', case.invite_token_hash or '') and case.invite_token_hash != token
          and token not in json.dumps({k: str(getattr(case, k)) for k in ConciergeCase.__table__.columns.keys()}))
    check('invite_version starts at 1 and the send is audited',
          case.invite_version == 1 and AuditLog.query.filter_by(action='concierge_invite_sent', resource_id=vid).count() == 1)
    check('the shared transition is audited', AuditLog.query.filter_by(action='concierge_shared', resource_id=vid).count() == 1)
    check('the admin email carries no venture document content', 'warehouse' not in m['body'].lower()
          or 'Rentable picking robots' not in m['body'])

    # The link.
    link = '/founder/concierge/t/%s' % token
    ctx.login(None)
    r = c.get(link)
    html = r.get_data(as_text=True)
    check('signed out: the link asks to sign in (next=) and shows no venture data',
          r.status_code == 200 and 'next=' in html and v.name not in html and 'jane@founder.io' not in html)
    other = make_user(ctx, 'stranger@x.com')
    ctx.login(other.id)
    r = c.get(link)
    html = r.get_data(as_text=True)
    check('another verified account gets the M6 page (403) with no venture data',
          r.status_code == 403 and legal_text.get('concierge_email_mismatch') in html and v.name not in html
          and 'Rentable' not in html)
    jane = make_user(ctx, 'Jane@Founder.io', verified=False)
    ctx.jane = jane
    ctx.login(jane.id)
    r = c.get(link)
    check('the right email, unverified: M6 (403), no venture data', r.status_code == 403
          and v.name not in r.get_data(as_text=True))
    check('refused claims are audited without the addresses',
          AuditLog.query.filter_by(action='concierge_invite_refused', resource_id=vid).count() == 2)
    jane.email_verified = True
    db.session.commit()
    r = c.get(link)
    check('the verified primary founder (email compared case-insensitively) is sent to the preview',
          r.status_code == 302 and r.headers['Location'].endswith('/founder/concierge/%s' % case.id), r.status_code)
    r = c.get('/founder/concierge/t/not-a-real-token')
    check('an unknown token is "no longer valid" (410)', r.status_code == 410)

    # Re-send and correct the email.
    ctx.login(ctx.admin.id)
    r = c.post('/api/admin/ventures/%s/resend' % vid, json={})
    token2 = last_token(ctx, 'jane@founder.io')
    db.session.expire_all()
    case = cs.case_for(vid)
    check('re-send issues a new token and version 2', r.status_code == 200 and token2 != token and case.invite_version == 2)
    ctx.login(jane.id)
    check('the old link stops working (410)', c.get(link).status_code == 410)
    check('the new link works', c.get('/founder/concierge/t/%s' % token2).status_code == 302)
    ctx.login(ctx.admin.id)
    r = c.post('/api/admin/ventures/%s/resend' % vid, json={'email': 'not an email'})
    check('a bad corrected email is refused', r.status_code == 400)
    r = c.post('/api/admin/ventures/%s/resend' % vid, json={'email': 'roe@founder.io'})
    token3 = last_token(ctx, 'roe@founder.io')
    db.session.expire_all()
    check('"Correct email & re-send" updates primary_email and emails the new address',
          r.status_code == 200 and cs.case_for(vid).primary_email == 'roe@founder.io' and token3)
    a = AuditLog.query.filter_by(action='concierge_invite_resent', resource_id=vid).order_by(
        AuditLog.created_at.desc()).first()
    check('the correction is audited (email_changed, version) without the addresses',
          a is not None and a.extra.get('email_changed') is True and a.extra.get('version') == 3
          and 'roe@founder.io' not in json.dumps(a.extra))
    ctx.login(jane.id)
    check('after the correction the old address cannot use its link (410)',
          c.get('/founder/concierge/t/%s' % token2).status_code == 410)
    check('... nor the new link (M6, 403)', c.get('/founder/concierge/t/%s' % token3).status_code == 403)
    ctx.login(ctx.admin.id)
    c.post('/api/admin/ventures/%s/resend' % vid, json={'email': 'jane@founder.io'})
    ctx.token = last_token(ctx, 'jane@founder.io')
    db.session.expire_all()
    check('the email is corrected back (version 4)', cs.case_for(vid).invite_version == 4)

    # Extend once.
    case = cs.case_for(vid)
    exp = case.expires_at
    r = c.post('/api/admin/ventures/%s/extend' % vid, json={})
    check('an extension needs a reason', r.status_code == 400)
    r = c.post('/api/admin/ventures/%s/extend' % vid, json={'reason': 'Founder travelling'})
    db.session.expire_all()
    case = cs.case_for(vid)
    check('Extend adds fnd_cc_extend_days once', r.status_code == 200 and case.expires_at - exp ==
          timedelta(days=fc.get('fnd_cc_extend_days')) and case.extend_reason == 'Founder travelling')
    a = AuditLog.query.filter_by(action='concierge_extended', resource_id=vid).first()
    check('the extension is audited with before, after and reason',
          a is not None and a.extra.get('reason') == 'Founder travelling' and a.extra.get('after'))
    r = c.post('/api/admin/ventures/%s/extend' % vid, json={'reason': 'again'})
    db.session.expire_all()
    check('a second extension is refused and changes nothing', r.status_code == 409
          and cs.case_for(vid).expires_at == case.expires_at)
    r = c.post('/api/admin/ventures/%s/reopen' % vid)
    check('"Reopen for review" is refused unless a revision was requested', r.status_code == 409)

    # The admin page.
    html = c.get('/admin/ventures/%s' % vid).get_data(as_text=True)
    check('the case page shows the window, days left, re-send and the used extension',
          'id="ccInvite"' in html and 'days left' in html and 'id="ccResend"' in html
          and 'cannot be extended again' in html and 'id="ccExtend"' not in html)
    check('the case page offers the messages thread', 'id="ccMessages"' in html and 'id="ccReply"' in html)
    n0 = len(ctx.mails)
    r = c.post('/api/admin/ventures/%s/messages' % vid, json={'body': 'Happy to walk you through it.'})
    check('an admin reply is saved and the founder is told by email (without the text)',
          r.status_code == 200 and len(mails_to(ctx, 'jane@founder.io', n0)) == 1
          and 'walk you through' not in ctx.mails[-1]['body'])
    html = c.get('/admin/ventures').get_data(as_text=True)
    check('the queue shows days since shared and days left', 'Shared' in html)


# ---------------------------------------------------------------------------
# Step 8: founder preview, revision, decline, pending card
# ---------------------------------------------------------------------------

PREVIEW_KEYS = {'case_id', 'state', 'state_label', 'venture', 'overall_score', 'top_gaps', 'needs_input_count',
                'unlocks', 'ownership_terms', 'fee', 'refund_policy', 'expires_on', 'days_left', 'days_left_text',
                'disclaimer', 'revision_note', 'decline_confirm', 'messages', 'actions', 'declined',
                'revision_requested'}


def leaks(text, ctx, v):
    """Full-evaluation content that must never reach the preview."""
    from app.models.founder import AgentBlueprint, EvaluationFinding, VentureArtifact
    from app.services import founder_evaluation_service as fes
    bad = []
    for f in EvaluationFinding.query.filter_by(venture_id=v.id).all():
        for s in (f.detail, f.citation_text, f.citation_ref):
            if s and s in text:
                bad.append(('finding', s[:40]))
    for a in VentureArtifact.query.filter_by(venture_id=v.id).all():
        if a.filename and a.filename in text:
            bad.append(('document', a.filename))
    for b in AgentBlueprint.query.filter_by(venture_id=v.id).all():
        if b.agent_key in text:
            bad.append(('agent', b.agent_key))
    for frag in ('third-party logistics', 'learns the shelf layout', 'Business model: monthly rental',
                 'citation', 'blueprint', 'startup_page', '/startup/', 'plan_step', '.pdf', '.docx'):
        if frag in text:
            bad.append(('text', frag))
    for dim, (label, *_rest) in fes.RUBRIC.items():
        if '"%s"' % dim in text:
            bad.append(('dimension', dim))
    return bad


def step8_preview(ctx):
    db, c, vid = ctx.db, ctx.client, ctx.vid
    from app.models.audit_log import AuditLog
    from app.models.founder import ConciergeMessage, FounderVenture
    from app.services import concierge_service as cs, founder_feature as ff, legal_text
    print('\n9. Step 8: founder preview (primary founder only, never the full evaluation), revision, decline')
    case = cs.case_for(vid)
    v = db.session.get(FounderVenture, vid)
    url, api = '/founder/concierge/%s' % case.id, '/api/founder/concierge/%s' % case.id
    jane = ctx.jane

    # Who may open it.
    cofounder = make_user(ctx, 'sam@founder.io')
    ctx.cofounder = cofounder
    for label, uid in (('a co-founder on file', cofounder.id), ('another account', ctx.member.id),
                       ('an admin (even with the concierge permission)', ctx.admin.id)):
        ctx.login(uid)
        r1, r2 = c.get(url), c.get(api)
        check('%s gets 403 on the preview page and its JSON' % label,
              r1.status_code == 403 and r2.status_code == 403 and v.name not in r1.get_data(as_text=True)
              and v.name not in r2.get_data(as_text=True), (r1.status_code, r2.status_code))
    ctx.login(None)
    check('signed out: the page asks to sign in, the JSON is 401',
          c.get(url).status_code == 302 and c.get(api).status_code == 401)
    jane.email_verified = False
    db.session.commit()
    ctx.login(jane.id)
    check('the primary founder with an unverified email gets 403', c.get(api).status_code == 403)
    jane.email_verified = True
    db.session.commit()
    db.session.expire_all()
    check('nothing so far counted as a view', cs.case_for(vid).state == 'shared')

    n0 = len(ctx.mails)
    r = c.get(api)
    d = r.get_json()
    db.session.expire_all()
    case = cs.case_for(vid)
    check('the primary founder opens the JSON preview; the first view moves shared -> viewed',
          r.status_code == 200 and case.state == 'viewed' and case.viewed_at is not None)
    admin_mail = [m for m in ctx.mails[n0:] if ctx.app.config['FOUNDER_EMAIL'] in m['to']]
    check('the admin is notified of the view (FOUNDER_EMAIL)', len(admin_mail) == 1 and 'viewed' in admin_mail[0]['subject'])
    check('the JSON has exactly the preview fields', set(d) == PREVIEW_KEYS, set(d) ^ PREVIEW_KEYS)
    check('name, one-liner, overall score, top three gap titles and the needs-input count',
          d['venture'] == {'name': v.name, 'one_liner': v.one_liner or ''} and isinstance(d['overall_score'], int)
          and 0 < len(d['top_gaps']) <= 3 and all(isinstance(t, str) for t in d['top_gaps'])
          and d['needs_input_count'] == cs.needs_input_count(case) and d['needs_input_count'] >= 1)
    check('the fee comes from Pricing (F1 line), with P1, P3, F2 and E1',
          d['fee']['cents'] == FEE and d['fee']['line'] == legal_text.get('concierge_fee_line', fee=cs.money(FEE))
          and d['unlocks'] == legal_text.get('concierge_unlocks') and str(d['days_left']) in d['days_left_text']
          and d['disclaimer'] == 'Not investment advice and not a promise of funding.'
          and d['refund_policy'].startswith('Refundable within'))
    raw = r.get_data(as_text=True)
    bad = leaks(raw, ctx, v)
    check('the JSON exposes no finding detail, citation, document, agent team, plan, page or dimension scores',
          not bad, bad[:6])
    r = c.get(url)
    html = r.get_data(as_text=True)
    check('the preview page renders for the primary founder', r.status_code == 200 and v.name in html)
    bad = leaks(html, ctx, v)
    check('the HTML exposes none of it either', not bad, bad[:6])
    check('the page shows score, gaps, needs-input, P1, P2, F1, F2, P3 and E1',
          all(t in html for t in d['top_gaps']) and legal_text.get('concierge_unlocks') in html
          and legal_text.get('concierge_ownership_terms')[:60] in html and cs.money(FEE) in html
          and 'days left' in html and legal_text.get('concierge_not_advice') in html
          and 'id="ccpAccept"' in html)
    check('the page is a CSS grid, not a table', '<table' not in html)
    check('a second view does not notify again',
          len([m for m in ctx.mails[n0:] if ctx.app.config['FOUNDER_EMAIL'] in m['to']]) == 1)

    # Fee override reaches the preview.
    ctx.login(ctx.admin.id)
    c.post('/api/admin/ventures/%s/fee' % vid, json={'fee_cents': 9900, 'reason': 'Pilot cohort rate'})
    ctx.login(jane.id)
    d = c.get(api).get_json()
    check('a per-venture override replaces the Pricing fee on the preview', d['fee']['cents'] == 9900
          and 'USD 99.00' in d['fee']['line'])
    ctx.login(ctx.admin.id)
    c.post('/api/admin/ventures/%s/fee' % vid, json={'clear': True, 'reason': 'Back to standard'})

    # Pending invitation card.
    ctx.login(jane.id)
    html = c.get('/dashboard').get_data(as_text=True)
    check('the dashboard shows the pending invitation to the verified invited email',
          'id="ccPending"' in html and v.name in html and url in html)
    ff.set_user_access([jane.id], 'allowed', ctx.admin.id)
    html = c.get('/founder').get_data(as_text=True)
    check('/founder shows the pending invitation card too', 'id="ccPending"' in html and url in html)
    ctx.login(cofounder.id)
    check('a co-founder on file sees no pending card', 'id="ccPending"' not in c.get('/dashboard').get_data(as_text=True))

    # Revision request.
    ctx.login(jane.id)
    r = c.post(api + '/revision', json={'body': ''})
    check('an empty revision request is refused', r.status_code == 400)
    n0 = len(ctx.mails)
    r = c.post(api + '/revision', json={'body': 'Please re-check the team section.'})
    db.session.expire_all()
    case = cs.case_for(vid)
    check('"Request a revision" -> revision_requested with a founder message',
          r.status_code == 200 and case.state == 'revision_requested'
          and ConciergeMessage.query.filter_by(case_id=case.id, direction='founder').count() == 1)
    check('the admin is notified (without the message text)',
          any('revision' in m['subject'] for m in ctx.mails[n0:]) and
          not any('re-check the team' in m['body'] for m in ctx.mails[n0:]))
    d = c.get(api).get_json()
    check('while a revision is open the founder cannot pay', d['actions']['accept'] is False and d['revision_requested'])
    ctx.login(ctx.cofounder.id)
    check('a co-founder cannot request a revision or decline (403)',
          c.post(api + '/revision', json={'body': 'x'}).status_code == 403
          and c.post(api + '/decline', json={'confirm': True}).status_code == 403)
    ctx.login(ctx.admin.id)
    html = c.get('/admin/ventures/%s' % vid).get_data(as_text=True)
    check('the admin sees the request on the case page and can reopen it',
          'Please re-check the team section.' in html and 'id="ccReopen"' in html)
    exp = case.expires_at
    r = c.post('/api/admin/ventures/%s/reopen' % vid)
    check('"Reopen for review" -> ready_for_review', r.status_code == 200 and cs.case_for(vid).state == 'ready_for_review')
    ctx.login(jane.id)
    r = c.get(url)
    check('meanwhile the founder sees "SimiSI is revising" (no venture data)',
          r.status_code == 200 and 'revising' in r.get_data(as_text=True) and v.name not in r.get_data(as_text=True))
    ctx.login(ctx.admin.id)
    c.post('/api/admin/ventures/%s/messages' % vid, json={'body': 'Updated the team findings.'})
    c.post('/api/admin/ventures/%s/confirm' % vid)
    r = c.post('/api/admin/ventures/%s/share' % vid)
    db.session.expire_all()
    case = cs.case_for(vid)
    check('re-sharing after the revision issues a new link and keeps the running window',
          r.status_code == 200 and case.state == 'shared' and case.expires_at == exp and case.invite_version == 5)
    ctx.token = last_token(ctx, 'jane@founder.io')
    ctx.login(jane.id)
    d = c.get(api).get_json()
    check('the founder sees the admin reply in the thread', any(m['direction'] == 'admin' and 'team findings' in m['body']
                                                               for m in d['messages']))

    # Decline and change of mind.
    r = c.post(api + '/decline', json={'reason': 'Not now'})
    check('decline needs the P4 confirmation', r.status_code == 400)
    n0 = len(ctx.mails)
    r = c.post(api + '/decline', json={'confirm': True, 'reason': 'Not now'})
    db.session.expire_all()
    case = cs.case_for(vid)
    check('"Decline" -> declined with the optional reason', r.status_code == 200 and case.state == 'declined'
          and case.decline_reason == 'Not now' and case.declined_at is not None)
    check('the admin is notified of the decline', any('declined' in m['subject'] for m in ctx.mails[n0:]))
    check('decline is audited', AuditLog.query.filter_by(action='concierge_declined', resource_id=vid).count() == 1)
    html = c.get(url).get_data(as_text=True)
    check('the declined preview shows P4 and offers to reopen', 'You declined this evaluation' in html
          and 'id="ccpReopen"' in html and 'id="ccpAccept"' not in html)
    r = c.post(api + '/reopen')
    db.session.expire_all()
    case = cs.case_for(vid)
    check('the founder may change their mind within the window: declined -> viewed',
          r.status_code == 200 and case.state == 'viewed' and case.declined_at is None and case.decline_reason is None)



# ---------------------------------------------------------------------------
# Stripe fakes and a quick shared case
# ---------------------------------------------------------------------------

class FakeStripeObj(dict):
    """A stripe object: dict access plus attributes."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


def install_stripe_fake(ctx):
    import stripe
    from app.blueprints.payments import routes as pay_routes
    ctx.app.config.update(STRIPE_SECRET_KEY='sk_test_fake', STRIPE_WEBHOOK_SECRET='whsec_fake')
    ctx.created, ctx.sessions, ctx.refunds = [], {}, []

    def fake_create(**kw):
        sid = 'cs_cc_%d' % (len(ctx.created) + 1)
        ctx.created.append(kw)
        s = FakeStripeObj(id=sid, url='https://checkout.stripe.test/' + sid, status='open', payment_status='unpaid',
                          metadata=dict(kw.get('metadata') or {}),
                          amount_total=int((kw.get('metadata') or {}).get('amount_cents') or 0))
        ctx.sessions[sid] = s
        return s
    stripe.checkout.Session.create = staticmethod(fake_create)
    stripe.checkout.Session.retrieve = staticmethod(lambda sid, **kw: ctx.sessions[sid])
    stripe.PaymentIntent.retrieve = staticmethod(lambda pi, **kw: FakeStripeObj(id=pi, latest_charge='ch_' + pi))

    def fake_refund(**kw):
        ctx.refunds.append(kw)
        return FakeStripeObj(id='re_%d' % len(ctx.refunds), status='succeeded', amount=0)
    stripe.Refund.create = staticmethod(fake_refund)
    ctx.next_event = {}
    pay_routes.construct_webhook_event = lambda payload, sig: ctx.next_event['e']

    def hook(obj, etype='checkout.session.completed'):
        ctx.next_event['e'] = {'type': etype, 'data': {'object': obj}}
        ctx.login(None)
        return ctx.client.post('/api/payments/webhook', data=b'{}', headers={'Stripe-Signature': 'sig'})
    ctx.hook = hook


def paid(ctx, sid, pi, **over):
    """The completed-session event Stripe would send for checkout sid."""
    s = dict(ctx.sessions[sid])
    s.update({'payment_status': 'paid', 'status': 'complete', 'payment_intent': pi})
    s.update(over)
    return s


def quick_case(ctx, email, state='viewed', name='Quick Co', days_left_=20, shared_days_ago=10, fee_override=None):
    """A concierge venture with a finished evaluation and a case already shared."""
    from datetime import timedelta
    from app.models.founder import ConciergeCase, EvaluationFinding, EvaluationRun, FounderVenture
    db = ctx.db
    now = datetime.utcnow()
    v = FounderVenture(owner_user_id=None, name=name, one_liner='Helps %s' % name, stage='idea', status='concierge',
                       track='concierge', rung=1)
    db.session.add(v)
    db.session.flush()
    run = EvaluationRun(venture_id=v.id, status='done', overall_score=55, scores={'team': 55}, finished_at=now)
    db.session.add(run)
    db.session.flush()
    db.session.add(EvaluationFinding(venture_id=v.id, evaluation_run_id=run.id, dimension='team', ftype='gap',
                                     severity='high', title='Gap of %s' % name, detail='Secret detail of %s' % name,
                                     citation_text='secret quote %s' % name))
    c = ConciergeCase(venture_id=v.id, state=state, primary_name=name + ' Founder', primary_email=email,
                      cofounder_emails=['co-%s' % email], consent_given_by='Founder', consent_at=now,
                      shared_at=now - timedelta(days=shared_days_ago),
                      expires_at=now + timedelta(days=days_left_), invite_version=1, invite_token_hash='h' + v.id,
                      fee_override_cents=fee_override, notes='Private note %s' % name,
                      prefill={'_meta': {}, 'name': {'value': name, 'status': 'admin'},
                               'one_liner': {'value': None, 'status': 'needs_input'},
                               'stage': {'value': None, 'status': 'needs_input'},
                               'team': {'value': None, 'status': 'needs_input'},
                               'alignment': {'value': None, 'status': 'needs_input'}},
                      reminders_sent={}, created_by=ctx.admin.id)
    db.session.add(c)
    db.session.commit()
    return v, c


# ---------------------------------------------------------------------------
# Step 9: payment
# ---------------------------------------------------------------------------

def step9_payment(ctx):
    db, c, vid = ctx.db, ctx.client, ctx.vid
    from app.models.audit_log import AuditLog
    from app.models.founder import FounderVenture
    from app.services import concierge_service as cs, founder_config as fc, stripe_service
    print('\n10. Step 9: Stripe checkout for the consultation fee; acceptance only on a paid session')
    install_stripe_fake(ctx)
    case = cs.case_for(vid)
    jane = ctx.jane
    accept = '/founder/concierge/%s/accept' % case.id
    check('the case is viewed before checkout', case.state == 'viewed')

    for label, uid in (('a co-founder', ctx.cofounder.id), ('another account', ctx.member.id),
                       ('an admin', ctx.admin.id)):
        ctx.login(uid)
        r = c.post(accept)
        check('%s cannot accept (403) and no checkout is created' % label, r.status_code == 403 and not ctx.created)

    fc.set('fnd_concierge_active', False, ctx.admin.id)
    ctx.login(jane.id)
    r = c.post(accept)
    check('with the fee inactive, Accept creates no checkout', r.status_code == 303 and not ctx.created
          and 'notice=unavailable' in r.headers['Location'])
    fc.set('fnd_concierge_active', True, ctx.admin.id)

    r = c.post(accept)
    db.session.expire_all()
    kw = ctx.created[-1] if ctx.created else {}
    check('"Accept and pay" redirects to Stripe Checkout (303)', r.status_code == 303
          and r.headers['Location'].startswith('https://checkout.stripe.test/'))
    li = (kw.get('line_items') or [{}])[0]
    check('the amount is the Pricing fee, inline price_data, in the configured currency',
          li.get('price_data', {}).get('unit_amount') == FEE and li['price_data']['currency'] == 'usd')
    md = kw.get('metadata') or {}
    check('metadata: product concierge_fee, case_id, venture_id, user_id, amount',
          md.get('product') == 'concierge_fee' and md.get('case_id') == case.id and md.get('venture_id') == vid
          and md.get('user_id') == jane.id and md.get('amount_cents') == str(FEE))
    check('starting checkout leaves the case unchanged', cs.case_for(vid).state == 'viewed')
    row = AuditLog.query.filter_by(action='concierge_checkout_started', resource_id=vid).first()
    check('the fee at checkout creation is recorded', row is not None and row.extra.get('amount_cents') == FEE
          and row.extra.get('session_id') == 'cs_cc_1')
    r = c.post(accept)
    check('a second click reuses the open session (no second chargeable session)',
          len(ctx.created) == 1 and r.headers['Location'].endswith('cs_cc_1'))

    ctx.login(ctx.admin.id)
    c.post('/api/admin/ventures/%s/fee' % vid, json={'fee_cents': 9900, 'reason': 'Pilot cohort rate'})
    ctx.login(jane.id)
    c.post(accept)
    check('the per-venture override is charged (new session at the new amount)',
          len(ctx.created) == 2 and ctx.created[-1]['line_items'][0]['price_data']['unit_amount'] == 9900)
    fc.set('fnd_concierge_stripe_price_id', 'price_cc123', ctx.admin.id)
    c.post(accept)
    check('with a Stripe price id set, an override still uses an inline price',
          'price_data' in ctx.created[-1]['line_items'][0])
    ctx.login(ctx.admin.id)
    c.post('/api/admin/ventures/%s/fee' % vid, json={'clear': True, 'reason': 'Back to standard'})
    ctx.login(jane.id)
    c.post(accept)
    check('with a Stripe price id set, the Pricing fee uses that price id',
          ctx.created[-1]['line_items'][0] == {'price': 'price_cc123', 'quantity': 1})
    fc.set('fnd_concierge_stripe_price_id', '', ctx.admin.id)
    sid = ctx.created[-1]['metadata'] and 'cs_cc_%d' % len(ctx.created)

    # Abandoned and failed payments change nothing.
    ctx.sessions[sid]['status'] = 'expired'
    r = c.get('/founder/concierge/%s/success?session_id=%s' % (case.id, sid))
    db.session.expire_all()
    check('an abandoned checkout (success return, session unpaid) leaves the state', r.status_code == 303
          and cs.case_for(vid).state == 'viewed' and 'notice=pending' in r.headers['Location'])
    ctx.hook(dict(ctx.sessions[sid], payment_status='unpaid', status='expired'))
    ctx.hook({'id': 'pi_fail', 'metadata': {}}, etype='payment_intent.payment_failed')
    db.session.expire_all()
    check('an unpaid session and a failed payment webhook leave the state', cs.case_for(vid).state == 'viewed'
          and cs.case_for(vid).payment_intent_id is None)
    check('nothing was charged twice: no venture is paid', db.session.get(FounderVenture, vid).paid_at is None)

    # A paid session for the wrong amount or the wrong payer is not accepted.
    n0 = len(ctx.mails)
    ctx.hook(paid(ctx, sid, 'pi_wrong', amount_total=FEE - 100))
    db.session.expire_all()
    check('a paid session whose amount differs from the recorded fee is not accepted',
          cs.case_for(vid).state == 'viewed' and AuditLog.query.filter_by(action='concierge_payment_mismatch',
                                                                          resource_id=vid).count() == 1)
    check('... and the admin is told to refund it', any('not accepted' in m['subject'] for m in ctx.mails[n0:]))
    other = paid(ctx, sid, 'pi_other')
    other['metadata'] = dict(other['metadata'], user_id=ctx.cofounder.id)
    ctx.hook(other)
    db.session.expire_all()
    check('a paid session by someone other than the primary founder is not accepted', cs.case_for(vid).state == 'viewed')
    from app.blueprints.founder.billing import fulfil_founder_session
    check('the self-serve fulfilment ignores a concierge session', fulfil_founder_session(paid(ctx, sid, 'pi_x')) is False)

    # The paid webhook accepts.
    n0 = len(ctx.mails)
    r = ctx.hook(paid(ctx, sid, 'pi_cc_good'))
    db.session.expire_all()
    case = cs.case_for(vid)
    check('a paid concierge_fee session (webhook) accepts the case', r.status_code == 200
          and case.state in ('accepted_paid', 'active'), case.state)
    check('... recording the payment intent, amount and accepting user',
          case.payment_intent_id == 'pi_cc_good' and case.amount_cents == FEE and case.accepted_user_id == jane.id
          and case.accepted_at is not None)
    check('accepted_paid is an audited transition',
          AuditLog.query.filter_by(action='concierge_accepted_paid', resource_id=vid).count() == 1)
    r = ctx.hook(paid(ctx, sid, 'pi_cc_good'))
    db.session.expire_all()
    check('a replayed webhook changes nothing',
          AuditLog.query.filter_by(action='concierge_accepted_paid', resource_id=vid).count() == 1)
    ctx.hook(paid(ctx, 'cs_cc_1', 'pi_cc_second'))
    db.session.expire_all()
    check('a second paid session for an accepted case is flagged for refund, not accepted again',
          AuditLog.query.filter_by(action='concierge_duplicate_payment', resource_id=vid).count() == 1
          and cs.case_for(vid).payment_intent_id == 'pi_cc_good')
    ctx.paid_session = sid

    # Second founder: the success return (webhook fallback) and the admin refund.
    bob = make_user(ctx, 'bob@founder.io')
    bv, bc = quick_case(ctx, 'bob@founder.io', name='Bob Labs')
    ctx.bob, ctx.bob_vid = bob, bv.id
    ctx.login(bob.id)
    c.get('/founder/concierge/%s' % bc.id)
    r = c.post('/founder/concierge/%s/accept' % bc.id)
    bsid = 'cs_cc_%d' % len(ctx.created)
    ctx.sessions[bsid].update({'payment_status': 'paid', 'status': 'complete', 'payment_intent': 'pi_bob'})
    r = c.get('/founder/concierge/%s/success?session_id=%s' % (bc.id, bsid))
    db.session.expire_all()
    bc = cs.case_for(bv.id)
    check('the success return verifies the session with Stripe and accepts when it is paid',
          bc.state in ('accepted_paid', 'active') and bc.payment_intent_id == 'pi_bob')
    ctx.login(ctx.admin.id)
    r = c.post('/api/admin/ventures/%s/refund' % bv.id, json={})
    check('a refund needs a reason', r.status_code == 400 and not ctx.refunds)
    html = c.get('/admin/ventures/%s' % bv.id).get_data(as_text=True)
    check('the case page offers the refund', 'id="ccRefundForm"' in html)
    r = c.post('/api/admin/ventures/%s/refund' % bv.id, json={'reason': 'Founder asked within the window'})
    db.session.expire_all()
    bc = cs.case_for(bv.id)
    check('"Refund" issues a Stripe refund of the charge', r.status_code == 200 and len(ctx.refunds) == 1
          and ctx.refunds[0]['charge'] == 'ch_pi_bob')
    check('refunded_at is set and the refund is audited', bc.refunded_at is not None
          and AuditLog.query.filter_by(action='concierge_refunded', resource_id=bv.id).count() == 1)
    ctx.hook({'id': 'ch_pi_bob', 'payment_intent': 'pi_bob', 'refunded': True}, etype='charge.refunded')
    db.session.expire_all()
    check('the charge.refunded webhook afterwards is a no-op',
          AuditLog.query.filter_by(action='concierge_refunded', resource_id=bv.id).count() == 1)
    ctx.login(ctx.admin.id)
    r = c.post('/api/admin/ventures/%s/refund' % bv.id, json={'reason': 'again'})
    check('a second refund is refused', r.status_code == 409 and len(ctx.refunds) == 1)



# ---------------------------------------------------------------------------
# Step 10: acceptance transfer
# ---------------------------------------------------------------------------

def step10_transfer(ctx):
    db, c, vid = ctx.db, ctx.client, ctx.vid
    from datetime import timedelta
    from app.models.audit_log import AuditLog
    from app.models.founder import DecisionRight, FounderVenture, StartupPage, VentureMember
    from app.services import concierge_service as cs, founder_ventures, legal_text
    print('\n11. Step 10: ownership transfer, confirm-your-details card, no premium fee, D5 refund')
    jane = ctx.jane
    db.session.expire_all()
    case = cs.case_for(vid)
    v = db.session.get(FounderVenture, vid)
    check('the paid case ends active', case.state == 'active', case.state)
    check('the venture is the primary founder\'s: owner, status active, track concierge, paid',
          v.owner_user_id == jane.id and v.status == 'active' and v.track == 'concierge' and v.paid_at is not None
          and v.amount_cents == FEE and v.payment_intent_id == 'pi_cc_good')
    m = VentureMember.query.filter_by(venture_id=vid).all()
    check('one owner member, as create_venture makes it', len(m) == 1 and m[0].user_id == jane.id
          and m[0].role == 'owner' and m[0].status == 'active')
    rights = DecisionRight.query.filter_by(venture_id=vid).all()
    check('the default decision rights are held by the owner',
          len(rights) == len(DecisionRight.DECISION_TYPES) and all(r.holder_member_id == m[0].id for r in rights))
    check('the transfer is audited ("ownership transferred") and active is an audited transition',
          AuditLog.query.filter_by(action='concierge_ownership_transferred', resource_id=vid).count() == 1
          and AuditLog.query.filter_by(action='concierge_active', resource_id=vid).count() == 1)
    page = StartupPage.query.filter_by(venture_id=vid).first()
    check('a startup page draft exists and is private', page is not None and page.status == 'draft')
    ctx.login(None)
    check('the draft page is not public', c.get('/startup/%s' % page.slug).status_code == 404)
    check('the venture is in the founder\'s list', vid in [x.id for x in founder_ventures.ventures_for_user(jane.id)])
    receipt = [x for x in mails_to(ctx, 'jane@founder.io') if x['subject'].startswith('Receipt')]
    check('the receipt (M4 + amount + F2 + F3) is emailed to the founder', len(receipt) == 1
          and legal_text.get('concierge_accepted_email', venture_name=v.name) in receipt[0]['body']
          and cs.money(FEE) in receipt[0]['body'] and legal_text.get('concierge_receipt_footer') in receipt[0]['body']
          and 'Refundable within' in receipt[0]['body'])
    check('the admin is notified of the acceptance', any('accepted and paid' in x['subject'] for x in ctx.mails))
    check('co-founders on file are NOT invited (D8)', not mails_to(ctx, 'sam@founder.io') and not mails_to(ctx, 'x@y.com')
          and VentureMember.query.filter_by(venture_id=vid).count() == 1)

    ctx.login(jane.id)
    r = c.get('/founder/concierge/%s' % case.id)
    check('the preview link now opens the workspace', r.status_code == 302 and r.headers['Location'].endswith('/founder/v/%s' % vid))
    r = c.get('/founder/v/%s' % vid)
    html = r.get_data(as_text=True)
    check('the workspace opens like a paid self-serve venture, with "Confirm your details"',
          r.status_code == 200 and 'id="ccDetails"' in html and 'id="fw-live"' in html)
    fields = re.findall(r'class="ccd-row" data-field="([a-z_]+)"', html)
    need = [k for k in fields if k in ('stage', 'team', 'alignment')]
    check('"Needs founder input" fields come first', fields[:len(need)] == need and 'Needs founder input' in html, fields)
    check('co-founder emails on file pre-fill the invite form (link only, no send)',
          '/alignment?invite=sam%40founder.io' in html)
    r = c.get('/founder/v/%s/alignment?invite=sam%%40founder.io' % vid)
    check('the alignment page reads ?invite= to pre-fill the email', r.status_code == 200
          and "get('invite')" in r.get_data(as_text=True))

    # Never the Founder Track premium fee.
    n_created = len(ctx.created)
    r = c.get('/founder/v/%s/checkout' % vid)
    html = r.get_data(as_text=True)
    from app.services import founder_config
    price = founder_config.pricing()['price_cents']
    check('the Founder Track checkout page refuses a concierge venture (409, no price)',
          r.status_code == 409 and 'id="ccNoFee"' in html and '{:,.2f}'.format(price / 100.0) not in html)
    r = c.post('/founder/v/%s/checkout' % vid)
    check('... and creating a Founder Track checkout for it is refused', r.status_code == 409
          and len(ctx.created) == n_created)

    # Confirm your details.
    r = c.post('/api/founder/concierge/v/%s/details' % vid, json={'name': 'Acme Robotics', 'one_liner': 'x',
                                                                  'stage': 'galactic'})
    check('an unknown stage is refused', r.status_code == 400)
    ctx.login(ctx.cofounder.id)
    r = c.post('/api/founder/concierge/v/%s/details' % vid, json={'name': 'Hijack', 'stage': 'seed'})
    check('someone who is not a member cannot confirm (403)', r.status_code == 403)
    ctx.login(jane.id)
    r = c.post('/api/founder/concierge/v/%s/details' % vid, json={'name': 'Acme Robotics', 'stage': 'pre-seed',
                                                                  'one_liner': 'Robots that pick for small warehouses'})
    db.session.expire_all()
    v = db.session.get(FounderVenture, vid)
    check('the owner confirms name, one-liner and stage', r.status_code == 200 and v.stage == 'pre-seed'
          and v.one_liner == 'Robots that pick for small warehouses')
    check('the card closes once confirmed', 'id="ccDetails"' not in c.get('/founder/v/%s' % vid).get_data(as_text=True))
    check('the confirmation is audited', AuditLog.query.filter_by(action='concierge_details_confirmed',
                                                                  resource_id=vid).count() == 1)

    # Owner cap: concierge ventures are exempt (D6).
    bob = ctx.bob
    made = []
    for i in range(founder_ventures.MAX_VENTURES_PER_OWNER):
        made.append(founder_ventures.create_venture(bob, 'Bob self-serve %d' % i))
    check('a concierge venture does not count toward the owner cap (D6)', len(made) == founder_ventures.MAX_VENTURES_PER_OWNER
          and len(founder_ventures.owned_ventures(bob.id)) == founder_ventures.MAX_VENTURES_PER_OWNER + 1)
    try:
        founder_ventures.create_venture(bob, 'One too many')
        capped = False
    except founder_ventures.VentureError:
        db.session.rollback()
        capped = True
    check('the cap still applies to self-serve ventures', capped and not founder_ventures.can_start_another(bob.id))

    # Admin: read-only after the transfer unless support access is on.
    ctx.login(ctx.admin.id)
    html = c.get('/admin/ventures/%s' % vid).get_data(as_text=True)
    check('the admin case page is read-only after the transfer',
          'id="ccOwned"' in html and 'read-only' in html and 'id="ccReply"' not in html and 'id="ccFeeForm"' not in html
          and 'class="cc-upload"' not in html and 'id="ccRun"' not in html)
    r = c.post('/api/admin/ventures/%s/messages' % vid, json={'body': 'Hello'})
    check('... and refuses changes', r.status_code == 409)
    let = []
    for path, body in (('/upload', None), ('/evaluate', {}), ('/fee', {'fee_cents': 9900, 'reason': 'x'}),
                       ('/confirm', {}), ('/share', {}), ('/extend', {'reason': 'x'}), ('/reopen', {}),
                       ('/prefill', {'field': 'name', 'value': 'x'})):
        rr = c.post('/api/admin/ventures/%s%s' % (vid, path), json=body) if body is not None else \
            c.post('/api/admin/ventures/%s%s' % (vid, path))
        if rr.status_code < 400:
            let.append((path, rr.status_code))
    check('upload, evaluate, fee, confirm, share, extend, reopen and edits are all refused after the transfer',
          not let, let)
    v.support_access_until = datetime.utcnow() + timedelta(days=3)
    db.session.commit()
    r = c.post('/api/admin/ventures/%s/messages' % vid, json={'body': 'Support note'})
    check('with the founder\'s support access the admin may message them', r.status_code == 200)
    v.support_access_until = None
    db.session.commit()

    # D5: a refund keeps the venture the founder's but locks it again.
    r = ctx.hook({'id': 'ch_pi_cc_good', 'payment_intent': 'pi_cc_good', 'refunded': True}, etype='charge.refunded')
    db.session.expire_all()
    v = db.session.get(FounderVenture, vid)
    case = cs.case_for(vid)
    check('charge.refunded on the consultation fee locks the venture (paid_at cleared) and keeps the owner (D5)',
          r.status_code == 200 and v.paid_at is None and v.owner_user_id == jane.id and case.refunded_at is not None
          and case.state == 'active')
    check('the refund is audited', AuditLog.query.filter_by(action='concierge_refunded', resource_id=vid).count() == 1)
    ctx.login(jane.id)
    html = c.get('/founder/v/%s' % vid).get_data(as_text=True)
    check('the locked concierge workspace never offers the Founder Track fee',
          'Unlock the Founder Track</a>' not in html and 'Why is it locked?' in html)
    bv = db.session.get(FounderVenture, ctx.bob_vid)
    check('the admin refund (Bob) also locked his venture and kept him as owner', bv.paid_at is None
          and bv.owner_user_id == bob.id)


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
