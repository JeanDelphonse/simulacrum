"""Founder Track foundation (FOUNDER_PLAN.md WP0).

Switches and their defaults, the counsel gate on founder_investor, admin
settings and pricing, venture creation, the founder agent catalog, the founder
tables, the stream and the Python 3.9 import rule. In-memory SQLite; requests
is blocked.

    python test_founder_foundation.py
"""
import io
import json
import os
import sys
from datetime import datetime

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = [
    'app/models/founder.py',
    'app/services/founder_feature.py',
    'app/services/founder_config.py',
    'app/services/founder_ventures.py',
    'app/services/founder_registry.py',
    'app/services/founder_stream.py',
    'app/blueprints/founder/__init__.py',
    'app/blueprints/founder/common.py',
    'app/blueprints/founder/landing.py',
    'app/blueprints/startup/__init__.py',
    'app/blueprints/startup/routes.py',
]

TABLES = ['founder_venture', 'venture_member', 'venture_artifact', 'founder_run_meta', 'evaluation_run',
          'evaluation_finding', 'finding_rating', 'founder_eval_cache', 'agent_blueprint', 'agent_param_override',
          'venture_plan_step', 'venture_step_event', 'venture_agent_output', 'proof_plan', 'proof_result',
          'alignment_response', 'decision_right', 'startup_page', 'startup_slug_history', 'startup_page_visit',
          'compliance_profile', 'investor_link', 'investor_access', 'investor_update', 'investor_profile',
          'investor_target', 'intro_path', 'venture_outreach_action', 'founder_alert_rule']


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def raises(fn, exc=Exception):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))

    print('\n1. New files are Python 3.9 safe')
    missing = [f for f in NEW_PY
               if 'from __future__ import annotations' not in io.open(os.path.join(ROOT, f), encoding='utf-8').read()]
    check('every new .py file has "from __future__ import annotations"', not missing, missing)

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)
    check('upload cap is 20 MB', app.config.get('MAX_CONTENT_LENGTH') == 20 * 1024 * 1024,
          app.config.get('MAX_CONTENT_LENGTH'))

    with app.app_context():
        print('\n2. Founder tables')
        db.create_all()
        have = set(db.inspect(db.engine).get_table_names())
        check('create_all builds all %d founder tables' % len(TABLES), set(TABLES) <= have, set(TABLES) - have)
        from app.models import founder as F
        from app.models import FounderVenture as FV2
        check('models are exported from app.models', FV2 is F.FounderVenture)
        pk = F.FounderVenture.__table__.c.id.default.arg
        check('primary keys come from generate_pk', getattr(pk, '__name__', '') == 'generate_pk'
              or 'generate_pk' in repr(pk))
        bad_cols = [(t, c) for t in TABLES for c in db.metadata.tables[t].columns.keys()
                    if c in ('session_id', 'run_id', 'step_id')]
        check('no session_id / run_id / step_id columns', not bad_cols, bad_cols)

        from flask import g
        from app.models.user import User
        from app.services import founder_config, founder_feature, founder_registry, founder_stream, \
            founder_ventures, legal_text
        now = datetime.utcnow()

        def user(email, admin=False, onboarded=True):
            u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x',
                     is_admin=admin, email_verified=True, onboarding_completed_at=now if onboarded else None)
            db.session.add(u)
            db.session.commit()
            return u

        founder = user('founder@x.com')
        admin = user('admin@x.com', admin=True)
        other = user('other@x.com')
        fresh = user('fresh@x.com', onboarded=False)
        F_ID, A_ID, O_ID, N_ID = founder.id, admin.id, other.id, fresh.id

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

        def status(path, uid):
            login(uid)
            return client.get(path).status_code

        print('\n3. Switch defaults: the track is off')
        check('founder_track defaults to off', founder_feature.mode() == 'off')
        check('founder_pitch defaults to on', founder_feature.mode('founder_pitch') == 'on')
        check('founder_investor defaults to off', founder_feature.mode('founder_investor') == 'off')
        check('a member gets 404 at /founder', status('/founder', F_ID) == 404)
        check('an admin gets 404 at /founder while the track is off', status('/founder', A_ID) == 404)
        check('anonymous gets 404 at /founder', status('/founder', None) == 404)
        check('/startup/<slug> is 404 while off', status('/startup/acme', None) == 404)
        check('public startup pages are off', founder_feature.public_pages_enabled() is False)
        check('is_enabled is False for everyone', not any(founder_feature.is_enabled(db.session.get(User, u))
                                                         for u in (F_ID, A_ID, O_ID)))
        check('sub-switches need the track (pitch is on, track off -> pitch disabled)',
              not founder_feature.is_enabled(db.session.get(User, A_ID), 'founder_pitch'))

        print('\n4. Allowlist: admins pass, members need an override')
        founder_feature.set_mode('founder_track', 'allowlist', A_ID)
        check('mode is allowlist', founder_feature.mode() == 'allowlist')
        r_status = status('/founder', A_ID)
        check('admin bypass: /founder renders for an admin', r_status == 200, r_status)
        login(A_ID)
        body = client.get('/founder').get_data(as_text=True)
        check('the intro page says Founder Track and extends base.html', 'Founder Track' in body and 'ss-nav' in body)
        check('the intro page carries the report disclaimer', legal_text.get('founder_report_disclaimer')[:40] in body)
        check('the intro page shows the configured price', '$499' in body)
        check('a member without an override gets 404', status('/founder', F_ID) == 404)
        founder_feature.set_user_access([F_ID], 'allowed', A_ID)
        check('an allowed member gets the page', status('/founder', F_ID) == 200)
        founder_feature.set_user_access([F_ID], 'blocked', A_ID)
        check('a blocked member gets 404', status('/founder', F_ID) == 404)
        founder_feature.set_mode('founder_track', 'on', A_ID)
        check('mode on: other members get the page', status('/founder', O_ID) == 200)
        check('mode on: a blocked member still gets 404', status('/founder', F_ID) == 404)
        founder_feature.set_user_access([F_ID], 'follow_global', A_ID)
        check('public startup pages follow the track', founder_feature.public_pages_enabled() is True)
        check('/startup/<slug> is 404 until a page exists (WP6)', status('/startup/acme', None) == 404)
        s_code = status('/startup/acme', N_ID)
        check('a half-onboarded member is not redirected to onboarding from /startup/', s_code == 404, s_code)
        login(N_ID)
        r = client.get('/founder/join/abc')
        check('/founder/join/ is not sent to onboarding', r.status_code != 302
              or '/onboarding' not in r.headers.get('Location', ''), r.status_code)
        check('bad switch values are refused', raises(lambda: founder_feature.set_mode('founder_track', 'maybe', A_ID),
                                                      founder_feature.FounderError)
              and raises(lambda: founder_feature.set_mode('nope', 'on', A_ID), founder_feature.FounderError))

        print('\n5. founder_investor needs the counsel sign-off')
        check('founder_investor refuses "on" without a sign-off date',
              raises(lambda: founder_feature.set_mode('founder_investor', 'on', A_ID), founder_feature.FounderError))
        check('... and "allowlist" too',
              raises(lambda: founder_feature.set_mode('founder_investor', 'allowlist', A_ID),
                     founder_feature.FounderError))
        check('still off', founder_feature.mode('founder_investor') == 'off')
        check('"off" is always allowed', not raises(lambda: founder_feature.set_mode('founder_investor', 'off', A_ID)))
        founder_config.set('fnd_counsel_signoff_at', '2026-11-01', A_ID)
        check('after the sign-off date is recorded, it can turn on',
              not raises(lambda: founder_feature.set_mode('founder_investor', 'allowlist', A_ID))
              and founder_feature.mode('founder_investor') == 'allowlist')
        check('investor connection is enabled for an admin once signed off',
              founder_feature.is_enabled(db.session.get(User, A_ID), 'founder_investor'))
        check('the sign-off date cannot be cleared while investor connection is on',
              raises(lambda: founder_config.set('fnd_counsel_signoff_at', '', A_ID), founder_config.FounderConfigError)
              and founder_config.counsel_signed_off())
        from app.models.platform_settings import PlatformSetting as _PS
        _PS.set('fnd_counsel_signoff_at', '')        # cleared behind the service's back
        check('without a sign-off date, is_enabled(founder_investor) is False even with the switch on',
              not founder_feature.is_enabled(db.session.get(User, A_ID), 'founder_investor'))
        founder_feature.set_mode('founder_investor', mode='off', admin_id=A_ID)
        check('set_mode takes mode= / admin_id= by keyword (section 5 signature)',
              founder_feature.mode('founder_investor') == 'off')
        check('with the switch off, the sign-off date can be cleared',
              not raises(lambda: founder_config.set('fnd_counsel_signoff_at', '', A_ID)))
        founder_config.set('fnd_counsel_signoff_at', '2026-11-01', A_ID)
        founder_feature.set_mode('founder_investor', new_mode='allowlist', admin_id=A_ID)
        check('set_mode still accepts new_mode=', founder_feature.mode('founder_investor') == 'allowlist')

        print('\n6. Settings: typed, validated, with defaults')
        p = founder_config.pricing()
        check('default prices 49900 / 29900 / 4900, refund 14 days, one_time',
              (p['price_cents'], p['addon_price_cents'], p['investor_access_cents'], p['refund_window_days'],
               p['fee_mode']) == (49900, 29900, 4900, 14, 'one_time'), p)
        check('every key starts with fnd_', all(k.startswith('fnd_') for k in founder_config.DEFAULTS))
        check('defaults: gates 2 / 4, alternates 3, preview scores',
              (founder_config.get('fnd_gate_rung_applications'), founder_config.get('fnd_gate_rung_intros'),
               founder_config.get('fnd_next_alternates'), founder_config.get('fnd_preview_level'))
              == (2, 4, 3, 'scores'))
        w = founder_config.get('fnd_rubric_weights')
        check('rubric weights: nine equal dimensions', len(w) == 9 and len(set(w.values())) == 1, w)
        m = founder_config.get('fnd_autonomy_matrix')
        check('autonomy matrix: first contact 2 (max 3), financial 2 (max 2), negotiation not permitted',
              m['first_contact'] == {'default': 2, 'max': 3} and m['financial_message'] == {'default': 2, 'max': 2}
              and m['negotiate_terms'] == {'default': 0, 'max': 0})
        check('targets cover the seven PRD metrics', set(founder_config.get('fnd_targets')) ==
              set(founder_config.TARGET_KEYS) and len(founder_config.TARGET_KEYS) == 7)
        E = founder_config.FounderConfigError
        bad = [('fnd_price_cents', -1), ('fnd_price_cents', 'abc'), ('fnd_price_cents', True),
               ('fnd_fee_mode', 'recurring'), ('fnd_preview_level', 'all'), ('fnd_gate_rung_intros', 6),
               ('fnd_autonomy_matrix', {'first_contact': {'default': 4, 'max': 3}}),
               ('fnd_autonomy_matrix', {'negotiate_terms': {'default': 1, 'max': 1}}),
               ('fnd_autonomy_matrix', {'wire_money': {'default': 1, 'max': 1}}),
               ('fnd_rubric_weights', {'vibes': 1}), ('fnd_rubric_weights', '{not json'),
               ('fnd_targets', {'finding_accuracy': 140}), ('fnd_counsel_signoff_at', 'soon'),
               ('fnd_unknown', 1)]
        let_through = [(k, v) for k, v in bad if not raises(lambda k=k, v=v: founder_config.set(k, v, A_ID), E)]
        check('invalid values are refused (%d cases)' % len(bad), not let_through, let_through)
        founder_config.set('fnd_price_cents', '39900', A_ID)
        check('a valid value is stored and read back typed', founder_config.get('fnd_price_cents') == 39900)
        check('pricing() follows the setting', founder_config.pricing()['price_cents'] == 39900)
        founder_config.set('fnd_autonomy_matrix', json.dumps({'research': {'default': 3}}), A_ID)
        m2 = founder_config.get('fnd_autonomy_matrix')
        check('a partial matrix keeps the other rows', m2['research'] == {'default': 3, 'max': 4}
              and m2['first_contact'] == {'default': 2, 'max': 3})
        from app.models.platform_settings import PlatformSetting
        PlatformSetting.set('fnd_next_alternates', 'garbage')
        check('a corrupt stored value reads as the default', founder_config.get('fnd_next_alternates') == 3)
        founder_config.set('fnd_price_cents', 49900, A_ID)

        print('\n7. Ventures')
        fu = db.session.get(User, F_ID)
        v = founder_ventures.create_venture(fu, '  Acme   Robotics ', 'Robots that pick strawberries', 'pre-seed')
        check('create_venture returns the venture', v is not None and v.name == 'Acme Robotics'
              and v.stage == 'pre-seed' and v.rung == 1 and v.status == 'active')
        check('the id is a 9-character uppercase key', len(v.id) == 9 and v.id == v.id.upper(), v.id)
        mem = founder_ventures.get_member(v, F_ID)
        check('the owner is an active member with role owner', mem is not None and mem.role == 'owner'
              and mem.status == 'active')
        rights = F.DecisionRight.query.filter_by(venture_id=v.id).all()
        check('default decision rights: the owner holds all seven types',
              sorted(r.decision_type for r in rights) == sorted(F.DecisionRight.DECISION_TYPES)
              and all(r.holder_member_id == mem.id for r in rights), len(rights))
        from app.models.audit_log import AuditLog
        check('creation is audit-logged', AuditLog.query.filter_by(action='founder_venture_created',
                                                                   resource_id=v.id).count() == 1)
        check('one owned venture per account',
              raises(lambda: founder_ventures.create_venture(fu, 'Second', '', 'idea'), founder_ventures.VentureError))
        real_vfo = founder_ventures.venture_for_owner
        founder_ventures.venture_for_owner = lambda uid: None     # both requests pass the check (race)
        try:
            raced = raises(lambda: founder_ventures.create_venture(fu, 'Twin', '', 'idea'),
                           founder_ventures.VentureError)
        finally:
            founder_ventures.venture_for_owner = real_vfo
        check('a racing second create hits the UNIQUE key and becomes VentureError, not a 500', raced)
        check('... and the session is usable afterwards', founder_ventures.venture_for_owner(F_ID).id == v.id)
        check('bad stage / empty name refused',
              raises(lambda: founder_ventures.create_venture(db.session.get(User, O_ID), 'X', '', 'unicorn'),
                     founder_ventures.VentureError)
              and raises(lambda: founder_ventures.create_venture(db.session.get(User, O_ID), '  ', '', 'idea'),
                         founder_ventures.VentureError))
        check('venture_for_owner / ventures_for_user', founder_ventures.venture_for_owner(F_ID).id == v.id
              and [x.id for x in founder_ventures.ventures_for_user(F_ID)] == [v.id]
              and founder_ventures.ventures_for_user(O_ID) == [])
        check('is_paid is False until paid', founder_ventures.is_paid(v) is False)
        from werkzeug.exceptions import Forbidden, NotFound
        with app.test_request_context():
            check('require_member: the owner passes', founder_ventures.require_member(v.id, fu).id == v.id)
            check('require_member: a non-member gets 403',
                  raises(lambda: founder_ventures.require_member(v.id, db.session.get(User, O_ID)), Forbidden))
            check('require_member: an unknown venture gets 404',
                  raises(lambda: founder_ventures.require_member('NOPE00000', fu), NotFound))
        db.session.add(F.VentureMember(venture_id=v.id, user_id=O_ID, role='cofounder', status='active',
                                       joined_at=now))
        db.session.commit()
        check('an active co-founder sees the venture', [x.id for x in founder_ventures.ventures_for_user(O_ID)]
              == [v.id])
        login(F_ID)
        check('the intro page lists the venture', 'Acme Robotics' in client.get('/founder').get_data(as_text=True))

        print('\n8. Stream')
        gen = founder_stream.generate(v.id, max_seconds=30, sleep=lambda s: None)
        first = next(gen)
        check('the stream opens with a keepalive', first == ': keepalive\n\n', first[:60])
        founder_stream.publish(v.id, 'step_ready', {'plan_step_id': 'STEP00001', 'title': 'Problem Statement',
                                                     'actor_kind': 'agent'})
        rollbacks = []
        real_rollback = db.session.rollback
        db.session.rollback = lambda: (rollbacks.append(1), real_rollback())[1]
        try:
            nxt = next(gen)
        finally:
            db.session.rollback = real_rollback
        check('a published event reaches the open stream', 'step_ready' in nxt and 'Problem Statement' in nxt,
              nxt[:60].replace('\n', ' '))
        check('each poll ends its read transaction (fresh snapshot on MySQL)', len(rollbacks) >= 1)
        ev = F.VentureStepEvent.query.filter_by(venture_id=v.id).order_by(F.VentureStepEvent.created_at.desc()).first()
        check('publish writes a venture_step_event row', ev is not None and ev.plan_step_id == 'STEP00001'
              and ev.actor_kind == 'agent')
        check('a non-transition event leaves to_state empty (the name is in the note)',
              ev.to_state is None and json.loads(ev.note).get('event') == 'step_ready', ev.to_state)
        ev2 = founder_stream.publish(v.id, 'step_changed', {'plan_step_id': 'STEP00001', 'from_state': 'ready',
                                                           'to_state': 'in_progress'})
        check('a real transition keeps from_state / to_state', (ev2.from_state, ev2.to_state) == ('ready', 'in_progress'))
        nxt2 = next(gen)
        if nxt2 == ': keepalive\n\n':      # the keepalive that closes the previous poll
            nxt2 = next(gen)
        check('the transition event is streamed by name', 'event: step_changed' in nxt2, nxt2[:60])
        gen.close()

        print('\n9. Founder agent catalog')
        agents = founder_registry.all_agents()
        keys = [a['action_type'] for a in agents]
        check('27 agents with unique fnd_ keys', len(agents) == 27 and len(set(keys)) == 27
              and all(k.startswith('fnd_') for k in keys))
        counts = [len(founder_registry.by_layer(n)) for n in range(1, 6)]
        check('five layers, split 5 / 5 / 6 / 4 / 7', counts == [5, 5, 6, 4, 7], counts)
        check('layer names', founder_registry.LAYER_NAMES ==
              {1: 'Validate', 2: 'Build', 3: 'Sell', 4: 'Operate', 5: 'Capitalize'})
        wealth = json.load(io.open(os.path.join(ROOT, 'config', 'agents.json'), encoding='utf-8'))
        wkeys = {a['action_type'] for a in wealth['agents']} | set(wealth.get('aliases', {})) \
            | set(wealth.get('aliases', {}).values())
        check('no overlap with config/agents.json keys or aliases', not (set(keys) & wkeys), set(keys) & wkeys)
        check('config/agents.json still has 49 agents', len(wealth['agents']) == 49)
        from app.services import agent_registry
        reg_keys = {a['action_type'] for a in agent_registry.get_all_agents()}
        check('the wealth registry is unchanged: 49 agents, none of them fnd_',
              len(reg_keys) == 49 and not any(k.startswith('fnd_') for k in reg_keys), len(reg_keys))
        req = {'action_type', 'track', 'layer', 'label', 'description', 'model', 'prerequisites', 'prompt_form',
               'output_spec', 'effort', 'external', 'action_type_class', 'default_params', 'prompt', 'dimensions'}
        incomplete = [a['action_type'] for a in agents if not req <= set(a)]
        check('every agent has the catalog fields', not incomplete, incomplete)
        check('every agent is track founder, effort 1-5, external none/customer/investor',
              all(a['track'] == 'founder' and 1 <= a['effort'] <= 5 and a['external'] in founder_registry.EXTERNAL_KINDS
                  for a in agents))
        check('prerequisites name founder agents only', all(set(a['prerequisites']) <= set(keys) for a in agents))
        prereq = {a['action_type']: list(a['prerequisites']) for a in agents}
        state, cycle = {}, []

        def visit(k, path):
            if state.get(k) == 'done' or cycle:
                return
            if state.get(k) == 'open':
                cycle.extend(path + [k])
                return
            state[k] = 'open'
            for p in prereq.get(k, []):
                visit(p, path + [k])
            state[k] = 'done'
        for k in keys:
            visit(k, [])
        check('prerequisites form a DAG (no cycles)', not cycle, cycle)
        import re as _re
        phr = json.load(io.open(os.path.join(ROOT, 'config', 'compliance_phrases.json'), encoding='utf-8'))
        cat_text = ' '.join(io.open(os.path.join(ROOT, 'config', 'founder_agents.json'), encoding='utf-8')
                            .read().lower().split())
        for ok_ctx in phr.get('allowed_contexts', []):
            cat_text = cat_text.replace(ok_ctx, ' ')
        hits = [p for p in phr['banned'] + phr.get('review', [])
                if _re.search(r'(?<![a-z])' + _re.escape(p.lower()) + r'(?![a-z])', cat_text)]
        check('config/founder_agents.json has no banned or review compliance phrases', not hits, hits)
        external_research = [a['action_type'] for a in agents
                             if a['external'] in ('customer', 'investor') and a['action_type_class'] == 'research']
        check('customer- or investor-facing agents are never classed as research', not external_research,
              external_research)
        over_default = [a['action_type'] for a in agents if a['external'] in ('customer', 'investor')
                        and a['default_params']['autonomy_level'] >
                        founder_config.DEFAULTS['fnd_autonomy_matrix'][a['action_type_class']]['default']]
        check('external agents never default above the matrix default', not over_default, over_default)
        dim_ref, by_key_ref = founder_registry.DIMENSION_AGENTS, founder_registry._BY_KEY
        founder_registry.reload()
        check('reload() updates DIMENSION_AGENTS in place (imported names stay current)',
              founder_registry.DIMENSION_AGENTS is dim_ref and founder_registry._BY_KEY is by_key_ref
              and dim_ref and len(by_key_ref) == 27)
        check('every action_type_class is an autonomy matrix row',
              all(a['action_type_class'] in founder_config.DEFAULTS['fnd_autonomy_matrix'] for a in agents))
        check('default_params carry exactly PARAM_KEYS',
              all(set(a['default_params']) == set(founder_registry.PARAM_KEYS) for a in agents))
        check('default autonomy never exceeds the matrix maximum',
              all(a['default_params']['autonomy_level'] <=
                  founder_config.DEFAULTS['fnd_autonomy_matrix'][a['action_type_class']]['max'] for a in agents))
        check('every rubric dimension has candidate agents',
              all(founder_registry.DIMENSION_AGENTS.get(d) for d in founder_config.DIMENSIONS),
              founder_registry.DIMENSION_AGENTS)
        check('get() and default_params()', founder_registry.get('fnd_pilot_offer')['layer'] == 3
              and founder_registry.get('nope') is None
              and founder_registry.default_params('fnd_warm_intro')['autonomy_level'] == 3)
        rules = ' '.join(founder_registry.rules()).lower()
        check('shared rules ask for citations, confidence, no advice, no return projections',
              all(w in rules for w in ('cite', 'confidence', 'no legal, tax', 'never project investor returns',
                                       'never negotiate')))

        print('\n10. Legal text')
        for k in ('founder_report_disclaimer', 'founder_not_advice', 'startup_claims_note', 'investor_update_footer'):
            check('legal text %s exists' % k, k in legal_text.DEFAULTS and legal_text.get(k))
        check('the report disclaimer says scores are not a funding prediction',
              'not a prediction of funding' in legal_text.get('founder_report_disclaimer'))

        print('\n11. The 20 MB cap leaves the 5 MB resume limits as they were')
        big = b'%PDF-1.4\n' + b'0' * (6 * 1024 * 1024)
        login(None)
        r = client.post('/api/careers/apply', data={
            'job_slug': 'growth-marketing-lead', 'full_name': 'Jane Applicant', 'email': 'jane@example.com',
            'phone': '+1 555 123 4567', 'resume': (io.BytesIO(big), 'resume.pdf')},
            content_type='multipart/form-data')
        check('careers: a 6 MB resume is refused with the 5 MB message', r.status_code == 413
              and 'under 5 MB' in (r.get_json() or {}).get('error', ''), (r.status_code, r.get_data(as_text=True)[:120]))
        from app.models.job_application import JobApplication
        check('... and nothing is stored', JobApplication.query.count() == 0)
        login(F_ID)
        r = client.post('/api/resumes/upload', data={
            'checkbox_1': 'true', 'checkbox_2': 'true', 'tos_version': '1.0', 'privacy_version': '1.0',
            'file': (io.BytesIO(big), 'resume.pdf')}, content_type='multipart/form-data')
        check('resumes: a 6 MB upload is refused', r.status_code == 413, (r.status_code, r.get_data(as_text=True)[:120]))

        print('\n12. Account deletion never reaches into another member\'s venture')
        import runpy
        gen_del = runpy.run_path(os.path.join(ROOT, 'sql', 'tools', 'gen_delete_user.py'), run_name='gen')
        dl = gen_del['deletes']
        check('owner-venture tables are matched by venture only, not by who started the run',
              all(not any('obs_run_id' in c for c in dl[t])
                  for t in ('founder_run_meta', 'evaluation_run', 'venture_agent_output')),
              {t: dl[t] for t in ('founder_run_meta', 'evaluation_run', 'venture_agent_output')})
        check('ratings on the deleted venture\'s findings go with it',
              any('finding_id IN' in c for c in dl['finding_rating']))
        check('a deleted member\'s alignment answers go, in any venture',
              any('member_id IN' in c for c in dl['alignment_response']))
        sql_text = gen_del['SQL_TEXT']
        check('decision rights held by a deleted co-founder go back to the owner',
              'UPDATE decision_right SET holder_member_id' in sql_text)
        check('founder-added investor profiles are deleted, admin ones kept',
              "DELETE FROM investor_profile WHERE source = 'founder' AND added_by = @uid;" in sql_text)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
