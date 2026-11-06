"""Banned earnings-claims phrases (COMPLIANCE_PLAN A3).

Fails if any phrase in config/compliance_phrases.json ['banned'] appears in:
  * a rendered page (public, member and admin pages through the test client),
  * a static JS string,
  * an email template (SimiSI's drip/broadcast defaults and every email body
    built in app/services), or
  * an agent prompt (every prompt source: agents.json, the layer definitions,
    the briefing rules, Simi and guide prompts).
/legal/* is excluded (counsel's text). 'review' phrases only warn. A seeded
phrase proves the scanner fails.

    python test_compliance_phrases.py
"""
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []
CFG = json.load(io.open(os.path.join(ROOT, 'config', 'compliance_phrases.json'), encoding='utf-8'))


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def scan(text, phrases=None):
    """Banned phrases found in text (after removing allowed disclaimer wording)."""
    t = ' '.join((text or '').lower().split())
    for ok in CFG['allowed_contexts']:
        t = t.replace(ok, ' ')
    hits = []
    for p in (phrases or CFG['banned']):
        if re.search(r'(?<![a-z])' + re.escape(p.lower()) + r'(?![a-z])', t):
            hits.append(p)
    return hits


def visible(html):
    """Page text a visitor sees or a script prints: drop tags, keep script strings."""
    html = re.sub(r'(?is)<style\b.*?</style>', ' ', html)
    return re.sub(r'<[^>]+>', ' ', html)


def py_strings(path):
    """String literals in a Python file, minus docstrings and comments: only
    strings can reach a prompt, an email or a page."""
    import ast
    tree = ast.parse(io.open(path, encoding='utf-8').read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    # Joined with a separator that breaks phrases, so two neighbouring literals
    # ('…Automated', 'Wealth…') can't form a phrase neither one contains.
    return ' ¦ '.join(n.value for n in ast.walk(tree)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs)


def sources():
    """(label, text) for every agent prompt, email template, page template and
    script: config JSON, Python string literals, template text, static JS."""
    out = []
    for f in ('config/agents.json', 'config/agent_execution.json', 'config/integrations.json', 'config/jobs.json'):
        if os.path.exists(os.path.join(ROOT, f)):
            out.append((f, io.open(os.path.join(ROOT, f), encoding='utf-8').read()))
    for root, _d, files in os.walk(os.path.join(ROOT, 'app')):
        if '__pycache__' in root:
            continue
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, ROOT).replace(os.sep, '/')
            if f.endswith('.py'):
                out.append((rel, py_strings(full)))
            elif f.endswith('.html') and '/legal/' not in rel:
                out.append((rel, visible(io.open(full, encoding='utf-8').read())))
            elif f.endswith('.js') and '.min.' not in f and '/static/' in rel:
                out.append((rel, re.sub(r'(?m)^\s*//.*$', ' ', io.open(full, encoding='utf-8').read())))
    return out


def main():
    print('\nThe scanner itself')
    check('a seeded banned phrase is caught', scan('Build passive income on autopilot!') == ['passive income', 'on autopilot'])
    check('word boundaries hold ("guaranteed" inside a word does not match)', scan('unguaranteedly') == [])
    check('disclaimer wording is allowed', scan('This is not a guarantee of results.') == [])

    failures = []

    print('\nPrompts, email templates, page templates and JS (source)')
    srcs = sources()
    dirty = [(rel, h) for rel, h in ((rel, scan(text)) for rel, text in srcs) if h]
    for rel, hits in dirty:
        failures.append((rel, hits))
        check('%-52s clean' % rel, False, hits)
    check('%d source files scanned, none dirty' % len(srcs), not dirty and len(srcs) > 300, len(dirty))

    print('\nRendered pages')
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    from cryptography.fernet import Fernet
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      ENCRYPTION_KEY=Fernet.generate_key().decode())
    import anthropic

    class _NoAI:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, *a, **k):
            raise RuntimeError('offline')
    anthropic.Anthropic = _NoAI
    with app.app_context():
        from flask import g
        from app import models as _models  # noqa: F401
        db.create_all()
        from app.models.user import User
        from app.models.profile import UserProfile
        from app.models.bio_page import BioPage
        from app.models.simulation import Simulation, SimulationLayer, IncomeStream
        now = datetime.utcnow()
        u = User(id=generate_id(), email='p@t.local', full_name='Pat Doe', password_hash='x', email_verified=True,
                 onboarding_completed_at=now, is_admin=True)
        db.session.add(u)
        db.session.commit()
        db.session.add(UserProfile(user_id=u.id, display_name='Pat Doe', username='patdoe', is_published=True,
                                   tagline='Operations consultant'))
        sim = Simulation(id=generate_id(), user_id=u.id, name='S', expertise_zone='Operations', status='complete')
        db.session.add(sim)
        db.session.flush()
        for n in range(1, 6):
            lay = SimulationLayer(id=generate_id(), simulation_id=sim.id, layer_number=n,
                                  layer_name=SimulationLayer.LAYER_NAMES[n], ai_narrative='Narrative.')
            db.session.add(lay)
            db.session.flush()
            db.session.add(IncomeStream(id=generate_id(), layer_id=lay.id, name='Stream', est_monthly_low=1000,
                                        est_monthly_high=2000, ai_reasoning='r'))
        db.session.add(BioPage(id=generate_id(), user_id=u.id, slug='patdoe', simulation_id=sim.id,
                               status=BioPage.STATUS_PUBLISHED, theme='light', published_at=now - timedelta(days=2)))
        db.session.commit()
        client = app.test_client()
        anon = ['/', '/login', '/register', '/careers', '/blog', '/partners/apply', '/samples/maya', '/samples/marcus',
                '/explore', '/help/simi', '/transition-package', '/u/patdoe']
        member = ['/dashboard', '/simulations/%s' % sim.id, '/simulations/%s/gcc' % sim.id,
                  '/simulations/%s/setup' % sim.id, '/simulations/%s/income' % sim.id, '/guide',
                  '/settings/profile', '/settings/integrations', '/settings/voice', '/settings/agent-autonomy',
                  '/u/patdoe/edit']

        def page(url):
            r = client.get(url, follow_redirects=True)
            hits = scan(visible(r.get_data(as_text=True)))
            if hits:
                failures.append((url, hits))
            check('%-44s clean' % url, r.status_code == 200 and not hits, hits or r.status_code)

        for url in anon:
            page(url)
        with client.session_transaction() as s:
            s['_user_id'] = u.id
            s['_fresh'] = True
        g.pop('_login_user', None)
        for url in member:
            page(url)
            g.pop('_login_user', None)

        print('\nEmail templates (rendered drip and broadcast defaults)')
        from app.services import outreach_campaign_service as ocs
        for key, tpl in ocs.DEFAULT_TEMPLATES.items():
            text = tpl['subject'] + ' ' + tpl.get('preview_text', '') + ' ' + tpl['body']
            hits = scan(text)
            check('drip template %-30s clean' % key, not hits, hits)

        print('\nReview list (warnings only)')
        for root, _d, files in os.walk(os.path.join(ROOT, 'app', 'templates')):
            for f in files:
                if f.endswith('.html') and 'legal' not in root:
                    t = io.open(os.path.join(root, f), encoding='utf-8').read()
                    hits = scan(visible(t), CFG['review'])
                    if hits:
                        print('  warn %s: %s' % (os.path.relpath(os.path.join(root, f), ROOT), hits))

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
