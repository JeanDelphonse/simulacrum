"""Founder Track observability, alerts, controls and member nav (FOUNDER_PLAN.md WP7).

F-layer codes pass through observability_service.layer_code while the L-code
rollups stay identical; the Founder Track tab APIs are admin-only; founder
content is masked unless the founders granted support access, and every
unmasked view is written to admin_access_audit; alerts fire (including any
send above its approved autonomy level); venture and agent pauses block
dispatch and re-runs; re-run calls founder_agent_service.execute with
trigger='rerun'; the member-nav item is hidden while the switch is off and
shown when it is on. In-memory SQLite; requests is blocked.

    python test_founder_obs_nav.py
"""
import io
import os
import sys
import types
from datetime import datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = ['app/services/founder_obs_service.py', 'app/services/founder_alerts.py',
          'app/blueprints/observability/founder_routes.py', 'app/blueprints/founder/support.py']


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

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.founder import (FounderRunMeta, FounderVenture, VentureOutreachAction, VenturePlanStep,
                                        VentureStepEvent)
        from app.models.observability import AdminAccessAudit, AgentRun, AgentRunTool, ObservabilityFlag
        from app.models.user import User
        from app.services import founder_alerts, founder_feature, founder_obs_service as fobs, founder_ventures
        from app.services import observability_service as obs
        from app.services import site_nav
        now = datetime.utcnow()

        def user(email, admin=False):
            u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x',
                     is_admin=admin, email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        admin = user('admin@x.com', admin=True)
        founder = user('founder@x.com')
        other = user('other@x.com')
        A_ID, F_ID, O_ID = admin.id, founder.id, other.id

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

        def api(method, path, body=None, uid=A_ID):
            login(uid)
            r = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
            return r.status_code, (r.get_json(silent=True) or {})

        B = '/api/admin/observability/founder'

        # ── 2. layer_code ─────────────────────────────────────────────────
        print('\n2. layer_code: F codes pass through, everything else as before')
        check('F1..F5 pass through unchanged', [obs.layer_code('F%d' % n) for n in range(1, 6)]
              == ['F1', 'F2', 'F3', 'F4', 'F5'])
        check('lower-case f3 normalises to F3', obs.layer_code('f3') == 'F3')
        check('L codes and integers are unchanged',
              [obs.layer_code(x) for x in ('L0', 'L3', 'l5', 0, 2, 6, '4')] == ['L0', 'L3', 'L5', 'L0', 'L2', 'L6', 'L6'],
              [obs.layer_code(x) for x in ('L0', 'L3', 'l5', 0, 2, 6, '4')])
        check('unknown values still read as L6', [obs.layer_code(x) for x in ('F6', 'F0', 'X1', 9, None, '')]
              == ['L6'] * 6)
        check('the overview axis is unchanged (L0-L6 only)', tuple(obs.LAYER_CODES) == ('L0', 'L1', 'L2', 'L3', 'L4', 'L5', 'L6'))

        # ── 3. Rollups unchanged by founder runs ──────────────────────────
        print('\n3. L-code rollups are identical with founder runs present')
        for key, layer, status in (('rate_card', 1, 'success'), ('rate_card', 1, 'error'),
                                   ('projections', 5, 'success'), ('layer6_orchestrator', 6, 'blocked')):
            obs.start_run(key, layer, user_id=F_ID, model='claude-haiku-4-5', dispatch_source='orchestrator')
            obs.record_usage(prompt_tokens=100, completion_tokens=200)
            obs.finish_run(status=status)
        obs.invalidate()
        before_layers = obs.layer_rollups(24)
        before_agents = {lyr: obs.agent_rollups(lyr, 24) for lyr in obs.LAYER_CODES}
        before_api = api('get', '/api/admin/observability/layers?hours=24')[1]

        v = founder_ventures.create_venture(db.session.get(User, F_ID), 'Acme Robotics', 'Robots for docks', 'seed')
        V_ID = v.id
        step = VenturePlanStep(venture_id=V_ID, layer=1, kind='agent', agent_key='fnd_market_sizing',
                               title='Size the market', state='in_progress')
        db.session.add(step)
        db.session.commit()
        STEP_ID = step.id

        def founder_run(agent_key, layer, status, cost=0, plan_step_id=None, venture_id=None, trigger='founder'):
            r = obs.start_run(agent_key, layer, user_id=F_ID, model='claude-sonnet-4-5', dispatch_source='founder')
            obs.record_usage(prompt_tokens=1000, completion_tokens=500)
            obs.finish_run(status=status)
            db.session.add(FounderRunMeta(obs_run_id=r.id, venture_id=venture_id or V_ID, plan_step_id=plan_step_id,
                                          agent_key=agent_key, trigger=trigger, cost_cents=cost))
            db.session.commit()
            return r.id

        ok_run = founder_run('fnd_market_sizing', 'F1', 'success', cost=40, plan_step_id=STEP_ID)
        failed_run = founder_run('fnd_market_sizing', 'F1', 'error', cost=10, plan_step_id=STEP_ID)
        founder_run('fnd_poc_plan', 'F2', 'success', cost=30)
        stored = {r.layer for r in AgentRun.query.filter(AgentRun.agent_key.like('fnd_%')).all()}
        check('founder runs are stored under F codes, not L6', stored == {'F1', 'F2'}, stored)

        obs.invalidate()
        check('layer_rollups are identical', obs.layer_rollups(24) == before_layers)
        check('per-layer agent_rollups are identical',
              {lyr: obs.agent_rollups(lyr, 24) for lyr in obs.LAYER_CODES} == before_agents)
        after_api = api('get', '/api/admin/observability/layers?hours=24')[1]
        check('the /layers API answers the same', after_api.get('layers') == before_api.get('layers')
              and after_api.get('totals') == before_api.get('totals'))
        l6 = [c for c in obs.layer_rollups(24) if c['layer'] == 'L6'][0]
        check('the L6 Orchestration card holds only its own run', l6['runs'] == 1, l6['runs'])

        print('\n4. Run listing: opt-in venture and F-layer filters')
        _, all_runs = api('get', '/api/admin/observability/runs?hours=24')
        check('the run list without a filter still lists every run', all_runs.get('total') == 7, all_runs.get('total'))
        _, by_v = api('get', '/api/admin/observability/runs?hours=24&venture=%s' % V_ID)
        check('?venture= narrows to that venture\'s runs', by_v.get('total') == 3
              and all(r['agent_key'].startswith('fnd_') for r in by_v['runs']), by_v.get('total'))
        _, by_f = api('get', '/api/admin/observability/runs?hours=24&layer=F2')
        check('?layer=F2 narrows to founder layer 2', by_f.get('total') == 1, by_f.get('total'))
        _, by_l1 = api('get', '/api/admin/observability/runs?hours=24&layer=L1')
        check('?layer=L1 is unchanged', by_l1.get('total') == 2, by_l1.get('total'))

        # ── 5. Admin-only tab ────────────────────────────────────────────
        print('\n5. The Founder Track tab is admin-only')
        paths = ['/meta', '/fleet', '/runs', '/ventures', '/ventures/%s/trace' % V_ID, '/autonomy', '/eval-quality',
                 '/compliance', '/connectors', '/startup-pages', '/alerts']
        check('a member gets 403 on every founder view', all(api('get', B + p, uid=F_ID)[0] == 403 for p in paths),
              [(p, api('get', B + p, uid=F_ID)[0]) for p in paths if api('get', B + p, uid=F_ID)[0] != 403])
        check('anonymous never gets 200', all(api('get', B + p, uid=None)[0] != 200 for p in paths))
        check('member POSTs (pause, re-run) are refused',
              api('post', B + '/ventures/%s/pause' % V_ID, {'paused': True}, uid=F_ID)[0] == 403
              and api('post', B + '/agents/fnd_poc_plan/pause', {'paused': True}, uid=F_ID)[0] == 403
              and api('post', B + '/runs/%s/rerun' % failed_run, {}, uid=F_ID)[0] == 403)
        bad = [(p, api('get', B + p)[0]) for p in paths if api('get', B + p)[0] != 200]
        check('an admin gets 200 on every founder view', not bad, bad)
        st, meta = api('get', B + '/meta')
        check('meta reports the switch off by default', meta.get('enabled') is False and meta.get('mode') == 'off')
        login(A_ID)
        page = client.get('/admin/observability').get_data(as_text=True)
        check('the console carries the tab hidden, plus its panel',
              'id="obFounderTab" hidden' in page and 'id="panel-founder"' in page)
        _, fleet = api('get', B + '/fleet?hours=24')
        check('fleet counts founder runs only', fleet['totals']['runs'] == 3, fleet['totals'])
        check('fleet has the five founder layers', [l['layer'] for l in fleet['layers']] == ['F1', 'F2', 'F3', 'F4', 'F5'])
        f1 = fleet['layers'][0]
        check('F1: 2 runs, 50% success, recorded cost $0.50', f1['runs'] == 2 and f1['success_rate'] == 50.0
              and f1['cost'] == 0.5 and f1['avg_latency_ms'] is not None, f1)
        from app.services import founder_registry
        catalog_keys = {a['action_type'] for a in founder_registry.all_agents()}
        ran = [a for a in fleet['agents'] if a['runs']]
        check('fleet lists the agents that ran first, with labels', {a['agent_key'] for a in ran}
              == {'fnd_market_sizing', 'fnd_poc_plan'} and fleet['agents'][:2] == ran
              and all(a['agent_label'] for a in fleet['agents']))
        check('fleet lists every catalog agent, so one can be paused before it runs',
              catalog_keys <= {a['agent_key'] for a in fleet['agents']}, len(fleet['agents']))

        # ── 6. Masking and support access ─────────────────────────────────
        print('\n6. Masking unless the founders grant support access')
        db.session.add(VentureStepEvent(venture_id=V_ID, plan_step_id=STEP_ID, from_state='ready',
                                        to_state='in_progress', actor_user_id=F_ID, actor_kind='founder',
                                        note='Spoke to Jane Roe at Harbor Logistics'))
        db.session.add(VentureOutreachAction(venture_id=V_ID, plan_step_id=STEP_ID, action_type='customer_message',
                                             level_required=2, level_applied=2, status='awaiting_approval',
                                             target_ref='Jane Roe <jane@harbor.example>',
                                             subject='Pilot proposal for Harbor', body='Hi Jane, our pilot...'))
        db.session.commit()

        def audits(action):
            return AdminAccessAudit.query.filter_by(action=action).count()

        trace_audits = audits(fobs.AUDIT_VIEW_TRACE)
        st, tr = api('get', B + '/ventures/%s/trace' % V_ID)
        items = tr.get('items', [])
        appr = [i for i in items if i['kind'] == 'approval'][0]
        stp = [i for i in items if i['kind'] == 'step'][0]
        flat = str(tr)
        check('trace shows run metadata', any(i['kind'] == 'run' and i['status'] == 'error' for i in items))
        check('message subject, body and contact are masked', appr['subject'] == fobs.MASK and appr['body'] == fobs.MASK
              and appr['target'] == fobs.MASK, appr)
        check('step notes are masked', stp['note'] == fobs.MASK, stp)
        check('no founder content leaks anywhere in the trace', 'Jane' not in flat and 'Harbor' not in flat
              and 'Robots for docks' not in flat)
        check('a masked trace view is audited as metadata-only', audits(fobs.AUDIT_VIEW_TRACE) == trace_audits + 1
              and audits(fobs.AUDIT_VIEW_UNMASKED) == 0)
        st, au = api('get', B + '/autonomy?hours=24')
        check('autonomy audit masks content too', 'Jane' not in str(au) and au['actions'][0]['level_required'] == 2)
        from app.models.agent_plan import ActionApproval
        declined = VentureOutreachAction(venture_id=V_ID, plan_step_id=STEP_ID, action_type='customer_message',
                                         level_required=2, status='blocked', block_reason="Don't contact Kim at Dockside")
        default_decl = VentureOutreachAction(venture_id=V_ID, action_type='followup', level_required=2,
                                             status='blocked', block_reason='Declined by the founder')
        db.session.add_all([declined, default_decl])
        db.session.flush()
        for a_ in (declined, default_decl):
            db.session.add(ActionApproval(target_type='fnd_outreach', target_id=a_.id, decided_by=F_ID,
                                          decision='reject'))
        db.session.commit()
        _, tr2 = api('get', B + '/ventures/%s/trace' % V_ID)
        _, au2 = api('get', B + '/autonomy?hours=24')
        _, co2 = api('get', B + '/compliance?hours=24')
        check("a founder's decline note is masked in the trace, autonomy and compliance views",
              'Kim' not in str(tr2) and 'Kim' not in str(au2) and 'Kim' not in str(co2)
              and any(r.get('block_reason') == fobs.MASK for r in au2['actions']))
        check('the stock decline reason stays readable',
              any(r.get('block_reason') == 'Declined by the founder' for r in co2['blocked_actions']))
        check('masked views of decline notes are not audited as unmasked', audits(fobs.AUDIT_VIEW_UNMASKED) == 0)
        for a_ in (declined, default_decl):     # keep the counts of the later sections as they were
            ActionApproval.query.filter_by(target_id=a_.id).delete()
            db.session.delete(a_)
        db.session.commit()

        # founder side: the switch is off -> 404
        check('support-access routes 404 while the switch is off',
              api('post', '/api/founder/v/%s/support-access' % V_ID, {'grant': True}, uid=F_ID)[0] == 404
              and api('get', '/founder/v/%s/support' % V_ID, uid=F_ID)[0] == 404)
        founder_feature.set_mode('founder_track', 'allowlist', A_ID)
        founder_feature.set_user_access([F_ID, O_ID], 'allowed', A_ID)
        check('a non-member cannot grant support access',
              api('post', '/api/founder/v/%s/support-access' % V_ID, {'grant': True}, uid=O_ID)[0] == 403)
        st, body = api('post', '/api/founder/v/%s/support-access' % V_ID, {'grant': True, 'days': 999}, uid=F_ID)
        check('grant length is bounded', st == 400 and 'days' in body.get('error', ''), body)
        st, body = api('post', '/api/founder/v/%s/support-access' % V_ID, {'grant': True, 'days': 3}, uid=F_ID)
        check('the founder grants support access', st == 200 and body.get('active') is True and body.get('until'), body)
        login(F_ID)
        sp = client.get('/founder/v/%s/support' % V_ID)
        check('the support page renders with the toggle', sp.status_code == 200
              and 'Turn off support access' in sp.get_data(as_text=True), sp.status_code)

        st, tr = api('get', B + '/ventures/%s/trace' % V_ID)
        appr = [i for i in tr['items'] if i['kind'] == 'approval'][0]
        check('with support access the content is visible', tr['unmasked'] is True
              and appr['subject'] == 'Pilot proposal for Harbor' and 'Jane Roe' in appr['target'])
        check('the unmasked view is audited', audits(fobs.AUDIT_VIEW_UNMASKED) == 1)
        aud = AdminAccessAudit.query.filter_by(action=fobs.AUDIT_VIEW_UNMASKED).first()
        check('the audit row names the admin, venture and founder', aud.admin_user_id == A_ID
              and aud.target_id == V_ID and aud.subject_user_id == F_ID)
        api('get', B + '/autonomy?hours=24')
        check('an unmasked autonomy view is audited too', audits(fobs.AUDIT_VIEW_UNMASKED) == 2)
        body_rows = VentureOutreachAction.query.filter(VentureOutreachAction.venture_id == V_ID,
                                                       VentureOutreachAction.body.isnot(None)).all()
        kept = [(r, r.body) for r in body_rows]
        for r, _b in kept:
            r.body = None
        db.session.commit()
        api('get', B + '/autonomy?hours=24')
        check('an unmasked view of contact names alone (no message body) is audited',
              audits(fobs.AUDIT_VIEW_UNMASKED) == 3)
        for r, b_ in kept:
            r.body = b_
        db.session.commit()

        def support_on():
            db.session.expire_all()
            return fobs.support_access_active(db.session.get(FounderVenture, V_ID))

        v = db.session.get(FounderVenture, V_ID)
        v.support_access_until = datetime.utcnow() - timedelta(minutes=1)
        db.session.commit()
        st, tr = api('get', B + '/ventures/%s/trace' % V_ID)
        check('expired support access masks again', tr['unmasked'] is False and 'Jane' not in str(tr))
        login(F_ID)
        r_form = client.post('/api/founder/v/%s/support-access' % V_ID, data={'grant': '1', 'days': '2'})
        check('the no-JS form post returns to the support page', r_form.status_code == 302
              and r_form.headers['Location'].endswith('/founder/v/%s/support' % V_ID)
              and support_on(), (r_form.status_code, r_form.headers.get('Location')))
        r_form = client.post('/api/founder/v/%s/support-access' % V_ID, data={'grant': '1', 'days': '999'})
        page_after = client.get('/founder/v/%s/support' % V_ID).get_data(as_text=True)
        check('a refused no-JS post returns to the page with the reason', r_form.status_code == 302
              and 'Support access can last' in page_after)
        api('post', '/api/founder/v/%s/support-access' % V_ID, {'grant': True}, uid=F_ID)
        st, body = api('post', '/api/founder/v/%s/support-access' % V_ID, {'grant': False}, uid=F_ID)
        check('the founder can revoke it', st == 200 and body.get('active') is False
              and db.session.get(FounderVenture, V_ID).support_access_until is None)

        # ── 7. Alerts ────────────────────────────────────────────────────
        print('\n7. Alerts')

        def flags(metric):
            return ObservabilityFlag.query.filter_by(rule_name=founder_alerts.RULE_PREFIX + metric).all()

        sent_ok = VentureOutreachAction(venture_id=V_ID, action_type='followup', level_required=3, level_applied=3,
                                        status='sent', approved_by=F_ID, sent_at=datetime.utcnow())
        sent_bad = VentureOutreachAction(venture_id=V_ID, action_type='first_contact', level_required=2,
                                         level_applied=3, status='sent', approved_by=F_ID, sent_at=datetime.utcnow())
        db.session.add_all([sent_ok, sent_bad])
        db.session.commit()
        check('above_level: a send at its approved level is fine', founder_alerts.above_level(sent_ok) is False)
        check('above_level: a send above it is caught', founder_alerts.above_level(sent_bad) is True)
        check('above_level: level 2 with no approver is caught', founder_alerts.above_level(
            VentureOutreachAction(status='sent', level_required=2, level_applied=2, approved_by=None)) is True)
        check('above_level: not-permitted (0) sends are caught', founder_alerts.above_level(
            VentureOutreachAction(status='sent', level_required=0, level_applied=0, approved_by=F_ID)) is True)
        check('above_level: an unsent draft is not', founder_alerts.above_level(
            VentureOutreachAction(status='awaiting_approval', level_required=2, level_applied=4)) is False)

        for _ in range(3):
            founder_run('fnd_competitor_scan', 'F1', 'success')
        for _ in range(2):
            founder_run('fnd_competitor_scan', 'F1', 'error')
        big = founder_run('fnd_pricing_test', 'F3', 'success', cost=2500)
        for n in range(5):
            db.session.add(AgentRunTool(run_id=big, tool_name='mailbox_send', sequence_index=n, status='error'))
        stuck = VenturePlanStep(venture_id=V_ID, layer=2, kind='manual', title='Write the pilot offer', state='ready')
        db.session.add(stuck)
        db.session.commit()
        stuck.updated_at = datetime.utcnow() - timedelta(days=20)
        db.session.commit()

        fired = founder_alerts.evaluate()
        check('evaluate() reports per metric', set(fired) == set(founder_alerts.METRICS), fired)
        ab = flags('above_level_send')
        check('the above-level send is flagged, critical', len(ab) == 1 and ab[0].target_id == sent_bad.id
              and ab[0].severity == 'critical' and ab[0].user_id == F_ID, [(f.target_id, f.severity) for f in ab])
        er = flags('error_rate_pct')
        check('error rate per agent fires (2 of 5 = 40% over 20%)', len(er) == 1
              and er[0].agent_key == 'fnd_competitor_scan' and er[0].layer == 'F1', [f.reason for f in er])
        check('fnd_market_sizing (2 runs) is under the minimum and does not fire',
              not any(f.agent_key == 'fnd_market_sizing' for f in er))
        co = flags('cost_per_venture_cents')
        check('cost per venture fires ($26.30 over $20)', len(co) == 1 and co[0].target_id == V_ID, [f.reason for f in co])
        sk = flags('stuck_days')
        check('a step stuck 20 days fires (threshold 14)', len(sk) == 1 and sk[0].target_id == stuck.id
              and sk[0].layer == 'F2', [f.reason for f in sk])
        check('the stuck alert reason leaves out the step title (founder content)',
              sk and 'pilot offer' not in (sk[0].reason or '') and stuck.id in (sk[0].reason or ''),
              [f.reason for f in sk])
        check('the in-progress step updated today does not', not any(f.target_id == STEP_ID for f in sk))
        cn = flags('connector_failures')
        check('connector failures fire (5 failed calls)', len(cn) == 1, [f.reason for f in cn])
        again = founder_alerts.evaluate()
        check('a second pass raises nothing new', sum(again.values()) == 0, again)

        later = VentureOutreachAction(venture_id=V_ID, action_type='intro_request', level_required=3, level_applied=4,
                                      status='sent', approved_by=F_ID, sent_at=datetime.utcnow())
        db.session.add(later)
        db.session.commit()
        check('check_action flags immediately, once', founder_alerts.check_action(later) is not None
              and founder_alerts.check_action(later) is None and len(flags('above_level_send')) == 2)

        st, body = api('put', B + '/alerts', {'metric': 'error_rate_pct', 'threshold': 50})
        check('admin can change a threshold', st == 200 and body['rule']['threshold'] == 50.0
              and founder_alerts.threshold('error_rate_pct') == 50.0, body)
        from app.services import founder_config
        check('the threshold is the /admin/founder setting (one value, two editors)',
              founder_config.get('fnd_alert_error_rate_pct') == 50)
        founder_config.set('fnd_alert_error_rate_pct', 35, A_ID)
        check('a change on /admin/founder shows in the alert rule', founder_alerts.threshold('error_rate_pct') == 35.0
              and [r for r in founder_alerts.rules() if r['metric'] == 'error_rate_pct'][0]['threshold'] == 35.0)
        bad_vals = [('error_rate_pct', 'nan'), ('error_rate_pct', 'inf'), ('cost_per_venture_cents', '-inf'),
                    ('stuck_days', 0), ('stuck_days', 400), ('error_rate_pct', 101), ('connector_failures', 'NaN'),
                    ('connector_failures', 10 ** 9), ('stuck_days', 2.5)]
        bad_st = [(m, v, api('put', B + '/alerts', {'metric': m, 'threshold': v})[0]) for m, v in bad_vals]
        check('NaN, infinity and out-of-range thresholds are refused (400)', all(x[2] == 400 for x in bad_st), bad_st)
        check('refused thresholds change nothing', founder_alerts.threshold('error_rate_pct') == 35.0
              and founder_alerts.threshold('stuck_days') == 14.0 and founder_alerts.threshold('connector_failures') == 5.0)
        st, body = api('put', B + '/alerts', {'metric': 'connector_failures', 'threshold': 7})
        check('the connector threshold (no admin setting) saves on the rule', st == 200
              and founder_alerts.threshold('connector_failures') == 7.0, body)
        api('put', B + '/alerts', {'metric': 'connector_failures', 'threshold': 5})
        founder_config.set('fnd_alert_error_rate_pct', 50, A_ID)
        st, body = api('put', B + '/alerts', {'metric': 'above_level_send', 'enabled': False})
        check('the above-level alert cannot be switched off', st == 400 and founder_alerts.enabled('above_level_send'))
        check('a member cannot change alert rules',
              api('put', B + '/alerts', {'metric': 'stuck_days', 'threshold': 1}, uid=F_ID)[0] == 403)
        api('put', B + '/alerts', {'metric': 'stuck_days', 'enabled': False})
        stuck2 = VenturePlanStep(venture_id=V_ID, layer=2, kind='manual', title='Old step', state='awaiting_approval')
        db.session.add(stuck2)
        db.session.commit()
        stuck2.updated_at = datetime.utcnow() - timedelta(days=60)
        db.session.commit()
        founder_alerts.evaluate()
        check('a disabled rule does not fire', not any(f.target_id == stuck2.id for f in flags('stuck_days')))
        _, al = api('get', B + '/alerts')
        check('the alerts view lists rules and recent alert flags', len(al['rules']) == 5 and len(al['flags']) >= 6,
              len(al.get('flags', [])))
        _, fl = api('get', '/api/admin/observability/flags?hours=24')
        check('alerts show in the existing Flags tab too', any((f.get('rule_name') or '').startswith('fnd_alert:')
                                                              for f in fl['flags']))
        src = io.open(os.path.join(ROOT, 'app/scheduler.py'), encoding='utf-8').read()
        check('the scheduler job exists and is gated on the switch', "id='founder-alerts-check'" in src
              and "founder_feature.mode() == 'off'" in src)

        # ── 8. Controls ──────────────────────────────────────────────────
        print('\n8. Controls: pause and re-run')
        calls = []
        fake = types.ModuleType('app.services.founder_agent_service')

        def fake_execute(step, user=None, trigger='founder'):
            calls.append((step.id, user, trigger))
            return types.SimpleNamespace(id='OUTPUT001')
        fake.execute = fake_execute
        real = sys.modules.get('app.services.founder_agent_service')
        import app.services as services_pkg
        real_attr = getattr(services_pkg, 'founder_agent_service', None)
        sys.modules['app.services.founder_agent_service'] = fake
        services_pkg.founder_agent_service = fake
        try:
            st, body = api('post', B + '/runs/%s/rerun' % ok_run, {})
            check('a successful run cannot be re-run', st == 409, body)
            st, body = api('post', B + '/runs/NOSUCHRUN/rerun', {})
            check('an unknown run is 404', st == 404)

            st, body = api('post', B + '/ventures/%s/pause' % V_ID, {'paused': True})
            vv = db.session.get(FounderVenture, V_ID)
            check('admin pauses all agents for one venture', st == 200 and body['paused'] and vv.paused_at is not None)
            check('dispatch_block_reason blocks every agent of a paused venture',
                  fobs.dispatch_block_reason(vv, 'fnd_market_sizing') and fobs.dispatch_block_reason(vv, 'fnd_poc_plan'))
            st, body = api('post', B + '/runs/%s/rerun' % failed_run, {})
            check('re-run is refused while the venture is paused', st == 409 and 'paused' in body.get('error', '')
                  and not calls, body)
            _, vlist = api('get', B + '/ventures')
            check('the venture list shows the pause', any(x['id'] == V_ID and x['paused'] for x in vlist['ventures']))
            st, body = api('post', B + '/ventures/%s/pause' % V_ID, {'paused': False})
            check('admin resumes the venture', st == 200 and not body['paused']
                  and db.session.get(FounderVenture, V_ID).paused_at is None)

            st, body = api('post', B + '/agents/fnd_market_sizing/pause', {'paused': True})
            check('admin pauses one agent everywhere', st == 200 and body['paused']
                  and fobs.agent_paused('fnd_market_sizing'))
            other_v = FounderVenture(owner_user_id=O_ID, name='Other Co', stage='idea')
            db.session.add(other_v)
            db.session.commit()
            check('the paused agent is blocked for every venture',
                  fobs.dispatch_block_reason(other_v, 'fnd_market_sizing') is not None
                  and fobs.dispatch_block_reason(db.session.get(FounderVenture, V_ID), 'fnd_market_sizing') is not None)
            check('other agents still run', fobs.dispatch_block_reason(other_v, 'fnd_poc_plan') is None)
            st, body = api('post', B + '/runs/%s/rerun' % failed_run, {})
            check('re-run is refused while the agent is paused', st == 409 and not calls, body)
            _, fleet = api('get', B + '/fleet')
            check('fleet marks the paused agent', fleet['paused_agents'] == ['fnd_market_sizing'])
            check('an unknown agent cannot be paused', api('post', B + '/agents/not_an_agent/pause',
                                                            {'paused': True})[0] == 404)
            api('post', B + '/agents/fnd_market_sizing/pause', {'paused': False})
            check('admin resumes the agent', not fobs.agent_paused('fnd_market_sizing'))

            st_row = db.session.get(VenturePlanStep, STEP_ID)
            for state in ('in_progress', 'done', 'awaiting_approval'):
                st_row.state = state
                db.session.commit()
                st, body = api('post', B + '/runs/%s/rerun' % failed_run, {})
                if st != 409 or calls:
                    break
            check('re-run is refused unless the step is back at ready (in progress, done, awaiting approval)',
                  st == 409 and not calls and 'nothing to re-run' in body.get('error', ''), (st, body))
            listed = [r for r in fobs.recent_runs(24, V_ID, 'error') if r['run_id'] == failed_run]
            check('the list offers no Re-run while the step is not ready', listed and not listed[0]['can_rerun'])
            st_row.state = 'ready'
            db.session.commit()
            listed = [r for r in fobs.recent_runs(24, V_ID, 'error') if r['run_id'] == failed_run]
            check('the list offers Re-run once the step is ready', listed and listed[0]['can_rerun'])

            st, body = api('post', B + '/runs/%s/rerun' % failed_run, {})
            check('re-run calls founder_agent_service.execute(step, trigger="rerun")',
                  st == 200 and calls == [(STEP_ID, None, 'rerun')] and body.get('output_id') == 'OUTPUT001',
                  (st, body, calls))
            st, body = api('post', B + '/runs/%s/rerun' % failed_run, {})
            check('the same failed run cannot be re-run twice (409, no second call)',
                  st == 409 and len(calls) == 1 and 'already' in body.get('error', ''), (st, body))
            listed = [r for r in fobs.recent_runs(24, V_ID, 'error') if r['run_id'] == failed_run]
            check('after its re-run the list offers no Re-run for it', listed and not listed[0]['can_rerun'])

            sys.modules['app.services.founder_agent_service'] = None     # import now raises ImportError
            if hasattr(services_pkg, 'founder_agent_service'):
                delattr(services_pkg, 'founder_agent_service')
            fresh_fail = founder_run('fnd_market_sizing', 'F1', 'error', plan_step_id=STEP_ID)
            check('a newer run of the step makes the older failure stale',
                  fobs._newer_step_run(db.session.get(AgentRun, failed_run), STEP_ID)
                  and not fobs._newer_step_run(db.session.get(AgentRun, fresh_fail), STEP_ID))
            st, body = api('post', B + '/runs/%s/rerun' % fresh_fail, {})
            check('without founder agents installed, re-run says so (503)', st == 503, (st, body))
        finally:
            if real is not None:
                sys.modules['app.services.founder_agent_service'] = real
            else:
                sys.modules.pop('app.services.founder_agent_service', None)
            if real_attr is not None:
                services_pkg.founder_agent_service = real_attr

        # -- 8b. A real failed run (no founder_run_meta) can be found and re-run --
        print('\n8b. A real failed run, then its re-run')
        from app.models.founder import VentureAgentOutput
        from app.services import founder_agent_service as fas

        class _Client(object):
            def __init__(self):
                self.fail, self.calls = True, 0
                self.messages = self

            def create(self, **kw):
                self.calls += 1
                if self.fail:
                    raise RuntimeError('api down')
                block = types.SimpleNamespace(type='tool_use', id='tu1', name=kw['tools'][0]['name'],
                                              input={'content': '## Problem\nA short, plain statement.',
                                                     'confidence': 'low'})
                return types.SimpleNamespace(content=[block],
                                             usage=types.SimpleNamespace(input_tokens=500, output_tokens=200))
        fake_client = _Client()
        real_client = fas.AI_CLIENT
        fas.AI_CLIENT = fake_client
        try:
            pv = db.session.get(FounderVenture, V_ID)
            pv.paid_at, pv.status, pv.paused_at = datetime.utcnow(), 'active', None
            real_step = VenturePlanStep(venture_id=V_ID, layer=1, kind='agent', agent_key='fnd_problem_statement',
                                        title='State the problem', state='ready')
            db.session.add(real_step)
            db.session.commit()
            RS_ID = real_step.id
            try:
                fas.execute(real_step)
                raised = None
            except fas.AgentError as exc:
                raised = exc
            real_failed = (AgentRun.query.filter_by(agent_key='fnd_problem_statement', status='error')
                           .order_by(AgentRun.started_at.desc()).first())
            check('execute() with a failing model raises and records an F1 error run', raised is not None
                  and real_failed is not None and real_failed.layer == 'F1', raised)
            RF_ID = real_failed.id if real_failed else None
            check('that failed run has no founder_run_meta row (WP4 writes it on success only)',
                  FounderRunMeta.query.filter_by(obs_run_id=RF_ID).count() == 0)
            check('its step is back at ready', db.session.get(VenturePlanStep, RS_ID).state == 'ready')
            listed = [r for r in fobs.recent_runs(24, status='error') if r['run_id'] == RF_ID]
            check('the failed-runs list names its venture and offers Re-run',
                  listed and listed[0]['venture_id'] == V_ID and listed[0]['venture_name'] == 'Acme Robotics'
                  and listed[0]['can_rerun'], listed)
            check('it is in the venture filter of the founder run list',
                  any(r['run_id'] == RF_ID for r in fobs.recent_runs(24, V_ID)))
            _, by_v2 = api('get', '/api/admin/observability/runs?hours=24&venture=%s' % V_ID)
            check('it is in ?venture= of the console run list',
                  any(RF_ID in (r.get('id'), r.get('run_id')) for r in by_v2.get('runs', [])), by_v2.get('total'))
            _, tr3 = api('get', B + '/ventures/%s/trace' % V_ID)
            check('it is in the venture trace', any(i.get('run_id') == RF_ID and i['kind'] == 'run'
                                                    for i in tr3.get('items', [])))
            check("it is not in another venture's runs",
                  not any(r['run_id'] == RF_ID for r in fobs.recent_runs(24, other_v.id)))

            fake_client.fail = False
            n0 = fake_client.calls
            st, body = api('post', B + '/runs/%s/rerun' % RF_ID, {})
            out_row = VentureAgentOutput.query.filter_by(plan_step_id=RS_ID).first()
            check('the admin re-runs it: one model call, an output, a new traced run',
                  st == 200 and fake_client.calls == n0 + 1 and out_row is not None
                  and body.get('output_id') == out_row.id
                  and FounderRunMeta.query.filter_by(plan_step_id=RS_ID, trigger='rerun').count() == 1, (st, body))
            st, body = api('post', B + '/runs/%s/rerun' % RF_ID, {})
            check('a second re-run of it is refused with no model call', st == 409 and fake_client.calls == n0 + 1,
                  (st, body))
            listed = [r for r in fobs.recent_runs(24, status='error') if r['run_id'] == RF_ID]
            check('and the list no longer offers Re-run for it', listed and not listed[0]['can_rerun'])
        finally:
            fas.AI_CLIENT = real_client

        # ── 9. Other views ───────────────────────────────────────────────
        print('\n9. Evaluation quality, compliance, connectors, startup pages')
        from app.models.founder import (AgentBlueprint, AgentParamOverride, ComplianceProfile, EvaluationFinding,
                                        EvaluationRun, FindingRating, StartupPage, StartupPageVisit)
        er_row = EvaluationRun(venture_id=V_ID, status='done')
        db.session.add(er_row)
        db.session.commit()
        fs = []
        for n, fair in enumerate((True, True, False)):
            f = EvaluationFinding(venture_id=V_ID, evaluation_run_id=er_row.id, dimension='market', ftype='gap',
                                  title='Secret finding %d' % n, recommended_agent='fnd_market_sizing')
            db.session.add(f)
            db.session.flush()
            db.session.add(FindingRating(finding_id=f.id, user_id=F_ID, fair=fair, comment='private comment'))
        bp1 = AgentBlueprint(venture_id=V_ID, agent_key='fnd_market_sizing', layer=1, status='accepted')
        bp2 = AgentBlueprint(venture_id=V_ID, agent_key='fnd_poc_plan', layer=2, status='accepted')
        db.session.add_all([bp1, bp2])
        db.session.flush()
        db.session.add(AgentParamOverride(venture_id=V_ID, blueprint_id=bp1.id, param_key='scope', user_id=F_ID,
                                          previous_value='a', new_value='b'))
        db.session.add(ComplianceProfile(venture_id=V_ID, exemption='506b', recorded_by=F_ID, recorded_at=now))
        db.session.add(VentureOutreachAction(venture_id=V_ID, action_type='first_contact', status='blocked',
                                             block_reason='General solicitation is not allowed under 506(b).'))
        page_row = StartupPage(venture_id=V_ID, slug='acme-robotics', name='Acme Robotics', status='published')
        db.session.add(page_row)
        db.session.flush()
        for view in ('public', 'public', 'investor'):
            db.session.add(StartupPageVisit(startup_page_id=page_row.id, view=view))
        db.session.commit()

        _, q = api('get', B + '/eval-quality')
        check('finding accuracy: 2 of 3 fair', q['ratings'] == 3 and q['accuracy'] == 66.7, q)
        check('accuracy by dimension and agent', q['by_dimension'][0]['dimension'] == 'market'
              and q['by_agent'][0]['agent_key'] == 'fnd_market_sizing')
        ov = {r['agent_key']: r for r in q['overrides_by_agent']}
        check('override rate per agent', ov['fnd_market_sizing']['override_rate'] == 100.0
              and ov['fnd_poc_plan']['override_rate'] == 0.0, ov)
        check('eval quality shows no finding text or comments', 'Secret finding' not in str(q)
              and 'private comment' not in str(q))
        _, c = api('get', B + '/compliance?hours=24')
        acme = [x for x in c['ventures'] if x['venture_id'] == V_ID][0]
        check('compliance: exemption recorded per venture', acme['exemption'] == '506b'
              and c['by_exemption'].get('not_recorded') == 1, c['by_exemption'])
        check('compliance: blocked actions listed with reason', len(c['blocked_actions']) == 1
              and '506(b)' in c['blocked_actions'][0]['block_reason'])
        _, cn_v = api('get', B + '/connectors?hours=24')
        check('connectors: failed calls counted', cn_v['failures'] == 5 and cn_v['tools'][0]['tool'] == 'mailbox_send',
              cn_v)
        _, sp_v = api('get', B + '/startup-pages?hours=24')
        pg = sp_v['pages'][0]
        check('startup pages: views by kind', pg['public_views'] == 2 and pg['investor_views'] == 1, pg)
        check('startup pages: investor unlocks read when present', pg['unlocks'] == 0 and sp_v['investor_tables'])

        # ── 10. Member nav ───────────────────────────────────────────────
        print('\n10. Member nav item')
        check('m_founder is a known nav id', 'm_founder' in site_nav.ITEM_IDS)
        founder_feature.set_mode('founder_track', 'off', A_ID)

        def my_work(uid):
            u = db.session.get(User, uid)
            with app.test_request_context('/'):
                nav = site_nav.resolve_member(u)
            return [it for g_ in nav['groups'] if g_['id'] == 'my_work' for it in g_['items']]

        check('hidden for a member while the switch is off', 'm_founder' not in [i['id'] for i in my_work(F_ID)])
        check('hidden for an admin while the switch is off', 'm_founder' not in [i['id'] for i in my_work(A_ID)])
        login(F_ID)
        html = client.get('/careers').get_data(as_text=True)
        check('the rendered nav has no Founder Track item while off', 'm_founder' not in html)
        founder_feature.set_mode('founder_track', 'allowlist', A_ID)
        items = my_work(F_ID)
        fi = [i for i in items if i['id'] == 'm_founder']
        check('shown to an allowed member when on', fi and fi[0]['href'] == '/founder' and fi[0]['title'] == 'Founder Track',
              fi)
        check('it sits in My work after the existing items', [i['id'] for i in items][-1] == 'm_founder')
        blocked = user('blocked@x.com')
        check('hidden for a member not on the allowlist', 'm_founder' not in [i['id'] for i in my_work(blocked.id)])
        login(F_ID)
        html = client.get('/careers').get_data(as_text=True)
        check('the rendered nav shows it when on', 'data-nav-item="m_founder"' in html)
        founder_feature.set_mode('founder_track', 'off', A_ID)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  FAIL:', f)
        sys.exit(1)


if __name__ == '__main__':
    main()
