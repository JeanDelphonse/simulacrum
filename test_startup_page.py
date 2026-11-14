"""Founder Track public startup page (FOUNDER_PLAN.md WP6).

Slug rules (reserved, unique, history redirect), the flag gate, unpublished
404, publishing (publish_page decision right + clear name check), the name
check, founder-stated labels, no ask in the public HTML, team links only to
published bios, the intro/data-room request (Contact + audit + founders
emailed + validation + per-IP limit), visits and analytics, the editor, and
the sitemap/robots additions. In-memory SQLite; requests and email blocked.

    python test_startup_page.py
"""
import io
import os
import sys
from datetime import datetime

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = ['app/services/startup_page_service.py', 'app/blueprints/startup/routes.py',
          'app/blueprints/founder/page_editor.py']


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))

    print('\n1. New files are Python 3.9 safe')
    missing = [f for f in NEW_PY
               if 'from __future__ import annotations' not in io.open(os.path.join(ROOT, f), encoding='utf-8').read()]
    check('every new .py file has "from __future__ import annotations"', not missing, missing)

    import tempfile
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      UPLOAD_FOLDER=tempfile.mkdtemp(prefix='stpage_'))

    sent = []
    from app.services import email_service
    email_service._send = lambda subject, recipients, body, html=None: sent.append((subject, list(recipients), body))

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.audit_log import AuditLog
        from app.models.bio_page import BioPage
        from app.models.contact import Contact
        from app.models.founder import (DecisionRight, EvaluationFinding, EvaluationRun, ProofPlan, ProofResult,
                                        StartupPage, StartupPageVisit, StartupSlugHistory, VentureMember)
        from app.models.profile import UserProfile
        from app.models.user import User
        from app.services import founder_feature, founder_ventures
        from app.services import startup_page_service as sps
        now = datetime.utcnow()

        def user(email, admin=False, name=None):
            u = User(id=generate_id(), email=email, full_name=name or email.split('@')[0].title(),
                     password_hash='x', is_admin=admin, email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        admin = user('admin@x.com', admin=True)
        owner = user('owner@x.com', name='Olive Owner')
        cof = user('cof@x.com', name='Cora Cofounder')
        outsider = user('out@x.com')
        A_ID, O_ID, C_ID, X_ID = admin.id, owner.id, cof.id, outsider.id

        client = app.test_client()
        for verb in ('get', 'post', 'put', 'delete', 'open'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                if uid:
                    s['_user_id'], s['_fresh'] = uid, True
                else:
                    s.pop('_user_id', None)

        def api(method, path, body=None, uid=O_ID, **kw):
            login(uid)
            fn = getattr(client, method)
            r = fn(path, json=body, **kw) if body is not None else fn(path, **kw)
            return r.status_code, (r.get_json(silent=True) or {})

        # ── venture with an evaluation, a co-founder and proof ──
        v = founder_ventures.create_venture(db.session.get(User, O_ID), 'Dockwise', 'Scheduling for loading docks',
                                            'seed')
        V = v.id
        cm = VentureMember(venture_id=V, user_id=C_ID, role='cofounder', status='active', title='CTO', joined_at=now)
        db.session.add(cm)
        run = EvaluationRun(venture_id=V, status='done', scores={}, started_at=now, finished_at=now)
        db.session.add(run)
        db.session.flush()

        def finding(dim, ftype, cite):
            db.session.add(EvaluationFinding(venture_id=V, evaluation_run_id=run.id, dimension=dim, layer=1,
                                             ftype=ftype, severity='medium', title='t', confidence=0.8,
                                             citation_text=cite, citation_ref='pitch_deck@v1'))
        finding('problem', 'strength', 'Warehouses lose four hours a day to trucks waiting at the dock.')
        finding('market', 'assumption', 'Every warehouse in North America needs this.')
        finding('solution', 'gap', 'No product description.')
        plan = ProofPlan(venture_id=V, customer_name='Acme Logistics', status='complete')
        db.session.add(plan)
        db.session.flush()
        db.session.add(ProofResult(venture_id=V, proof_plan_id=plan.id, measured=True,
                                   benefits_text='Wait time fell from 4 hours to 50 minutes over six weeks.',
                                   quote_text='We stopped paying detention fees.', quote_permission=True))
        db.session.add(ProofResult(venture_id=V, proof_plan_id=plan.id, measured=False,
                                   quote_text='SECRET UNPERMITTED QUOTE', quote_permission=False))
        # The owner has a published bio page; the co-founder does not.
        db.session.add(UserProfile(user_id=O_ID, username='olive', display_name='Olive Owner'))
        db.session.add(UserProfile(user_id=C_ID, username='cora', display_name='Cora Cofounder'))
        db.session.add(BioPage(user_id=O_ID, slug='olive', status='published'))
        db.session.add(BioPage(user_id=C_ID, slug='cora', status='draft'))
        db.session.commit()

        print('\n2. Slugs')
        check('slugify lowercases and hyphenates', sps.slugify('Acme  Robotics, Inc.') == 'acme-robotics-inc')
        check('slugify strips accents', sps.slugify('Café Ünïon') == 'cafe-union')
        check('slugify never returns an empty slug', sps.slugify('!!!') == 'startup')
        check('short names are padded to the minimum', len(sps.slugify('X')) >= sps.SLUG_MIN)
        for w in ('edit', 'i', 'unlock', 'signin', 'request', 'onepager', 'admin', 'api', 'new', 'static'):
            check('"%s" is reserved' % w, w in sps.RESERVED)
        check('a reserved base gets a numeric suffix', sps.unique_slug('Admin') == 'admin-2')
        from app.models.profile import UserProfile as _UP  # noqa: F401
        import app.blueprints.profile.routes as _prof
        before_reserved = set(getattr(_prof, '_RESERVED', set()))

        login(O_ID)
        check('editor 404s while the track is off', client.get('/founder/v/%s/page' % V).status_code == 404)
        founder_feature.set_mode('founder_track', 'on', A_ID)
        # Unpaid: the startup page is part of the paid track; findings stay locked.
        r = client.get('/founder/v/%s/page' % V)
        check('unpaid: the editor redirects the owner to checkout', r.status_code == 302
              and '/checkout' in r.headers.get('Location', ''), (r.status_code, r.headers.get('Location')))
        login(C_ID)
        r = client.get('/founder/v/%s/page/preview' % V)
        check('unpaid: a co-founder is sent to the report', r.status_code == 302
              and '/report' in r.headers.get('Location', ''), (r.status_code, r.headers.get('Location')))
        code, body = api('post', '/api/founder/v/%s/page' % V, {'name': 'x'}, uid=O_ID)
        check('unpaid: the editor API answers 402 with the checkout URL', code == 402 and body.get('checkout_url'),
              (code, body))
        code, body = api('post', '/api/founder/v/%s/page/publish' % V, {'action': 'publish'}, uid=O_ID)
        check('unpaid: publishing answers 402', code == 402, code)
        check('unpaid: no page was drafted', StartupPage.query.filter_by(venture_id=V).count() == 0)
        check('unpaid: no drafting from locked findings', sps._evidence(v) == {} and sps.supported_fields(v) == set())
        v.paid_at = now
        db.session.commit()
        login(O_ID)
        r = client.get('/founder/v/%s/page' % V)
        check('the editor opens for the owner', r.status_code == 200, r.status_code)
        page = StartupPage.query.filter_by(venture_id=V).first()
        check('ensure_page drafted the page', page is not None and page.slug == 'dockwise' and page.status == 'draft')
        check('problem drafted from the cited strength finding', page.problem and 'four hours' in page.problem)
        check('... and it is evidence-backed (not founder-stated)', 'problem' not in (page.founder_stated or []))
        check('market drafted from an assumption is founder-stated', 'market' in (page.founder_stated or []))
        check('a gap citation is not used as page copy', not page.solution)
        check('the one-liner is founder-stated', 'one_liner' in (page.founder_stated or []))
        check('an ordinary name is cleared automatically', page.name_check == 'clear')
        check('ensure_page is idempotent', sps.ensure_page(v).id == page.id
              and StartupPage.query.filter_by(venture_id=V).count() == 1)

        v2 = founder_ventures.create_venture(db.session.get(User, X_ID), 'Dockwise', '', 'idea')
        p2 = sps.ensure_page(v2)
        check('a second page with the same name gets a numbered slug', p2.slug == 'dockwise-2', p2.slug)
        try:
            sps.set_slug(p2, 'dockwise')
            check('a taken slug is refused', False)
        except sps.PageError:
            check('a taken slug is refused', True)
        for bad in ('edit', 'Has Space', 'a', '-lead', 'tail-', 'dou--ble', 'x' * 61):
            try:
                sps.validate_slug(bad, p2.id)
                check('slug %r is refused' % bad, False)
            except sps.PageError:
                check('slug %r is refused' % bad, True)

        print('\n3. Publishing rules')
        code, body = api('post', '/api/founder/v/%s/page/publish' % V, {'action': 'publish'}, uid=C_ID)
        check('a co-founder without the decision right cannot publish', code == 403, (code, body))
        code, body = api('post', '/api/founder/v/%s/page/publish' % V, {'action': 'request'}, uid=C_ID)
        db.session.refresh(page)
        check('a co-founder can request publishing', code == 200 and page.status == 'draft', (code, body))
        check('... the holder is emailed', any('owner@x.com' in s[1] and 'Publish request' in s[0] for s in sent))
        check('... and the request is audit-logged',
              AuditLog.query.filter_by(action='founder_page_publish_requested').count() == 1)
        # Move the publish_page right to the co-founder: now the owner cannot publish.
        right = DecisionRight.query.filter_by(venture_id=V, decision_type='publish_page').first()
        right.holder_member_id = cm.id
        db.session.commit()
        try:
            sps.publish(page, db.session.get(User, O_ID))
            check('the owner without the right cannot publish', False)
        except sps.PageError:
            check('the owner without the right cannot publish', True)
        login(None)
        check('a draft page 404s publicly', client.get('/startup/dockwise').status_code == 404)

        # Name check holds publishing.
        code, body = api('post', '/api/founder/v/%s/page' % V, {'name': 'G00gle Docks'}, uid=C_ID)
        db.session.refresh(page)
        check('a name imitating a famous brand sets name_check pending', page.name_check == 'pending', body)
        check('... and shows up in the admin Name checks tab',
              'G00gle Docks' in (lambda: (login(A_ID), client.get('/admin/founder?tab=names'))[1])().get_data(True))
        code, body = api('post', '/api/founder/v/%s/page/publish' % V, {'action': 'publish'}, uid=C_ID)
        check('publish is refused while the name is pending', code == 403 and page.status != 'published', body)
        code, body = api('post', '/api/founder/v/%s/page/publish' % V, {'action': 'request'}, uid=C_ID)
        db.session.refresh(page)
        check('a publish request while pending holds the page', page.status == 'held', (page.status, body))
        code, body = api('post', '/api/admin/founder/name-checks/%s' % page.id, {'state': 'clear'}, uid=A_ID)
        db.session.refresh(page)
        check('an admin clears the name', code == 200 and page.name_check == 'clear')
        check('a held page is still not public', (login(None), client.get('/startup/dockwise'))[1].status_code == 404)
        code, body = api('post', '/api/founder/v/%s/page' % V, {'name': 'Dockwise'}, uid=C_ID)
        db.session.refresh(page)
        check('renaming re-runs the check (an exact match of another published page is pending only if '
              'published; here clear)', page.name_check == 'clear', page.name_check)
        # An admin hold is sticky: renaming goes back to review, never straight to clear.
        api('post', '/api/admin/founder/name-checks/%s' % page.id, {'state': 'held'}, uid=A_ID)
        db.session.refresh(page)
        code, body = api('post', '/api/founder/v/%s/page/publish' % V, {'action': 'publish'}, uid=C_ID)
        check('a held name refuses publishing with honest copy', code == 403
              and 'did not clear' in body.get('error', ''), body)
        code, body = api('post', '/api/founder/v/%s/page' % V, {'name': 'Dockwise Inc.'}, uid=C_ID)
        db.session.refresh(page)
        check('renaming after an admin hold goes to pending, not clear', page.name_check == 'pending'
              and not sps.is_public(page), page.name_check)
        code, body = api('post', '/api/founder/v/%s/page' % V, {'name': 'Dockwise'}, uid=C_ID)
        db.session.refresh(page)
        check('... and a second rename does not clear it either', page.name_check == 'pending', page.name_check)
        api('post', '/api/admin/founder/name-checks/%s' % page.id, {'state': 'clear'}, uid=A_ID)
        db.session.refresh(page)
        check('the admin clears it again', page.name_check == 'clear')
        check('exact match against another published page is flagged',
              (lambda: (setattr(p2, 'status', 'published'), db.session.commit(),
                        sps.name_concerns('dockwise', page.id), setattr(p2, 'status', 'draft'),
                        db.session.commit()))()[2] != [])
        for nm, flagged in (('Apple Orchard Labs', True), ('Stripe Pay Tools', True), ('Metaphor Labs', False),
                            ('Dockwise', False), ('OpenAI Helper', True), ('Shopifyx Tools', True)):
            check('name check %r -> %s' % (nm, 'pending' if flagged else 'clear'),
                  bool(sps.name_concerns(nm, page.id)) == flagged, sps.name_concerns(nm, page.id))

        print('\n4. Editing and claims')
        code, body = api('post', '/api/founder/v/%s/page' % V,
                         {'solution': 'Investors can expect a 10x return within five years.'}, uid=C_ID)
        check('copy projecting investor returns is refused', code == 400 and 'return' in body.get('error', ''), body)
        for txt in ('Now accepting investments from everyone.', 'Invest in us today.',
                    'Anyone can invest here.'):
            code, body = api('post', '/api/founder/v/%s/page' % V, {'one_liner': txt}, uid=C_ID)
            check('public solicitation %r is refused (no 506(c))' % txt, code == 400, body)
        code, body = api('post', '/api/founder/v/%s/page' % V,
                         {'traction': [{'label': 'Round', 'value': 'open', 'note': 'Invest in us'}]}, uid=C_ID)
        check('solicitation in a traction note is refused', code == 400, body)
        check('solicitation passes for a 506(c) venture',
              sps.copy_problem('Invest in us today.', '506c') == ''
              and sps.copy_problem('Invest in us today.', 'undecided') != '')
        check('advice in public copy is refused',
              sps.copy_problem('You should invest in our seed round.', '506c') != '')
        code, body = api('post', '/api/founder/v/%s/page' % V,
                         {'traction': [{'label': 'Uptime', 'value': '100%', 'note': 'Guaranteed every month'}]},
                         uid=C_ID)
        try:
            import app.services.founder_compliance  # noqa: F401  (WP4; banned phrases checked when present)
            check('a banned earnings phrase in traction is refused', code == 400, body)
        except ImportError:
            check('a projection regex still applies without founder_compliance', sps.return_projection('IRR of 40'))
        code, body = api('post', '/api/founder/v/%s/page' % V, {'demo_url': 'https://evil.example/video'}, uid=C_ID)
        check('a demo link outside the allowlist is refused', code == 400, body)
        check('youtube/vimeo/loom links embed',
              sps.demo_embed('https://youtu.be/dQw4w9WgXcQ')['embed_url'].endswith('/embed/dQw4w9WgXcQ')
              and sps.demo_embed('https://vimeo.com/76979871')['embed_url'] == 'https://player.vimeo.com/video/76979871'
              and sps.demo_embed('https://www.loom.com/share/abcdef123456')['embed_url'].startswith(
                  'https://www.loom.com/embed/')
              and sps.demo_embed('javascript:alert(1)') is None)
        payload = {'solution': 'A dock calendar that carriers book themselves.', 'stage': 'seed',
                   'demo_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
                   'market_method': 'Bottom-up: 14,000 US warehouses times the annual price.',
                   'business_model': 'Annual subscription per dock door.',
                   'customer': 'Operations managers at mid-size third-party warehouses.',
                   'ask_round': 'Seed', 'ask_amount': '1,500,000', 'use_of_funds': 'SECRET-USE-OF-FUNDS hiring',
                   'traction': [{'label': 'Pilot customers', 'value': '3', 'as_of': '2026-10-01',
                                 'note': 'NOTE-DETAIL signed pilots'},
                                {'label': 'Hidden metric', 'value': '99', 'show': False}]}
        code, body = api('post', '/api/founder/v/%s/page' % V, payload, uid=C_ID)
        db.session.refresh(page)
        check('edits save', code == 200 and page.ask_amount == 1500000 and len(page.traction) == 2, body)
        check('founder-written fields become founder-stated',
              {'solution', 'customer', 'business_model', 'market_method'} <= set(page.founder_stated))
        check('the evidence-backed problem stays evidence-backed', 'problem' not in page.founder_stated)
        code, body = api('post', '/api/founder/v/%s/page' % V,
                         {'traction': [{'label': 'Only a label'}]}, uid=C_ID)
        check('a metric without a value is refused', code == 400, body)
        code, body = api('post', '/api/founder/v/%s/page' % V, {'slug': 'dockwise-hq'}, uid=C_ID)
        db.session.refresh(page)
        check('the slug can be renamed', code == 200 and page.slug == 'dockwise-hq', body)
        check('... and the old slug is kept in history',
              StartupSlugHistory.query.filter_by(old_slug='dockwise', startup_page_id=page.id).count() == 1)
        try:
            sps.set_slug(p2, 'dockwise')
            check('another page cannot take an old slug', False)
        except sps.PageError:
            check('another page cannot take an old slug', True)
        code, _ = api('post', '/api/founder/v/%s/page' % V, {'name': 'x'}, uid=X_ID)
        check('a non-member gets 403 from the editor API', code == 403, code)

        print('\n5. Publish, public view, redirects, flag')
        code, body = api('post', '/api/founder/v/%s/page/publish' % V, {'action': 'publish'}, uid=C_ID)
        db.session.refresh(page)
        check('the right holder publishes with a clear name', code == 200 and page.status == 'published'
              and page.publish_approved_by == C_ID, body)
        login(None)
        sent.clear()
        r = client.get('/startup/dockwise-hq', headers={'User-Agent': 'Mozilla/5.0 test', 'Referer': 'https://news.example.com/x'})
        html = r.get_data(as_text=True)
        check('the published page renders', r.status_code == 200, r.status_code)
        check('memo header: name, stage, as-of date', 'Dockwise' in html and 'Seed' in html and 'As of' in html)
        check('founder-stated labels render', html.count('founder-stated') >= 4)
        check('the claims note (legal text) renders', 'Items marked founder-stated' in html)
        check('no ask in the public HTML', 'SECRET-USE-OF-FUNDS' not in html and '1,500,000' not in html
              and '1500000' not in html)
        check('hidden traction metrics are not shown', 'Hidden metric' not in html and 'Pilot customers' in html)
        check('traction notes (detail) stay out of the public view', 'NOTE-DETAIL' not in html)
        check('the public view offers no data-room request', 'value="data_room"' not in html
              and 'Data room access' not in html
              and list(sps.public_context(page)['request_kinds']) == ['intro'])
        check('the evidence ladder shows 5 rungs', html.count('class="st-rung') == 5)
        check('the benefits case and permitted quote render', 'Wait time fell' in html
              and 'We stopped paying detention fees.' in html)
        check('an unpermitted quote is not shown', 'SECRET UNPERMITTED QUOTE' not in html)
        check('the customer is not named with the quote', 'Acme Logistics' not in html)
        check('demo video embedded from the allowlist', 'youtube-nocookie.com/embed/dQw4w9WgXcQ' in html)
        check('team links to a published bio', 'href="/u/olive"' in html)
        check('team does not link to an unpublished bio', '/u/cora' not in html and 'Cora Cofounder' in html)
        check('founders\' emails are not on the page', 'owner@x.com' not in html and 'cof@x.com' not in html)
        check('OG and canonical tags', 'property="og:title"' in html and 'rel="canonical"' in html
              and '/startup/dockwise-hq' in html)
        check('favicon partial and "Made with SimiSI" footer', 'favicon-32.png' in html and 'Made with' in html
              and 'ss-logo' in html)
        check('public context carries no ask keys',
              not {'ask', 'ask_round', 'ask_amount', 'use_of_funds'} & set(sps.public_context(page)))
        check('a visit was recorded', StartupPageVisit.query.filter_by(startup_page_id=page.id).count() == 1)
        vis = StartupPageVisit.query.filter_by(startup_page_id=page.id).first()
        check('... with a hashed visitor, not an IP', vis.visitor_hash and len(vis.visitor_hash) == 32
              and '127.0.0.1' not in vis.visitor_hash and vis.referrer.startswith('https://news.example.com'))
        client.get('/startup/dockwise-hq', headers={'User-Agent': 'Googlebot/2.1'})
        check('bots are not counted', StartupPageVisit.query.filter_by(startup_page_id=page.id).count() == 1)
        login(C_ID)
        client.get('/startup/dockwise-hq')
        check('founders\' own visits are not counted',
              StartupPageVisit.query.filter_by(startup_page_id=page.id).count() == 1)
        login(None)
        r = client.get('/startup/dockwise')
        check('an old slug 301s to the current one', r.status_code == 301
              and r.headers['Location'].endswith('/startup/dockwise-hq'), (r.status_code, r.headers.get('Location')))
        r = client.get('/startup/DOCKWISE-HQ')
        check('slugs are case-insensitive', r.status_code == 200)
        check('an unknown slug 404s', client.get('/startup/no-such-startup').status_code == 404)
        stats = sps.analytics(page)
        check('analytics: views over time (two counted views)', stats['views'] == 2 and len(stats['series']) == 30
              and stats['series'][-1]['views'] == 2 and ('news.example.com', 1) in stats['referrers'], stats)
        code, body = api('get', '/api/founder/v/%s/page/analytics' % V, uid=O_ID)
        check('analytics API for members', code == 200 and body.get('views') == 2, body.get('views'))
        login(O_ID)
        eh = client.get('/founder/v/%s/page' % V).get_data(as_text=True)
        check('the editor shows views and status', 'Page views' in eh and 'published' in eh)
        pv = client.get('/founder/v/%s/page/preview' % V)
        check('the preview renders for members with the preview banner', pv.status_code == 200
              and 'Preview.' in pv.get_data(as_text=True) and 'noindex' in pv.get_data(as_text=True))
        check('the preview links back to the editor (opened in a new tab)',
              'href="/founder/v/%s/page"' % V in pv.get_data(as_text=True)
              and 'history.back' not in pv.get_data(as_text=True))
        login(X_ID)
        check('a non-member cannot preview', client.get('/founder/v/%s/page/preview' % V).status_code == 403)

        founder_feature.set_mode('founder_track', 'off', A_ID)
        login(None)
        check('flag off: the published page 404s', client.get('/startup/dockwise-hq').status_code == 404)
        check('flag off: old slugs 404 too', client.get('/startup/dockwise').status_code == 404)
        check('flag off: requests 404', client.post('/startup/dockwise-hq/request', json={}).status_code == 404)
        sm = client.get('/sitemap.xml').get_data(as_text=True)
        check('flag off: no startup pages in the sitemap', '/startup/' not in sm)
        founder_feature.set_mode('founder_track', 'on', A_ID)

        print('\n6. Sitemap and robots')
        sm = client.get('/sitemap.xml')
        body_sm = sm.get_data(as_text=True)
        check('sitemap still serves XML', sm.status_code == 200 and body_sm.strip().endswith('</urlset>'))
        check('sitemap lists the published page', '/startup/dockwise-hq</loc>' in body_sm)
        check('sitemap leaves out drafts', '/startup/dockwise-2' not in body_sm)
        rb = client.get('/robots.txt').get_data(as_text=True)
        for line in ('Disallow: /startup/*/i/', 'Disallow: /startup/*/signin/', 'Disallow: /founder/',
                     'Disallow: /u/*/edit', 'Disallow: /admin', 'Sitemap: '):
            check('robots has %r' % line, line in rb)

        print('\n7. Intro and data-room requests')
        sent.clear()
        good = {'kind': 'intro', 'name': 'Ivy Investor', 'email': 'ivy@fund.vc', 'firm': 'Ivy Fund',
                'message': 'Would love an intro.'}
        r = client.post('/startup/dockwise-hq/request', json=good)
        check('a valid request is accepted', r.status_code == 200, r.get_json())
        c = Contact.query.filter_by(user_id=O_ID, email='ivy@fund.vc').first()
        check('it is stored as a Contact of the venture owner', c is not None and c.source == 'startup_page'
              and c.company_name == 'Ivy Fund' and c.first_name == 'Ivy')
        check('it is audit-logged', AuditLog.query.filter_by(action='startup_page_request').count() == 1)
        check('both founders are emailed', {'owner@x.com', 'cof@x.com'} <= {x for s in sent for x in s[1]}
              and any('Ivy Investor' in s[2] for s in sent))
        n_sent = len(sent)
        r = client.post('/startup/dockwise-hq/request', data=dict(good, kind='data_room'))
        check('a repeat form-encoded request is accepted and does not duplicate the contact',
              r.status_code == 200 and Contact.query.filter_by(user_id=O_ID, email='ivy@fund.vc').count() == 1)
        check('... nor email the founders again (per-email dedupe)', len(sent) == n_sent, len(sent) - n_sent)
        aud = [a.extra for a in AuditLog.query.filter_by(action='startup_page_request').all()]
        check('the public view cannot file a data-room request', all(a.get('kind') == 'intro' for a in aud), aud)
        r = client.post('/startup/dockwise-hq/request', json=dict(good, email='z@fund.vc', kind=['data_room']))
        check('a non-string field is a 400, not a 500', r.status_code == 400, r.status_code)
        r = client.post('/startup/dockwise-hq/request', json=dict(good, email='z@fund.vc', website=5))
        check('a non-string honeypot is a 400, not a 500', r.status_code == 400, r.status_code)
        for bad, label in (({'name': '', 'email': 'a@b.co'}, 'missing name'),
                           ({'name': 'Win money http://spam.ly', 'email': 'a@b.co'}, 'a spam name'),
                           ({'name': 'Real Person', 'email': 'not-an-email'}, 'a bad email'),
                           ({'name': 'Real Person', 'email': 'x@mailinator.com'}, 'a disposable email'),
                           ({'name': 'Real Person', 'email': 'a@b.co', 'message': 'x' * 2001}, 'a long message')):
            r = client.post('/startup/dockwise-hq/request', json=bad)
            check('refuses %s (400)' % label, r.status_code == 400 and r.get_json().get('error'), r.status_code)
        n_contacts = Contact.query.count()
        r = client.post('/startup/dockwise-hq/request', json=dict(good, email='bot@x.co', website='http://x'))
        check('the honeypot accepts quietly and stores nothing', r.status_code == 200
              and Contact.query.count() == n_contacts)
        check('requests to an unpublished page 404', client.post('/startup/dockwise-2/request', json=good).status_code
              == 404)
        codes = [client.post('/startup/dockwise-hq/request', json=dict(good, email='n%d@fund.vc' % i)).status_code
                 for i in range(6)]
        check('per-IP limit returns 429', 429 in codes and codes.count(200) == sps.request_limit() - 2, codes)
        r = client.post('/startup/dockwise-hq/request', json=dict(good, email='other@fund.vc'),
                        environ_base={'REMOTE_ADDR': '10.9.8.7'})
        check('another IP is not limited', r.status_code == 200, r.status_code)
        r = client.post('/startup/dockwise-hq/request', json=dict(good, email='spoof@fund.vc'),
                        headers={'X-Forwarded-For': '1.2.3.4, 127.0.0.1'})
        check('a spoofed X-Forwarded-For first hop does not reset the limit', r.status_code == 429, r.status_code)
        import hashlib
        peer_hash = hashlib.sha256(b'8.8.4.4').hexdigest()[:32]
        r = client.post('/startup/dockwise-hq/request', json=dict(good, email='spoof2@fund.vc'),
                        headers={'X-Forwarded-For': '1.2.3.4'}, environ_base={'REMOTE_ADDR': '8.8.4.4'})
        check('X-Forwarded-For from a public peer is ignored (keyed on the peer)', r.status_code == 200
              and AuditLog.query.filter(AuditLog.action == 'startup_page_request',
                                        AuditLog._metadata.like('%' + peer_hash + '%')).count() == 1,
              [a._metadata for a in AuditLog.query.filter_by(action='startup_page_request').all()][-2:])
        # The cap comes from founder_config (fnd_startup_page_request_limit); pin it low for this check.
        keep = sps.page_request_limit
        sps.page_request_limit = lambda: 1
        try:
            r = client.post('/startup/dockwise-hq/request', json=dict(good, email='cap@fund.vc'),
                            environ_base={'REMOTE_ADDR': '9.9.9.9'})
        finally:
            sps.page_request_limit = keep
        check('a per-page hourly cap applies across IPs', r.status_code == 429
              and 'This page' in (r.get_json() or {}).get('error', ''), r.status_code)

        print('\n8. Unpublish')
        code, body = api('post', '/api/founder/v/%s/page/publish' % V, {'action': 'unpublish'}, uid=O_ID)
        db.session.refresh(page)
        check('any founder can unpublish', code == 200 and page.status == 'unpublished', body)
        login(None)
        check('an unpublished page 404s', client.get('/startup/dockwise-hq').status_code == 404)
        check('... and leaves the sitemap', '/startup/dockwise-hq' not in client.get('/sitemap.xml').get_data(True))

        print('\n9. Rules that must not change')
        check('profile._RESERVED is untouched', set(getattr(_prof, '_RESERVED', set())) == before_reserved
              and 'onepager' not in before_reserved)
        tpl = io.open(os.path.join(ROOT, 'app/templates/startup/page.html'), encoding='utf-8').read()
        check('the startup page does not use the public footer', 'partials/_footer_public.html' not in tpl)
        check('the template has investor extension blocks', all(b in tpl for b in (
            'block st_ask', 'block st_investor_extra', 'block st_contact', 'block st_banner')))
        import re
        css = io.open(os.path.join(ROOT, 'app/static/css/startup.css'), encoding='utf-8').read()
        css = re.sub(r'(?s)/\*.*?\*/', '', css)
        sels = [s.strip() for s in re.findall(r'(?m)^[ \t]*([^@{}/\s][^{}]*)\{', css)]
        unscoped = [s for s in sels for part in s.split(',') if not part.strip().startswith('.st-root')
                    and not part.strip().startswith(('from', 'to'))]
        check('every startup.css rule is scoped under .st-root', not unscoped, unscoped[:5])

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED:', FAIL)
        sys.exit(1)


if __name__ == '__main__':
    main()
