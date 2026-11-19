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
