"""PRD-SIM-OBS-001 — end-to-end check of Agent Observability & Governance.

Runs against an in-memory SQLite database, so it touches no real data and makes
no network calls. Covers the pieces that are easy to get subtly wrong: PII
masking, the capture-fails-open promise, sampling, the by-layer rollups, every
guardrail type, both enforcement carve-outs, the enforcement-fails-closed rule,
the three retention clocks, and the admin API's access-audit guarantee.

    python test_observability.py
"""
import os
import sys

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from utils.id_gen import generate_id                          # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    mark = 'ok  ' if condition else 'FAIL'
    line = '  {} {}{}'.format(mark, label, ('  -- ' + str(detail)) if detail else '')
    print(line.encode(sys.stdout.encoding or 'ascii', 'replace')
              .decode(sys.stdout.encoding or 'ascii'))


def main():
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()

        from datetime import datetime, timedelta

        from app.models.observability import (
            AdminAccessAudit, AgentArtifact, AgentEvent, AgentRun, AgentRunTool,
            GuardrailRule, ObservabilityFlag,
        )
        from app.models.user import User
        from app.services import guardrail_service as gr
        from app.services import observability_service as obs

        owner = User(id=generate_id(), email='owner@example.com',
                     full_name='Owner', password_hash='x', is_admin=False)
        admin = User(id=generate_id(), email='admin@example.com',
                     full_name='Admin', password_hash='x', is_admin=True)
        db.session.add_all([owner, admin])
        db.session.commit()

        def new_rule(**kw):
            payload = dict(name=kw.pop('name'), rule_type=kw.pop('rule_type'),
                           scope=kw.pop('scope', 'global'), scope_ref=kw.pop('scope_ref', None),
                           action=kw.pop('action', 'log'), severity=kw.pop('severity', 'medium'),
                           definition=kw.pop('definition', {}))
            clean = gr.validate_rule(payload)
            rule = GuardrailRule(id=generate_id(),
                                 definition_json=clean.pop('definition'), **clean)
            db.session.add(rule)
            db.session.commit()
            return rule

        # ── 1. Masking ─────────────────────────────────────────────────────
        print('\n1. PII masking at capture')
        masked = obs.mask_params({
            'note': 'reach me at jane.doe@corp.com or 555-123-4567',
            'email': 'jane.doe@corp.com',
            'rate': 250,
            'bio': 'x' * 900,
        })
        check('email in free text is masked', '[email]' in masked['note'], masked['note'])
        check('phone in free text is masked', '[phone]' in masked['note'], masked['note'])
        check('a PII-named key is redacted whole', masked['email'] == '[redacted]')
        check('numbers survive unchanged', masked['rate'] == 250)
        check('long free text is bounded', len(masked['bio']) <= 205, len(masked['bio']))

        obs.save_setting('obs_redact_pii', False, updated_by=admin.id)
        loose = obs.mask_params({'email': 'jane.doe@corp.com'})
        check('redaction can be switched off', loose['email'] == 'jane.doe@corp.com')
        obs.save_setting('obs_redact_pii', True, updated_by=admin.id)

        # ── 2. Capture ─────────────────────────────────────────────────────
        print('\n2. Capture')
        run = obs.start_run('income_allocation', 5, user_id=owner.id,
                            simulation_id=None, action_id=None,
                            params={'invest_pct': 0.65}, model='claude-haiku-4-5-20251001',
                            dispatch_source='orchestrator')
        check('a run opens a header immediately',
              AgentRun.query.filter_by(id=run.id).first() is not None)
        check('the open header reads as running',
              AgentRun.query.get(run.id).status == 'running')

        obs.record_decision('Computed split', invest=0.65)
        with obs.tool_span('alloc_fold', {'streams': 4}) as span:
            span['output'] = '4 buckets computed'
        check('detail is buffered, not written mid-run',
              AgentRunTool.query.count() == 0 and AgentEvent.query.count() == 0)

        obs.record_usage(prompt_tokens=800, completion_tokens=1200)
        obs.record_artifact('Allocation Plan', content='PLAN BODY',
                            content_ref='agent_action:abc123456')
        obs.finish_run(status='success')

        header = AgentRun.query.get(run.id)
        check('the header closes as success', header.status == 'success')
        check('token counts land on the header',
              header.prompt_tokens == 800 and header.completion_tokens == 1200)
        check('latency is recorded', header.latency_ms is not None)
        check('the tool call flushed', AgentRunTool.query.filter_by(run_id=run.id).count() == 1)
        check('the trace flushed', AgentEvent.query.filter_by(run_id=run.id).count() == 1)
        check('params on the header are masked',
              header.params_json.get('invest_pct') == 0.65)
        artifact = AgentArtifact.query.filter_by(run_id=run.id).first()
        check('the artifact is a summary plus a reference',
              artifact is not None and artifact.withheld_content is None
              and artifact.content_ref == 'agent_action:abc123456')
        check('artifact size is captured without the content', artifact.size == len('PLAN BODY'))
        check('the thread-local is cleared', obs.current() is None)

        # ── 3. Fail open ───────────────────────────────────────────────────
        print('\n3. Capture fails open')
        obs.record_decision('no run in flight')      # must not raise
        obs.record_tool('orphan')
        obs.finish_run()
        check('recording outside a run is a no-op, not an error', True)

        broken = obs.start_run('rate_card', 1, user_id=owner.id, params={'x': 1})
        broken.artifacts.append({'id': 'bad', 'artifact_type': None,
                                 'summary_json': None, 'content_ref': None, 'size': None})
        obs.finish_run(status='success')             # a bad row must not raise
        check('a malformed detail row does not raise into the pipeline', True)

        obs.save_setting('obs_capture_enabled', False, updated_by=admin.id)
        check('capture off means no run is opened',
              obs.start_run('rate_card', 1, user_id=owner.id) is None)
        obs.save_setting('obs_capture_enabled', True, updated_by=admin.id)

        # ── 4. Sampling ────────────────────────────────────────────────────
        print('\n4. Sampling')
        obs.save_setting('obs_detail_sampling_pct', 0, updated_by=admin.id)
        sampled_out = obs.start_run('projections', 5, user_id=owner.id)
        obs.record_decision('should not be stored')
        obs.finish_run(status='success')
        check('a sampled-out run still writes its header',
              AgentRun.query.get(sampled_out.id) is not None)
        check('a sampled-out run stores no trace',
              AgentEvent.query.filter_by(run_id=sampled_out.id).count() == 0)

        forced = obs.start_run('projections', 5, user_id=owner.id)
        obs.record_decision('kept because the run was blocked')
        obs.finish_run(status='blocked')
        check('a blocked run is captured in full whatever the sampling rate',
              AgentEvent.query.filter_by(run_id=forced.id).count() == 1,
              AgentEvent.query.filter_by(run_id=forced.id).count())
        obs.save_setting('obs_detail_sampling_pct', 100, updated_by=admin.id)

        # ── 5. Rollups ─────────────────────────────────────────────────────
        print('\n5. By-layer rollups')
        obs.invalidate()
        cards = {c['layer']: c for c in obs.layer_rollups(24)}
        # Seven: the free tier plus the five wealth layers plus orchestration.
        check('every layer always renders, even with no runs',
              len(cards) == len(obs.LAYER_CODES), sorted(cards))
        check('L5 counts its runs', cards['L5']['runs'] >= 3, cards['L5']['runs'])
        check('L1 counts its runs', cards['L1']['runs'] >= 1, cards['L1']['runs'])
        check('top tools are surfaced per layer',
              'alloc_fold' in cards['L5']['top_tools'], cards['L5']['top_tools'])
        check('spend is derived from tokens and the price table',
              cards['L5']['spend'] > 0, cards['L5']['spend'])
        agents = {a['agent_key']: a for a in obs.agent_rollups('L5', 24)}
        check('per-agent rollups are scoped to the layer',
              'income_allocation' in agents and 'rate_card' not in agents)

        # MySQL returns SUM() over an INT column as Decimal where SQLite returns
        # int, so the aggregate path has to be exercised with Decimal directly —
        # this database will never hand it one, and production always will.
        from decimal import Decimal
        spend = obs.estimate_spend([
            ('claude-haiku-4-5-20251001', Decimal('1000000'), Decimal('2000000')),
        ])
        check('spend survives Decimal aggregates from MySQL',
              abs(spend - 11.0) < 0.001, spend)
        check('spend handles a null aggregate',
              obs.estimate_spend([('claude-sonnet-4-6', None, None)]) == 0.0)
        check('spend handles an unknown model',
              obs.estimate_spend([(None, Decimal('1000000'), 0)]) == 3.0,
              obs.estimate_spend([(None, Decimal('1000000'), 0)]))
        check('every rollup number is JSON-serialisable',
              all(isinstance(c[k], (int, float))
                  for c in cards.values()
                  for k in ('runs', 'errors', 'flagged', 'tokens', 'spend',
                            'error_rate', 'flag_rate')))

        # ── 6. Guardrails — parameter bound ────────────────────────────────
        print('\n6. Guardrails: parameter bound')
        cap = new_rule(name='invest-cap-55', rule_type='param_bound', scope='agent',
                       scope_ref='income_allocation', action='warn',
                       definition={'field': 'invest_pct', 'max': 0.55})
        run = obs.start_run('income_allocation', 5, user_id=owner.id,
                            params={'invest_pct': 0.65})
        verdict = gr.evaluate_run(run, artifact='plan', params={'invest_pct': 0.65})
        check('an over-cap parameter fires the rule', len(verdict.flags) == 1, verdict.reason)
        check('warn does not withhold the output', not verdict.blocked)
        obs.finish_run(status='success')
        check('the flag is persisted', ObservabilityFlag.query.filter_by(rule_id=cap.id).count() == 1)
        check('the run is marked flagged', AgentRun.query.get(run.id).flag_count == 1)
        db.session.expire_all()
        check('the rule counts its evaluations and fires',
              GuardrailRule.query.get(cap.id).eval_count == 1
              and GuardrailRule.query.get(cap.id).fire_count == 1)

        run = obs.start_run('income_allocation', 5, user_id=owner.id,
                            params={'invest_pct': 0.40})
        verdict = gr.evaluate_run(run, artifact='plan', params={'invest_pct': 0.40})
        check('an in-bounds parameter does not fire', not verdict.flags)
        obs.finish_run(status='success')

        # ── 7. Content policy and blocking ─────────────────────────────────
        print('\n7. Guardrails: content policy and blocking')
        new_rule(name='no-earnings-claims', rule_type='content_policy', scope='global',
                 action='block', severity='high',
                 definition={'forbid': ['guaranteed returns']})
        run = obs.start_run('projections', 5, user_id=owner.id)
        obs.record_artifact('Projection', content='We promise guaranteed returns of 40%.')
        verdict = gr.evaluate_run(run, artifact='We promise guaranteed returns of 40%.')
        check('a forbidden phrase blocks the output', verdict.blocked, verdict.reason)
        obs.quarantine_artifacts('We promise guaranteed returns of 40%.', verdict.reason)
        obs.finish_run(status='blocked')
        held = AgentArtifact.query.filter_by(run_id=run.id).first()
        check('the blocked artifact is quarantined', held.is_quarantined)
        check('the withheld content is held for review',
              held.withheld_content == 'We promise guaranteed returns of 40%.')
        check('the run closes as blocked', AgentRun.query.get(run.id).status == 'blocked')

        run = obs.start_run('projections', 5, user_id=owner.id)
        verdict = gr.evaluate_run(run, artifact='A conservative range, not a promise.')
        check('clean copy passes the same rule', not verdict.blocked)
        obs.finish_run(status='success')

        # required text (a disclaimer the artifact has to carry)
        new_rule(name='needs-disclaimer', rule_type='content_policy', scope='layer',
                 scope_ref='L5', action='warn',
                 definition={'require': ['not financial advice']})
        run = obs.start_run('fund_recommendations', 5, user_id=owner.id)
        verdict = gr.evaluate_run(run, artifact='Buy these funds.')
        check('a missing required disclaimer fires',
              any('missing required' in f['reason'] for f in verdict.flags), verdict.reason)
        obs.finish_run(status='success')

        # ── 8. Enforcement switch ──────────────────────────────────────────
        print('\n8. Enforcement switch')
        obs.save_setting('obs_enforcement_enabled', False, updated_by=admin.id)
        run = obs.start_run('projections', 5, user_id=owner.id)
        verdict = gr.evaluate_run(run, artifact='guaranteed returns again')
        check('enforcement off downgrades block to warn', not verdict.blocked)
        check('the flag still records what would have happened',
              verdict.flags and verdict.flags[0]['action'] == 'warn')
        obs.finish_run(status='success')
        obs.save_setting('obs_enforcement_enabled', True, updated_by=admin.id)

        # ── 9. Enforcement fails closed ────────────────────────────────────
        print('\n9. Enforcement fails closed')
        exploding = new_rule(name='broken-rule', rule_type='param_bound', scope='global',
                             action='block', definition={'field': 'amount', 'max': 10})
        original = gr._EVALUATORS['param_bound']

        def boom(rule, context):
            if rule.id == exploding.id:
                raise RuntimeError('rule is broken')
            return original(rule, context)

        gr._EVALUATORS['param_bound'] = boom
        run = obs.start_run('rate_card', 1, user_id=owner.id, params={'amount': 1})
        verdict = gr.evaluate_run(run, artifact='ok', params={'amount': 1})
        check('a blocking rule that errors blocks anyway', verdict.blocked, verdict.reason)
        obs.finish_run(status='blocked')

        gr._EVALUATORS['param_bound'] = original
        exploding.action = 'log'
        db.session.commit()
        gr._EVALUATORS['param_bound'] = boom
        run = obs.start_run('rate_card', 1, user_id=owner.id, params={'amount': 1})
        verdict = gr.evaluate_run(run, artifact='ok', params={'amount': 1})
        check('a non-blocking rule that errors is skipped', not verdict.blocked)
        obs.finish_run(status='success')
        gr._EVALUATORS['param_bound'] = original
        db.session.delete(GuardrailRule.query.get(exploding.id))
        db.session.commit()

        # ── 10. Rate limit at dispatch ─────────────────────────────────────
        print('\n10. Carve-out one: rate limits gate at dispatch')
        # Scoped to an agent no earlier section has run, so the window starts empty.
        new_rule(name='throttle-booking-page', rule_type='rate_limit', scope='agent',
                 scope_ref='booking_page', action='block',
                 definition={'max_runs': 2, 'per_minutes': 60, 'per': 'user'})
        allowed, blocked_count = 0, 0
        for _ in range(5):
            run = obs.start_run('booking_page', 1, user_id=owner.id)
            gate = gr.check_dispatch('booking_page', 1, owner.id, 'claude-haiku-4-5-20251001', run)
            if gate.blocked:
                blocked_count += 1
                obs.finish_run(status='blocked', error_code='dispatch_blocked')
            else:
                allowed += 1
                obs.finish_run(status='success')
        check('the limit lets exactly its quota through', allowed == 2, allowed)
        check('the limit blocks every run past the quota', blocked_count == 3, blocked_count)

        other = User(id=generate_id(), email='other@example.com', full_name='Other',
                     password_hash='x')
        db.session.add(other)
        db.session.commit()
        run = obs.start_run('booking_page', 1, user_id=other.id)
        gate = gr.check_dispatch('booking_page', 1, other.id, None, run)
        check('a per-user limit does not throttle a different user', not gate.blocked)
        obs.finish_run(status='success')

        # ── 11. Tool boundary ──────────────────────────────────────────────
        print('\n11. Carve-out two: side-effecting tools gate at the boundary')
        new_rule(name='outbound-consent', rule_type='tool_allowlist', scope='layer',
                 scope_ref='L1', action='block',
                 definition={'mode': 'deny', 'tools': ['outbound_email_send']})
        run = obs.start_run('cold_email_campaign', 1, user_id=owner.id)
        sent = False
        try:
            with obs.tool_span('outbound_email_send', {'to': 'x'}, side_effecting=True):
                sent = True
        except gr.ToolBlocked:
            pass
        check('the send is stopped before it commits', not sent)
        obs.finish_run(status='success')
        check('the blocked tool call is on the trace',
              AgentRunTool.query.filter_by(run_id=run.id, status='blocked').count() == 1)

        run = obs.start_run('income_allocation', 5, user_id=owner.id)
        sent = False
        try:
            with obs.tool_span('outbound_email_send', {'to': 'x'}, side_effecting=True):
                sent = True
        except gr.ToolBlocked:
            pass
        check('an L1-scoped tool rule does not touch L5', sent)
        obs.finish_run(status='success')

        # ── 12. Model constraint and pinning ───────────────────────────────
        print('\n12. Guardrails: model constraint')
        new_rule(name='pin-l5-model', rule_type='model_constraint', scope='layer',
                 scope_ref='L5', action='warn',
                 definition={'allowed': ['claude-sonnet-4-6'], 'pin_to': 'claude-sonnet-4-6'})
        run = obs.start_run('projections', 5, user_id=owner.id, model='claude-haiku-4-5-20251001')
        gate = gr.check_dispatch('projections', 5, owner.id, 'claude-haiku-4-5-20251001', run)
        check('pinning substitutes an approved model at dispatch',
              gate.model_override == 'claude-sonnet-4-6', gate.model_override)
        verdict = gr.evaluate_run(run, artifact='ok', model='claude-haiku-4-5-20251001')
        check('an unapproved model is flagged post-run',
              any('not an approved model' in f['reason'] for f in verdict.flags), verdict.reason)
        obs.finish_run(status='success')

        run = obs.start_run('projections', 5, user_id=owner.id, model='claude-sonnet-4-6')
        verdict = gr.evaluate_run(run, artifact='ok', model='claude-sonnet-4-6')
        check('an approved model passes',
              not any('approved model' in f['reason'] for f in verdict.flags))
        obs.finish_run(status='success')

        # ── 13. Scope resolution ───────────────────────────────────────────
        print('\n13. Scope resolution')
        names = {r.name for r in gr.rules_for('L5', 'income_allocation')}
        check('a global rule reaches every agent', 'no-earnings-claims' in names)
        check('a layer rule reaches its layer', 'needs-disclaimer' in names)
        check('an agent rule reaches its agent', 'invest-cap-55' in names)
        check('an agent rule does not reach another agent',
              'invest-cap-55' not in {r.name for r in gr.rules_for('L5', 'projections')})
        check('an L5 rule does not reach L1',
              'needs-disclaimer' not in {r.name for r in gr.rules_for('L1', 'rate_card')})

        # ── 14. Authoring validation ───────────────────────────────────────
        print('\n14. Authoring validation')

        def rejects(label, payload):
            try:
                gr.validate_rule(payload)
                check(label, False, 'accepted when it should not have been')
            except ValueError:
                check(label, True)

        rejects('a non-slug name is rejected', {'name': 'Bad Name!', 'rule_type': 'param_bound',
                                                'definition': {'field': 'x', 'max': 1}})
        rejects('a duplicate name is rejected', {'name': 'invest-cap-55', 'rule_type': 'param_bound',
                                                 'definition': {'field': 'x', 'max': 1}})
        rejects('an unknown rule type is rejected', {'name': 'x-1', 'rule_type': 'nonsense',
                                                     'definition': {}})
        rejects('a bound with no limit is rejected', {'name': 'x-2', 'rule_type': 'param_bound',
                                                      'definition': {'field': 'x'}})
        rejects('min above max is rejected', {'name': 'x-3', 'rule_type': 'param_bound',
                                              'definition': {'field': 'x', 'min': 9, 'max': 1}})
        rejects('an empty tool list is rejected', {'name': 'x-4', 'rule_type': 'tool_allowlist',
                                                   'definition': {'tools': []}})
        rejects('an invalid regex is rejected', {'name': 'x-5', 'rule_type': 'output_filter',
                                                 'definition': {'regex': ['[unclosed']}})
        rejects('a scoped rule with no target is rejected',
                {'name': 'x-6', 'rule_type': 'content_policy', 'scope': 'layer',
                 'definition': {'forbid': ['a']}})
        rejects('pinning to an unapproved model is rejected',
                {'name': 'x-7', 'rule_type': 'model_constraint',
                 'definition': {'allowed': ['a'], 'pin_to': 'b'}})

        clean = gr.validate_rule({'name': 'Mixed Case Rule', 'rule_type': 'content_policy',
                                  'definition': '{"forbid": ["x"]}'})
        check('a name is slugified', clean['name'] == 'mixed-case-rule', clean['name'])
        check('a JSON string definition is parsed', clean['definition'] == {'forbid': ['x']})
        check('a new rule defaults to log', clean['action'] == 'log')

        # ── 15. Overfitting guard ──────────────────────────────────────────
        print('\n15. The overfitting guard')
        fresh = GuardrailRule.query.filter_by(name='needs-disclaimer').first()
        check('promoting a thinly-evaluated rule warns',
              bool(gr.promotion_warning(fresh, 'block')))
        check('promoting to warn does not', not gr.promotion_warning(fresh, 'warn'))
        seasoned = GuardrailRule.query.filter_by(name='invest-cap-55').first()
        seasoned.eval_count = 500
        db.session.commit()
        check('a well-observed rule promotes without a warning',
              not gr.promotion_warning(seasoned, 'block'))

        # ── 16. Retention ──────────────────────────────────────────────────
        print('\n16. Retention')
        old_run = AgentRun(id=generate_id(), user_id=owner.id, agent_key='rate_card',
                           layer='L1', status='success',
                           started_at=datetime.utcnow() - timedelta(days=400))
        db.session.add(old_run)
        db.session.flush()
        db.session.add_all([
            AgentEvent(id=generate_id(), run_id=old_run.id, type='step',
                       ts=datetime.utcnow() - timedelta(days=60)),
            AgentRunTool(id=generate_id(), run_id=old_run.id, tool_name='old',
                         created_at=datetime.utcnow() - timedelta(days=60)),
            ObservabilityFlag(id=generate_id(), target_type='run', target_id=old_run.id,
                              created_at=datetime.utcnow() - timedelta(days=500)),
            ObservabilityFlag(id=generate_id(), target_type='run', target_id=old_run.id,
                              created_at=datetime.utcnow() - timedelta(days=100)),
            AdminAccessAudit(id=generate_id(), admin_user_id=admin.id, action='view_run',
                             ts=datetime.utcnow() - timedelta(days=800)),
        ])
        db.session.commit()

        before_flags = ObservabilityFlag.query.count()
        removed = obs.purge_expired()
        check('run detail past 30 days is purged', removed['events'] >= 1 and removed['tools'] >= 1)
        check('a flag past 13 months is purged', removed['flags'] == 1, removed['flags'])
        check('a flag inside 13 months survives',
              ObservabilityFlag.query.count() == before_flags - 1)
        check('an access-audit row past 24 months is purged', removed['access_audit'] == 1)
        check('the run header is never purged by retention',
              AgentRun.query.get(old_run.id) is not None)

        # Ids the API section needs once the setup context is gone.
        owner_id, admin_id = owner.id, admin.id

    # -- 17. The admin API -------------------------------------------------
    # Deliberately outside the setup app context. Flask reuses an already-pushed
    # app context for a test request, and Flask-Login caches the loaded user on
    # `g` -- so requests made inside one long-lived context would all see the
    # first request's user, and the access checks below would prove nothing.
    print('\n17. The admin API')

    def client_for(user_id=None):
        c = app.test_client()
        if user_id:
            with c.session_transaction() as sess:
                sess['_user_id'] = user_id
                sess['_fresh'] = True
        return c

    def in_ctx(fn):
        with app.app_context():
            return fn()

    check('the console is closed to anonymous callers',
          client_for().get('/api/admin/observability/layers').status_code in (302, 401, 403))
    check('the console is closed to a non-admin user',
          client_for(owner_id).get('/api/admin/observability/layers').status_code == 403)
    check('the console page is 403 for a non-admin',
          client_for(owner_id).get('/admin/observability').status_code == 403)

    res = client_for(admin_id).get('/api/admin/observability/layers?hours=24')
    check('an admin can read the layer rollups', res.status_code == 200, res.status_code)
    check('the rollup response carries every layer',
          res.status_code == 200 and len(res.get_json()['layers']) == 7,
          len((res.get_json() or {}).get('layers', [])))

    res = client_for(admin_id).get('/api/admin/observability/runs?layer=L5')
    check('runs filter by layer', res.status_code == 200
          and all(r['layer'] == 'L5' for r in res.get_json()['runs']))

    res = client_for(admin_id).get('/api/admin/observability/runs?status=blocked')
    check('runs filter by status', res.status_code == 200
          and all(r['status'] == 'blocked' for r in res.get_json()['runs']))

    from app.models.observability import (                       # noqa: E402
        AdminAccessAudit, AgentArtifact, AgentRun,
    )
    from app.services import observability_service as obs        # noqa: E402

    audits_before = in_ctx(lambda: AdminAccessAudit.query.count())
    run_id = in_ctx(lambda: AgentRun.query.filter_by(user_id=owner_id).first().id)
    res = client_for(admin_id).get('/api/admin/observability/runs/' + run_id)
    check('the run detail returns its three panes',
          res.status_code == 200
          and all(k in res.get_json() for k in ('trace', 'tools', 'artifacts')))
    check('viewing a run writes an access-audit row',
          in_ctx(lambda: AdminAccessAudit.query.count()) == audits_before + 1)
    last = in_ctx(lambda: AdminAccessAudit.query
                  .order_by(AdminAccessAudit.ts.desc()).first())
    check('the audit row names the admin and the subject',
          last.admin_user_id == admin_id and last.subject_user_id == owner_id)

    held_id = in_ctx(lambda: AgentArtifact.query.filter_by(is_quarantined=True).first().id)
    audits_before = in_ctx(lambda: AdminAccessAudit.query.count())
    res = client_for(admin_id).get('/api/admin/observability/artifacts/' + held_id)
    check('a withheld artifact can be read by an admin',
          res.status_code == 200 and res.get_json()['content_source'] == 'quarantine')
    check('reading artifact content writes an access-audit row',
          in_ctx(lambda: AdminAccessAudit.query.count()) == audits_before + 1)

    res = client_for(admin_id).post(
        '/api/admin/observability/artifacts/' + held_id + '/release')
    check('a withheld artifact can be released', res.status_code == 200)
    check('release clears the quarantine',
          not in_ctx(lambda: AgentArtifact.query.get(held_id).is_quarantined))
    check('releasing is audited too',
          in_ctx(lambda: AdminAccessAudit.query.filter_by(
              action='release_artifact').count()) == 1)

    res = client_for(admin_id).post('/api/admin/guardrails/rules', json={
        'name': 'api-made-rule', 'rule_type': 'param_bound', 'scope': 'agent',
        'scope_ref': 'rate_card', 'action': 'log',
        'definition': {'field': 'hourly_rate', 'min': 50},
    })
    check('an admin can author a rule over the API', res.status_code == 201, res.status_code)
    made_id = res.get_json()['rule']['id'] if res.status_code == 201 else ''

    res = client_for(admin_id).post('/api/admin/guardrails/rules', json={
        'name': 'api-made-rule', 'rule_type': 'param_bound',
        'definition': {'field': 'x', 'min': 1}})
    check('a duplicate name is refused with a readable error',
          res.status_code == 400 and 'already exists' in res.get_json()['error'])

    res = client_for(admin_id).put('/api/admin/guardrails/rules/' + made_id,
                                   json={'is_enabled': False})
    check('a rule can be toggled off without resending it',
          res.status_code == 200 and res.get_json()['rule']['is_enabled'] is False)

    res = client_for(admin_id).put('/api/admin/guardrails/rules/' + made_id,
                                   json={'action': 'block'})
    check('promoting a thin rule to block returns the overfitting warning',
          res.status_code == 200 and bool(res.get_json().get('warning')),
          res.get_json().get('warning'))

    res = client_for(admin_id).get('/api/admin/observability/flags')
    check('the flags view lists what fired',
          res.status_code == 200 and len(res.get_json()['flags']) > 0)

    res = client_for(admin_id).get('/api/admin/observability/audit')
    check('the access-audit log is readable',
          res.status_code == 200 and len(res.get_json()['entries']) > 0)

    res = client_for(admin_id).get('/api/admin/observability/export?layer=L5')
    check('the export returns CSV', res.status_code == 200
          and 'text/csv' in res.headers.get('Content-Type', ''))
    check('an export is audited',
          in_ctx(lambda: AdminAccessAudit.query.filter_by(action='export').count()) == 1)

    res = client_for(admin_id).put('/api/admin/observability/settings',
                                   json={'settings': {'obs_detail_sampling_pct': 150}})
    check('an out-of-range setting is refused', res.status_code == 400)
    res = client_for(admin_id).put('/api/admin/observability/settings',
                                   json={'settings': {'obs_token_prices': 'not json'}})
    check('a malformed price table is refused', res.status_code == 400)
    res = client_for(admin_id).put('/api/admin/observability/settings',
                                   json={'settings': {'obs_retention_detail_days': 45}})
    check('a valid setting saves', res.status_code == 200
          and in_ctx(lambda: obs.setting('obs_retention_detail_days')) == 45)

    check('the console page renders for an admin',
          client_for(admin_id).get('/admin/observability').status_code == 200)

    res = client_for(admin_id).get('/api/admin/observability/meta')
    check('the filter vocabularies load',
          res.status_code == 200 and len(res.get_json()['agents']) > 40)

    # -- 18. The real pipeline ----------------------------------------------
    # Everything above tests the services directly. This drives the actual
    # entry point in app/services/claude.py with a stubbed Claude client, so
    # the wiring itself -- run opened, tools traced, artifact registered,
    # guardrails consulted, output withheld -- is covered rather than assumed.
    print('\n18. execute_agent_action end to end')

    class _Usage(object):
        input_tokens, output_tokens = 1234, 5678

    class _Block(object):
        def __init__(self, text):
            self.text = text

    class _Response(object):
        def __init__(self, text):
            self.content, self.usage, self.stop_reason = [_Block(text)], _Usage(), 'end_turn'

    class _Messages(object):
        def __init__(self, text):
            self.text = text

        def create(self, **kwargs):
            _Messages.last_kwargs = kwargs
            return _Response(self.text)

    class _Client(object):
        def __init__(self, text):
            self.messages = _Messages(text)

    from app.services import claude as claude_svc                # noqa: E402

    def run_agent(text, action_type='rate_card', layer=1, inputs=None):
        claude_svc._client = lambda: _Client(text)
        claude_svc._log_interaction = lambda *a, **k: None
        with app.app_context():
            return claude_svc.execute_agent_action(
                action_type=action_type, layer_number=layer,
                expertise_zone='Data platform architecture',
                parsed_text='Fifteen years building data platforms.',
                user_inputs=inputs or {'hourly_rate': 250,
                                       'contact_email': 'jane@corp.com'},
                user_id=owner_id, simulation_id=None,
                dispatch_source='user_rerun', action_id=None,
            )

    def latest_run(agent_key):
        with app.app_context():
            return (AgentRun.query.filter_by(agent_key=agent_key)
                    .order_by(AgentRun.started_at.desc()).first())

    # The pipeline strips the model's trailing whitespace, so compare stripped.
    artifact_text = '# Rate Card\n\nDay rate: $2,000.\n'
    stripped = artifact_text.strip()
    returned = run_agent(artifact_text)
    check('the agent returns its artifact unchanged', returned == stripped, repr(returned))

    r = latest_run('rate_card')
    check('the pipeline opened and closed a run', r is not None and r.status == 'success',
          r.status if r else None)
    check('the pipeline recorded the model and tokens',
          r.model and r.prompt_tokens == 1234 and r.completion_tokens == 5678,
          (r.model, r.prompt_tokens, r.completion_tokens))
    check('the pipeline recorded a latency', r.latency_ms is not None)
    check('the dispatch source is carried through', r.dispatch_source == 'user_rerun')

    with app.app_context():
        tools = [t.tool_name for t in AgentRunTool.query.filter_by(run_id=r.id).all()]
        events = AgentEvent.query.filter_by(run_id=r.id).count()
        arts = AgentArtifact.query.filter_by(run_id=r.id).all()
    check('the Claude call is on the tool trace', 'claude_generate' in tools, tools)
    check('the behaviour trace has at least one step', events >= 1, events)
    check('the artifact is registered with its agent label',
          len(arts) == 1 and arts[0].artifact_type.startswith('Generate Rate Card'),
          [a.artifact_type for a in arts])
    check('the captured params are masked',
          r.params_json.get('contact_email') == '[redacted]', r.params_json)

    # A blocking content rule must withhold the output rather than raise.
    with app.app_context():
        from app.models.observability import GuardrailRule as GR
        db.session.add(GR(id=generate_id(), name='no-day-rate-promises',
                          scope='agent', scope_ref='rate_card',
                          rule_type='content_policy', action='block', severity='high',
                          definition_json={'forbid': ['Day rate']}))
        db.session.commit()

    returned = run_agent(artifact_text)
    check('a blocked run returns the held-for-review notice',
          'Held for review' in returned and stripped not in returned)

    r = latest_run('rate_card')
    check('the blocked run closes as blocked', r.status == 'blocked', r.status)
    check('the blocked run carries an error code', r.error_code == 'output_withheld')
    with app.app_context():
        held = AgentArtifact.query.filter_by(run_id=r.id).first()
    check('the withheld output is quarantined, not lost',
          held.is_quarantined and held.withheld_content == stripped,
          (held.is_quarantined, (held.withheld_content or '')[:40]))

    # A rate limit must stop the run before the Claude call is made.
    with app.app_context():
        from app.models.observability import GuardrailRule as GR
        db.session.query(GR).filter_by(name='no-day-rate-promises').delete()
        db.session.add(GR(id=generate_id(), name='throttle-linkedin',
                          scope='agent', scope_ref='linkedin_optimization',
                          rule_type='rate_limit', action='block', severity='medium',
                          definition_json={'max_runs': 1, 'per_minutes': 60,
                                           'per': 'user'}))
        db.session.commit()

    calls = []

    class _CountingMessages(_Messages):
        def create(self, **kwargs):
            calls.append(1)
            return _Response('LinkedIn headline copy.')

    def run_linkedin():
        client = _Client('LinkedIn headline copy.')
        client.messages = _CountingMessages('LinkedIn headline copy.')
        claude_svc._client = lambda: client
        claude_svc._log_interaction = lambda *a, **k: None
        with app.app_context():
            return claude_svc.execute_agent_action(
                action_type='linkedin_optimization', layer_number=1,
                expertise_zone='Data platform architecture', parsed_text='x',
                user_inputs={}, user_id=owner_id, simulation_id=None,
                dispatch_source='user_rerun', action_id=None,
            )

    # Each run makes two calls — the planning step, then generation.
    first = run_linkedin()
    calls_after_first = len(calls)
    second = run_linkedin()
    check('the first run through the limit reaches Claude',
          'LinkedIn' in first and calls_after_first == 2, (first[:30], calls_after_first))
    check('the run past the limit is held', 'Held for review' in second)
    check('a dispatch block spends no tokens', len(calls) == calls_after_first, len(calls))

    r = latest_run('linkedin_optimization')
    check('a dispatch block is recorded as blocked',
          r.status == 'blocked' and r.error_code == 'dispatch_blocked',
          (r.status, r.error_code))

    # Capture failing must never cost the user their artifact.
    with app.app_context():
        obs.save_setting('obs_capture_enabled', False)
    returned = run_agent('# Rate Card\n\nNo telemetry this time.\n')
    check('the agent still runs with capture switched off',
          'No telemetry this time' in returned)
    with app.app_context():
        obs.save_setting('obs_capture_enabled', True)

    # -- 19a. The free tier (L0) --------------------------------------------
    # Most people meet the product before they pay, so the console has to see
    # the resume parse and the zone extraction, not just what follows a sale.
    print('\n19a. Free tier (L0)')

    from app.models.observability import LAYER_CODES, LAYER_FREE   # noqa: E402

    check('L0 is a first-class code on the layer axis',
          LAYER_FREE == 'L0' and 'L0' in LAYER_CODES, LAYER_CODES)
    check('L0 fits the existing column, so no migration is needed',
          len(LAYER_FREE) <= 2)

    # extract_expertise_zones with the stubbed client, as a free user would hit it.
    zones_json = ('[{"zone_name": "Data Platform Architecture", "summary": "s",'
                  ' "deliverables": [], "monetization_potential": "High",'
                  ' "estimated_hourly_rate": "$200-350/hr"},'
                  ' {"zone_name": "Analytics Leadership", "summary": "s",'
                  ' "deliverables": [], "monetization_potential": "Medium",'
                  ' "estimated_hourly_rate": "$150/hr"}]')
    claude_svc._client = lambda: _Client(zones_json)
    claude_svc._log_interaction = lambda *a, **k: None
    with app.app_context():
        zones = claude_svc.extract_expertise_zones('Fifteen years of data work.',
                                                   owner_id, resume_id='rsm123456')
    check('zone extraction still returns its zones', len(zones) == 2, len(zones))

    r = latest_run('expertise_zone_extraction')
    check('zone extraction is recorded', r is not None and r.status == 'success',
          r.status if r else None)
    check('it is tagged as free tier', r.layer == 'L0', r.layer)
    check('it has no simulation, because there is none yet', r.simulation_id is None)
    check('its tokens are captured, so the card can price it',
          r.prompt_tokens == 1234 and r.completion_tokens == 5678)

    with app.app_context():
        arts = AgentArtifact.query.filter_by(run_id=r.id).all()
        tools = [t.tool_name for t in AgentRunTool.query.filter_by(run_id=r.id).all()]
    check('the Claude call is on the free-tier trace', 'claude_generate' in tools, tools)
    check('the zones are registered as an artifact',
          len(arts) == 1 and arts[0].artifact_type == 'Expertise Zones',
          [a.artifact_type for a in arts])
    check('the artifact summary carries the zone names',
          (arts[0].summary_json or {}).get('count') == 2,
          arts[0].summary_json)
    check('the artifact references the resume, not an agent action',
          arts[0].content_ref == 'resume:rsm123456', arts[0].content_ref)

    # A failure on the free tier has to be visible too.
    class _Boom(object):
        class messages(object):
            @staticmethod
            def create(**kwargs):
                raise RuntimeError('upstream refused')

    claude_svc._client = lambda: _Boom()
    with app.app_context():
        try:
            claude_svc.extract_expertise_zones('x', owner_id)
            raised = False
        except Exception:
            raised = True
    check('a free-tier failure still propagates to the caller', raised)
    r = latest_run('expertise_zone_extraction')
    check('a free-tier failure is recorded as an error',
          r.status == 'error' and r.error_code == 'RuntimeError',
          (r.status, r.error_code))

    # The card itself.
    with app.app_context():
        obs.invalidate()
        cards = {c['layer']: c for c in obs.layer_rollups(24)}
    check('the overview now carries seven cards', len(cards) == 7, sorted(cards))
    free = cards.get('L0') or {}
    check('the free tier card is present and named', free.get('layer_name') == 'Free Tier')
    check('the card is labelled as pre-simulation',
          free.get('subtitle') == 'Before a simulation exists', free.get('subtitle'))
    check('the card counts free-tier runs', free.get('runs', 0) >= 2, free.get('runs'))
    check('the card shows the error rate', free.get('error_rate', 0) > 0,
          free.get('error_rate'))
    check('the card reports a flag rate', 'flag_rate' in free)
    check('the card prices the free tier', free.get('spend', 0) > 0, free.get('spend'))
    check('the card lists its artifact types',
          'Expertise Zones' in (free.get('top_artifacts') or []),
          free.get('top_artifacts'))

    res = client_for(admin_id).get('/api/admin/observability/layers')
    body = res.get_json()
    check('the API returns the free tier card',
          any(c['layer'] == 'L0' for c in body['layers']))
    res = client_for(admin_id).get('/api/admin/observability/runs?layer=L0')
    check('the free tier drills down to its runs',
          res.status_code == 200 and len(res.get_json()['runs']) >= 2,
          len(res.get_json()['runs']))
    res = client_for(admin_id).get('/api/admin/observability/agents?layer=L0')
    keys = [a['agent_key'] for a in res.get_json()['agents']]
    check('the free tier rolls up per agent',
          'expertise_zone_extraction' in keys, keys)
    res = client_for(admin_id).get('/api/admin/observability/meta')
    check('L0 is offered as a filter and a guardrail scope',
          any(l['code'] == 'L0' for l in res.get_json()['layers']))

    # An artifact whose content lives on the resume must still be fetchable.
    with app.app_context():
        from app.models.resume import Resume
        from app.models.resume_consent import ResumeConsent
        consent = ResumeConsent(id=generate_id(), user_id=owner_id,
                                tos_version='1', privacy_version='1',
                                checkbox_1=True, checkbox_2=True,
                                consent_method='test')
        db.session.add(consent)
        db.session.flush()
        resume = Resume(id='rsm123456', user_id=owner_id, consent_id=consent.id,
                        label='r', file_path='/tmp/r.pdf', file_type='pdf',
                        source='upload', parsed_text='x')
        resume.expertise_zones = [{'zone_name': 'Data Platform Architecture'}]
        db.session.add(resume)
        db.session.commit()
        art_id = AgentArtifact.query.filter_by(
            content_ref='resume:rsm123456').first().id

    res = client_for(admin_id).get('/api/admin/observability/artifacts/' + art_id)
    payload = res.get_json()
    check('a free-tier artifact resolves to the resume it lives on',
          res.status_code == 200 and payload['content_source'] == 'product_store',
          payload.get('content_source'))
    check('its content comes back',
          'Data Platform Architecture' in (payload.get('content') or ''))

    # Guardrails scope to L0 like any other layer.
    with app.app_context():
        from app.models.observability import GuardrailRule as GR
        db.session.add(GR(id=generate_id(), name='free-tier-watch', scope='layer',
                          scope_ref='L0', rule_type='content_policy', action='warn',
                          severity='low', definition_json={'forbid': ['guaranteed']}))
        db.session.commit()
        names = {x.name for x in gr.rules_for('L0', 'expertise_zone_extraction')}
        paid = {x.name for x in gr.rules_for('L1', 'rate_card')}
    check('a rule scoped to the free tier reaches it', 'free-tier-watch' in names)
    check('a free-tier rule does not reach the paid layers',
          'free-tier-watch' not in paid)

    # Nesting guard: a free-tier helper reached from inside an agent run must
    # attach to that run rather than orphan it.
    with app.app_context():
        parent = obs.start_run('rate_card', 1, user_id=owner_id)
        with obs.free_tier_run('zone_classification', owner_id):
            pass
        check('a nested free-tier call does not steal the thread',
              obs.current() is not None and obs.current().id == parent.id)
        obs.finish_run(status='success')
        nested_tools = [t.tool_name for t in
                        AgentRunTool.query.filter_by(run_id=parent.id).all()]
    check('it is recorded as a tool call on the parent run',
          'zone_classification' in nested_tools, nested_tools)

    # -- 19. Deployed without the migration ---------------------------------
    # The code can reach production before sql/050 is run. That has to read as
    # a set-up step with a named fix, not as "Internal server error" -- the
    # app-wide handler would otherwise hide the one failure with an easy answer.
    # Runs last: it drops the tables the sections above depend on.
    print('\n19. Deployed without the migration')

    owned = ('agent_runs', 'agent_run_tools', 'agent_events', 'agent_artifacts',
             'guardrail_rules', 'guardrail_policies', 'observability_flags',
             'admin_access_audit')
    with app.app_context():
        db.metadata.drop_all(db.engine, tables=[
            db.metadata.tables[t] for t in owned], checkfirst=True)

    res = client_for(admin_id).get('/api/admin/observability/layers')
    body = res.get_json() or {}
    check('a missing table answers 503, not 500', res.status_code == 503, res.status_code)
    check('the response is flagged as a set-up problem', body.get('setup_required') is True)
    check('the response names the migration to run',
          body.get('migration') == 'sql/050_observability.sql', body.get('migration'))
    check('the response names the missing table',
          body.get('missing_table') in owned, body.get('missing_table'))
    check('the message tells the admin what to do',
          'Run sql/050_observability.sql' in (body.get('error') or ''), body.get('error'))

    res = client_for(admin_id).get('/api/admin/guardrails/rules')
    check('the guardrails endpoint reports it too',
          res.status_code == 503 and (res.get_json() or {}).get('setup_required') is True)

    check('the console page still renders so the message has somewhere to show',
          client_for(admin_id).get('/admin/observability').status_code == 200)
    check('a non-admin is still refused before any of that',
          client_for(owner_id).get('/api/admin/observability/layers').status_code == 403)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        print('\nFailures:')
        for name in FAIL:
            print('  - ' + name)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
