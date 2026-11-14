"""Founder Track team, plan, agents and workspace (FOUNDER_PLAN.md WP4).

Faked Claude. Blueprints only for findings, overrides logged with the previous
value and limits enforced, plan DAG locked/ready, next-step ranking (unblocked,
then effort), explore across layers early, completing unblocks dependents,
skip needs a reason, L5 locked below the gate and while investor connection is
off, agent runs (output + founder_run_meta + observability run), external
agents create drafts awaiting approval and send nothing, compliance
regenerate / fallback, paused ventures refuse runs, approval routed to the
decision-right holder, unpaid users redirected. In-memory SQLite; requests and
SendGrid are blocked.

    python test_founder_plan.py
"""
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []

NEW_PY = ['app/services/founder_team_service.py', 'app/services/founder_orchestrator.py',
          'app/services/founder_agent_service.py', 'app/services/founder_compliance.py',
          'app/services/founder_autonomy.py', 'app/blueprints/founder/workspace.py']
NEW_TEMPLATES = ['workspace.html', 'team.html', 'plan.html', '_workspace_live.html', '_plan_live.html',
                 '_drafts.html', '_step_actions.html', '_ws_dialog.html', '_ws_nav.html']

DECK = ("Acme Dental cuts no-shows for dental clinics.\n"
        "Clinics lose $40,000 a year to missed appointments.\n"
        "We have not incorporated yet.")


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


class FakeClaude(object):
    """messages.create returns a forced deliver_output tool call. script maps
    an agent label to a list of payloads used in order; otherwise a clean
    default (with two drafts for external agents)."""

    def __init__(self):
        self.calls = []
        self.script = {}
        self.fail = False
        self.messages = self

    def create(self, **kw):
        if self.fail:
            raise RuntimeError('api down')
        label = re.match(r'You are the (.+?) agent', kw['system']).group(1)
        self.calls.append({'label': label, 'system': kw['system'], 'brief': kw['messages'][0]['content'][0]['text'],
                           'tools': kw['tools'], 'model': kw['model']})
        queue = self.script.get(label)
        if queue:
            out = queue.pop(0)
        else:
            out = {'content': '## %s\nWork based on the deck (pitch_deck@v1).' % label, 'confidence': 'medium',
                   'assumptions': ['Clinics will talk to us']}
            if 'drafts' in kw['tools'][0]['input_schema']['properties']:
                out['drafts'] = [{'target': 'Clinic manager', 'subject': 'Quick question about no-shows',
                                  'body': 'Hi, could we ask you three questions about missed appointments?'},
                                 {'target': 'Practice owner', 'subject': 'No-shows', 'body': 'Hello, a short note.'}]
        block = SimpleNamespace(type='tool_use', id='tu_%d' % len(self.calls), name='deliver_output', input=out)
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=900, output_tokens=300))


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
    sent = []
    import sendgrid

    class _NoSend(object):
        def __init__(self, *a, **k):
            sent.append('init')

        def send(self, *a, **k):
            sent.append('send')
            raise AssertionError('sendgrid must not be called')
    sendgrid.SendGridAPIClient = _NoSend

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
        from app.models.agent_plan import ActionApproval
        from app.models.audit_log import AuditLog
        from app.models.compliance import ComplianceEvent
        from app.models.founder import (AgentBlueprint, AgentParamOverride, DecisionRight, EvaluationFinding,
                                        EvaluationRun, FounderRunMeta, FounderVenture, VentureAgentOutput,
                                        VentureArtifact, VentureMember, VentureOutreachAction, VenturePlanStep,
                                        VentureStepEvent)
        from app.models.user import User
        from app.services import (founder_agent_service as fas, founder_autonomy, founder_compliance,
                                  founder_config, founder_feature, founder_orchestrator as fo,
                                  founder_team_service as fts, founder_ventures, legal_text)
        now = datetime.utcnow()

        def user(email, admin=False):
            u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x',
                     is_admin=admin, email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        founder, cofounder, other, admin = (user('founder@x.com'), user('co@x.com'), user('other@x.com'),
                                            user('admin@x.com', admin=True))
        F_ID, C_ID, O_ID, A_ID = founder.id, cofounder.id, other.id, admin.id
        v = founder_ventures.create_venture(founder, 'Acme Dental', 'Fewer no-shows', 'idea')
        V_ID = v.id
        co_member = VentureMember(venture_id=V_ID, user_id=C_ID, role='cofounder', status='active', joined_at=now)
        db.session.add(co_member)
        db.session.add(VentureArtifact(venture_id=V_ID, kind='pitch_deck', version=1, is_current=True,
                                       filename='deck.pdf', text_content=DECK, char_count=len(DECK),
                                       uploaded_by=F_ID))
        run = EvaluationRun(venture_id=V_ID, status='done', scores={'problem': 40}, finished_at=now)
        db.session.add(run)
        db.session.flush()

        def finding(dim, ftype, sev, title, agent=None, layer=1, superseded=False, cite='Clinics lose $40,000'):
            f = EvaluationFinding(venture_id=V_ID, evaluation_run_id=run.id, dimension=dim, layer=layer, ftype=ftype,
                                  severity=sev, title=title, detail='d', alpha=2, beta=2, confidence=0.5,
                                  citation_text=cite, citation_ref='pitch_deck@v1', recommended_agent=agent,
                                  superseded_at=now if superseded else None)
            db.session.add(f)
            return f
        f_pain = finding('problem', 'gap', 'high', 'Pain is not confirmed by customers', 'fnd_pain_evidence')
        finding('market', 'assumption', 'medium', 'Market size is top-down')
        finding('market', 'gap', 'low', 'No competitor list')            # same default agent: one blueprint
        finding('operations', 'gap', 'medium', 'Not incorporated', 'fnd_incorporation_checklist', layer=4,
                cite='We have not incorporated yet')
        finding('fundraising', 'gap', 'low', 'No investor materials', 'fnd_investor_readiness', layer=5)
        finding('customer', 'strength', 'low', 'Pain is costed', 'fnd_customer_interview')
        finding('team', 'gap', 'high', 'Old finding', 'fnd_hiring_plan', layer=4, superseded=True)
        db.session.commit()

        fake = FakeClaude()
        fas.AI_CLIENT = fake
        fas._spawn = lambda step_id, uid, trigger, inputs, prior=None: fas._run_by_id(step_id, uid, trigger, inputs, prior)

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

        def venture():
            return db.session.get(FounderVenture, V_ID)

        def step_for(key):
            return VenturePlanStep.query.filter_by(venture_id=V_ID, agent_key=key).first()

        base = '/founder/v/%s' % V_ID
        api = '/api/founder/v/%s' % V_ID

        print('\n2. Track off: every WP4 route is 404')
        login(F_ID)
        for path in (base, base + '/team', base + '/plan', api + '/next', api + '/live'):
            check('GET %s is 404 while off' % path.replace(V_ID, '<id>'), client.get(path).status_code == 404)
        check('POST team/build is 404 while off', client.post(api + '/team/build').status_code == 404)
        founder_feature.set_mode('founder_track', 'on', A_ID)

        print('\n3. Access and the paid gate')
        login(O_ID)
        check('a non-member gets 403 on the workspace', client.get(base).status_code == 403)
        check('a non-member gets 403 on team build', client.post(api + '/team/build').status_code == 403)
        login(None)
        check('anonymous gets 404', client.get(base).status_code == 404)
        login(F_ID)
        r = client.get(base)
        html = r.get_data(as_text=True)
        check('unpaid workspace renders a locked card with the checkout link',
              r.status_code == 200 and 'unlock with the Founder Track' in html and '/checkout' in html)
        r = client.get(base + '/team')
        check('unpaid owner: /team redirects to checkout', r.status_code == 302 and '/checkout' in r.location,
              r.location)
        r = client.get(base + '/plan')
        check('unpaid owner: /plan redirects to checkout', r.status_code == 302 and '/checkout' in r.location)
        login(C_ID)
        r = client.get(base + '/team')
        check('unpaid co-founder: /team redirects to the report', r.status_code == 302 and '/report' in r.location)
        login(F_ID)
        r = client.post(api + '/team/build')
        check('unpaid: team build API is 402 with the checkout URL',
              r.status_code == 402 and '/checkout' in (r.get_json() or {}).get('checkout_url', ''))
        check('unpaid: no blueprints were created', AgentBlueprint.query.count() == 0)
        vv = venture()
        vv.paid_at, vv.amount_cents = now, 49900
        db.session.commit()

        print('\n4. Team: one blueprint per agent tied to an open finding')
        r = client.get(base + '/team')
        check('paid: /team renders and builds the team', r.status_code == 200, r.status_code)
        bps = {b.agent_key: b for b in AgentBlueprint.query.filter_by(venture_id=V_ID).all()}
        check('blueprints only for agents tied to open findings',
              set(bps) == {'fnd_pain_evidence', 'fnd_market_sizing', 'fnd_incorporation_checklist',
                           'fnd_investor_readiness'}, sorted(bps))
        check('strengths and superseded findings recommend no agent',
              'fnd_customer_interview' not in bps and 'fnd_hiring_plan' not in bps)
        check('the blueprint links the most severe finding', bps['fnd_pain_evidence'].finding_id == f_pain.id)
        pe = bps['fnd_pain_evidence'].params
        check('autonomy default from the matrix row for the action class (customer_message: 2)',
              pe['autonomy_level'] == 2 and bps['fnd_market_sizing'].params['autonomy_level'] == 4)
        check('caps from founder_config: budget cap and (external agents) action cap',
              pe['budget_cap'] == founder_config.get('fnd_budget_cap_cents')
              and pe['action_cap'] == founder_config.get('fnd_action_cap_per_day')
              and bps['fnd_market_sizing'].params['action_cap'] is None)
        check('scope comes from the evaluation (finding focus, dimension, source, stage)',
              pe['scope'].get('focus') == f_pain.title and pe['scope'].get('dimension') == 'problem'
              and pe['scope'].get('source') == 'pitch_deck@v1' and pe['scope'].get('stage') == 'idea', pe['scope'])
        check('catalog defaults are kept (stopping rule, success metric)',
              pe['stopping_rule'] and pe['success_metric'])
        again = fts.build(venture(), founder)
        check('build is idempotent', len(again) == 4 and AgentBlueprint.query.count() == 4)

        print('\n5. Overrides: logged with the previous value, limits enforced')
        ms = bps['fnd_market_sizing']
        r = client.post(api + '/team/override', json={'param_key': 'autonomy_level', 'value': 1,
                                                      'blueprint_id': ms.id})
        row = AgentParamOverride.query.filter_by(blueprint_id=ms.id).first()
        check('lowering autonomy is always allowed', r.status_code == 200, r.get_data(as_text=True)[:200])
        check('the override row keeps the previous and the new value',
              row is not None and json.loads(row.previous_value) == 4 and json.loads(row.new_value) == 1
              and row.user_id == F_ID and row.param_key == 'autonomy_level')
        eff = fts.effective(venture(), ms)
        check('the recommended value stays visible beside the one in force',
              eff['recommended']['autonomy_level'] == 4 and eff['current']['autonomy_level'] == 1)
        r = client.post(api + '/team/override', json={'param_key': 'autonomy_level', 'value': 4,
                                                      'blueprint_id': bps['fnd_pain_evidence'].id})
        check('raising above the matrix max (customer_message max 3) is refused with 403',
              r.status_code == 403 and 'at most' in (r.get_json() or {}).get('error', ''), r.get_data(as_text=True))
        r = client.post(api + '/team/override', json={'param_key': 'autonomy_level', 'value': 3,
                                                      'blueprint_id': bps['fnd_pain_evidence'].id})
        check('raising up to the matrix max is allowed', r.status_code == 200
              and founder_autonomy.level_for(venture(), 'fnd_pain_evidence') == 3)
        check('can_raise follows the matrix', founder_autonomy.can_raise('research', 4)
              and not founder_autonomy.can_raise('financial_message', 3) and founder_autonomy.can_raise('application', 3))
        founder_config.set('fnd_autonomy_matrix', {'first_contact': {'default': 2, 'max': 2}}, A_ID)
        founder_config.set('fnd_first_contact_cap', 2, A_ID)
        check('the first-contact cap also holds application forms',
              not founder_autonomy.can_raise('fnd_application_form', 3)
              and founder_autonomy.limits('application')['max'] == 2)
        founder_config.set('fnd_first_contact_cap', 3, A_ID)
        founder_config.set('fnd_autonomy_matrix', {'first_contact': {'default': 2, 'max': 3}}, A_ID)
        r = client.post(api + '/team/override', json={'param_key': 'autonomy_level', 'value': 4, 'layer': 1})
        d = r.get_json() or {}
        lay = AgentParamOverride.query.filter_by(venture_id=V_ID, layer=1).first()
        check('a layer-wide raise applies within each agent\'s limit and says which stay lower',
              r.status_code == 200 and 'lower level' in d.get('note', '')
              and fts.effective_param(venture(), 'fnd_market_sizing', 'autonomy_level') == 4
              and fts.effective_param(venture(), 'fnd_pain_evidence', 'autonomy_level') == 3, d)
        check('the layer override is logged with the previous values per agent',
              lay is not None and json.loads(lay.previous_value) == {'fnd_market_sizing': 1, 'fnd_pain_evidence': 3},
              lay and lay.previous_value)
        mtx = founder_config.get('fnd_autonomy_matrix')
        mtx['research'] = {'default': 3, 'max': 3}
        founder_config.set('fnd_autonomy_matrix', mtx, A_ID)
        check('an admin lowering the matrix lowers the level in force (re-clamped on read)',
              fts.effective_param(venture(), 'fnd_market_sizing', 'autonomy_level') == 3)
        mtx['research'] = {'default': 4, 'max': 4}
        founder_config.set('fnd_autonomy_matrix', mtx, A_ID)
        r = client.post(api + '/team/override', json={'param_key': 'budget_cap', 'value': 500,
                                                      'blueprint_id': ms.id})
        check('a budget cap above the platform limit is refused', r.status_code == 403, r.status_code)
        r = client.post(api + '/team/override', json={'param_key': 'voice', 'value': 'Plain and direct',
                                                      'blueprint_id': ms.id})
        check('text parameters can be overridden', r.status_code == 200
              and fts.effective_param(venture(), 'fnd_market_sizing', 'voice') == 'Plain and direct')
        r = client.post(api + '/team/override', json={'param_key': 'secret', 'value': 1, 'blueprint_id': ms.id})
        check('an unknown parameter is refused', r.status_code == 400)
        login(O_ID)
        r = client.post(api + '/team/override', json={'param_key': 'voice', 'value': 'x', 'blueprint_id': ms.id})
        check('a non-member cannot override', r.status_code == 403)
        login(F_ID)
        html = client.get(base + '/team').get_data(as_text=True)
        check('the team page shows recommended and in-force values side by side',
              'Recommended' in html and 'In force' in html and 'changed' in html and 'Changes you made' in html)
        check('the team page disables levels above the limit', 'above the limit' in html)

        print('\n6. Plan: DAG with locked and ready steps')
        r = client.post(api + '/plan/build')
        check('building a plan with no accepted agents is refused', r.status_code == 409, r.status_code)
        for layer in (1, 4, 5):
            r = client.post(api + '/team/accept', json={'layer': layer})
            check('accept layer %d' % layer, r.status_code == 200 and (r.get_json() or {}).get('accepted') >= 1)
        r = client.post(api + '/plan/build')
        check('plan builds', r.status_code == 200, r.get_data(as_text=True)[:300])
        keys = {s.agent_key for s in VenturePlanStep.query.filter_by(venture_id=V_ID, kind='agent').all()}
        check('plan holds accepted agents plus their catalog prerequisites',
              {'fnd_pain_evidence', 'fnd_market_sizing', 'fnd_incorporation_checklist', 'fnd_investor_readiness',
               'fnd_problem_statement', 'fnd_runway_budget'} <= keys, sorted(keys))
        proof = VenturePlanStep.query.filter_by(venture_id=V_ID, kind='proof').all()
        check('the proof-of-concept path is in the plan (agree, run, paid) with the PoC plan agent',
              len(proof) == 3 and 'fnd_poc_plan' in keys and 'fnd_pilot_offer' not in keys)
        st = {s.agent_key or s.title: s.state for s in fo.steps(venture())}
        check('steps with no prerequisites are ready',
              st['fnd_problem_statement'] == 'ready' and st['fnd_incorporation_checklist'] == 'ready'
              and st['fnd_runway_budget'] == 'ready', st)
        check('steps waiting on prerequisites are locked',
              st['fnd_pain_evidence'] == 'locked' and st['fnd_market_sizing'] == 'locked'
              and st['fnd_poc_plan'] == 'locked' and all(p.state == 'locked' for p in proof))
        check('the L5 agent is locked', st['fnd_investor_readiness'] == 'locked')
        agree = fo.proof_step(venture(), 'agree')
        run_s = fo.proof_step(venture(), 'run')
        check('proof steps chain: agree waits for the PoC plan agent, run waits for agree',
              agree.prereq_keys == ['fnd_poc_plan'] and run_s.prereq_keys == [agree.id])
        n_steps = VenturePlanStep.query.count()
        fo.build_plan(venture(), founder)
        check('rebuilding the plan adds nothing new', VenturePlanStep.query.count() == n_steps)
        ev = VentureStepEvent.query.filter_by(venture_id=V_ID, to_state='ready').count()
        au = AuditLog.query.filter_by(action='founder_step_ready').count()
        check('every transition writes a venture_step_event and an AuditLog row', ev >= 3 and au == ev, (ev, au))

        print('\n7. Next step: unblocked first, then shortest effort; explore early')
        nxt = fo.next_steps(venture())
        check('primary is the step that unblocks the most (problem statement unblocks 2)',
              nxt['primary']['agent_key'] == 'fnd_problem_statement' and nxt['primary']['unblocks'] == 2, nxt['primary'])
        alts = [a['agent_key'] for a in nxt['alternates']]
        check('ties on unblocked go to the smaller effort (incorporation 2 before runway 3)',
              alts == ['fnd_incorporation_checklist', 'fnd_runway_budget'], alts)
        r = client.get(api + '/next')
        check('the next-step API answers with primary and alternates', r.status_code == 200
              and (r.get_json() or {}).get('primary', {}).get('id') == step_for('fnd_problem_statement').id)
        r = client.post('/api/founder/steps/%s/complete' % step_for('fnd_problem_statement').id,
                        json={'evidence_note': 'Wrote it with two clinic managers'})
        d = r.get_json() or {}
        unl = {u['agent_key'] for u in d.get('unlocked', [])}
        check('completing a step unblocks its dependents', r.status_code == 200
              and unl == {'fnd_pain_evidence', 'fnd_market_sizing'}, unl)
        ps = step_for('fnd_problem_statement')
        check('the evidence note is kept and the step is done', ps.state == 'done'
              and ps.evidence_note == 'Wrote it with two clinic managers' and ps.completed_at)
        founder_config.set('fnd_next_alternates', 1, A_ID)
        nxt = fo.next_steps(venture())
        picked = [nxt['primary']['agent_key']] + [a['agent_key'] for a in nxt['alternates']]
        check('early on (explore) the pick spans layers: the best L1 step and the best L4 step',
              nxt['explore'] and picked == ['fnd_pain_evidence', 'fnd_incorporation_checklist'], picked)
        real_explore = fo.is_exploring
        fo.is_exploring = lambda rows: False
        nxt = fo.next_steps(venture())
        picked = [nxt['primary']['agent_key']] + [a['agent_key'] for a in nxt['alternates']]
        fo.is_exploring = real_explore
        check('later (exploit) the pick follows the ranking and the layer that is moving',
              not nxt['explore'] and picked == ['fnd_pain_evidence', 'fnd_market_sizing'], picked)
        founder_config.set('fnd_next_alternates', 3, A_ID)

        print('\n8. Skip needs a reason')
        msid = step_for('fnd_market_sizing').id
        r = client.post('/api/founder/steps/%s/skip' % msid, json={'reason': ' '})
        check('skip without a reason is refused', r.status_code == 400 and step_for('fnd_market_sizing').state == 'ready')
        r = client.post('/api/founder/steps/%s/skip' % msid, json={'reason': 'We sized it with an advisor already'})
        check('skip with a reason records it', r.status_code == 200 and step_for('fnd_market_sizing').state == 'skipped'
              and step_for('fnd_market_sizing').skip_reason == 'We sized it with an advisor already')
        r = client.post('/api/founder/steps/%s/complete' % agree.id)
        check('a locked step cannot be completed', r.status_code == 409)

        print('\n9. Agent run: output, founder_run_meta, observability run')
        from app.services import observability_service as obs
        from app.models.observability import AgentRun
        inc = step_for('fnd_incorporation_checklist')
        r = client.post('/api/founder/steps/%s/run' % inc.id, json={})
        check('the run API accepts (202)', r.status_code == 202, r.get_data(as_text=True)[:300])
        inc = step_for('fnd_incorporation_checklist')
        outs = VentureAgentOutput.query.filter_by(plan_step_id=inc.id).all()
        check('one Claude call per run', len(fake.calls) == 1, len(fake.calls))
        call = fake.calls[0]
        check('the system prompt is built from the catalog entry and the shared rules',
              'Incorporation Checklist' in call['system'] and 'Give no legal, tax, securities' in call['system']
              and 'Output sections' in call['system'])
        check('the briefing carries artifacts, open findings with citations and the parameters in force',
              'We have not incorporated yet' in call['brief'] and 'pitch_deck@v1' in call['brief']
              and '"autonomy_level"' in call['brief'] and 'open_findings' in call['brief'])
        check('the model comes from the founder catalog (haiku)', 'haiku' in call['model'], call['model'])
        check('internal agent: output stored and the step is done with it attached',
              len(outs) == 1 and inc.state == 'done' and inc.output_id == outs[0].id)
        check('outputs carry the not-advice note', legal_text.get('founder_not_advice') in outs[0].content)
        meta = FounderRunMeta.query.filter_by(plan_step_id=inc.id).first()
        hdr = db.session.get(AgentRun, meta.obs_run_id) if meta else None
        if obs.setting('obs_capture_enabled') and obs.layer_code('F4') == 'F4':
            check('founder_run_meta links an observability run on layer F4 with the agent key',
                  meta is not None and hdr is not None and hdr.layer == 'F4'
                  and hdr.agent_key == 'fnd_incorporation_checklist' and outs[0].obs_run_id == hdr.id
                  and meta.trigger == 'founder' and meta.autonomy_level == 4 and (hdr.prompt_tokens or 0) > 0,
                  (meta and meta.obs_run_id, hdr and hdr.layer))
        else:
            check('observability not available: the run still completes', inc.state == 'done')
        brief_upstream = fas.briefing(step_for('fnd_pain_evidence'), venture(), {}, None)
        check('upstream step outputs and evidence reach dependent agents',
              'Wrote it with two clinic managers' in brief_upstream)

        print('\n10. Compliance: regenerate once, then fall back')
        banned = __import__('app.services.compliance_filter', fromlist=['_cfg'])._cfg()['banned'][0]
        check('check() flags negotiation, return projections and banned phrases',
              {i['rule'] for i in founder_compliance.check(
                  'We accept your terms. Investors will make 10x returns. %s' % banned, 3)}
              >= {'negotiation', 'return_projection', 'banned_phrase'})
        sol = 'Post this on LinkedIn so everyone knows we are raising. Anyone interested in investing can join.'
        check('public solicitation is flagged under 506(b) and undecided, not under 506(c)',
              founder_compliance.check(sol, 5, '506b') and founder_compliance.check(sol, 5, None)
              and not founder_compliance.check(sol, 5, '506c'))
        check('clean planning text passes', not founder_compliance.check(
            'List the documents a startup attorney will ask for. This is an estimate based on the deck.', 4))
        fake.script['Runway and Budget'] = [
            {'content': 'Runway plan. We accept the terms of the first offer.', 'confidence': 'low'},
            {'content': 'Runway plan: 9 months at current spend (estimate from the deck).', 'confidence': 'medium'}]
        n0 = len(fake.calls)
        rb = step_for('fnd_runway_budget')
        out = fas.execute(rb, founder)
        check('a failing output is regenerated once with stricter rules',
              len(fake.calls) == n0 + 2 and 'broke these SimiSI rules' in fake.calls[-1]['system'])
        check('the clean second answer is stored', 'accept the terms' not in out.content and '9 months' in out.content
              and step_for('fnd_runway_budget').state == 'done')
        ce = ComplianceEvent.query.filter_by(agent='fnd_runway_budget').all()
        check('the breach is a compliance_events row (regenerated)',
              any(e.rule == 'fnd_negotiation' and e.outcome == 'regenerated' for e in ce), [(e.rule, e.outcome) for e in ce])

        print('\n11. L5 gate: rung and investor switch')
        ir = step_for('fnd_investor_readiness')
        check('runway done, but the L5 step stays locked while investor connection is off',
              ir.state == 'locked' and 'investor connection' in (fo.gate_reason(venture(), ir) or ''))
        try:
            fas.execute(ir, founder)
            check('a locked L5 step refuses to run', False)
        except fas.AgentError as exc:
            check('a locked L5 step refuses to run', exc.code == 'locked')
        founder_config.set('fnd_counsel_signoff_at', '2026-11-01', A_ID)
        founder_feature.set_mode('founder_investor', 'on', A_ID)
        fo.refresh(venture())
        ir = step_for('fnd_investor_readiness')
        check('investor on but rung 1 < gate 2: still locked, the reason names the rung',
              ir.state == 'locked' and 'rung 2' in (fo.gate_reason(venture(), ir) or ''), fo.gate_reason(venture(), ir))
        check('intro agents use the intros gate', fo.gate_rung('fnd_warm_intro') == 4
              and fo.gate_rung('fnd_application_form') == 2)
        vv = venture()
        vv.rung = 2
        db.session.commit()
        fo.refresh(venture())
        check('at the gate rung with investor on but no exemption recorded, L5 stays locked',
              step_for('fnd_investor_readiness').state == 'locked'
              and 'exemption' in (fo.gate_reason(venture(), step_for('fnd_investor_readiness')) or ''))
        from app.services import founder_investors
        founder_investors.record_exemption(venture(), db.session.get(User, F_ID), 'undecided', 'test')
        fo.refresh(venture())
        check('at the gate rung with investor on, the L5 step opens', step_for('fnd_investor_readiness').state == 'ready')
        founder_feature.set_mode('founder_investor', 'off', A_ID)
        fo.refresh(venture())
        check('switching investor connection off locks it again', step_for('fnd_investor_readiness').state == 'locked')

        print('\n12. External agent: drafts awaiting approval, nothing sent')
        pe_step = step_for('fnd_pain_evidence')
        out = fas.execute(pe_step, founder)
        pe_step = step_for('fnd_pain_evidence')
        drafts = VentureOutreachAction.query.filter_by(plan_step_id=pe_step.id).all()
        check('customer-facing agent: drafts created in awaiting_approval',
              len(drafts) == 2 and all(a.status == 'awaiting_approval' for a in drafts)
              and all(a.action_type == 'customer_message' and a.sent_at is None for a in drafts))
        check('the step waits for approval with the output attached',
              pe_step.state == 'awaiting_approval' and pe_step.output_id == out.id)
        check('the level in force is recorded on the draft', all(a.level_required == 3 and a.level_applied is None
                                                                 for a in drafts), [(a.level_required, a.level_applied) for a in drafts])
        check('nothing was sent (SendGrid never called)', sent == [])
        html = client.get(base).get_data(as_text=True)
        check('the workspace lists the drafts waiting for approval', 'Waiting for approval' in html
              and 'Quick question about no-shows' in html)

        print('\n13. Approval goes to the decision-right holder')
        dr = DecisionRight.query.filter_by(venture_id=V_ID, decision_type='customer_contact').first()
        dr.holder_member_id = co_member.id
        db.session.commit()
        check('the approver is the co-founder who holds customer_contact',
              founder_autonomy.approver_for(drafts[0]).user_id == C_ID)
        r = client.post('/api/founder/actions/%s/approve' % drafts[0].id)
        check('the owner cannot approve when a co-founder holds the right (403)',
              r.status_code == 403 and 'final say' in (r.get_json() or {}).get('error', ''), r.get_data(as_text=True))
        html = client.get(base).get_data(as_text=True)
        check('the owner sees who approves instead of the buttons', 'has the final say on this' in html)
        login(C_ID)
        r = client.post('/api/founder/actions/%s/approve' % drafts[0].id)
        a0 = db.session.get(VentureOutreachAction, drafts[0].id)
        check('the holder approves: status approved, approver recorded, not sent',
              r.status_code == 200 and a0.status == 'approved' and a0.approved_by == C_ID and a0.sent_at is None)
        check('the approval is an ActionApproval row', ActionApproval.query.filter_by(
            target_type='fnd_outreach', target_id=a0.id, decided_by=C_ID, decision='approve').count() == 1)
        check('one draft still pending: the step keeps waiting', step_for('fnd_pain_evidence').state == 'awaiting_approval')
        r = client.post('/api/founder/actions/%s/reject' % drafts[1].id, json={'reason': 'Too pushy'})
        check('declining the last draft settles the step: done (one draft approved)',
              r.status_code == 200 and step_for('fnd_pain_evidence').state == 'done'
              and db.session.get(VentureOutreachAction, drafts[1].id).block_reason == 'Too pushy')
        r = client.post('/api/founder/actions/%s/approve' % drafts[0].id)
        check('a decided draft cannot be decided again', r.status_code == 409)
        check('still nothing sent', sent == [] and VentureOutreachAction.query.filter_by(status='sent').count() == 0)
        login(F_ID)
        check('the PoC plan agent is ready after pain evidence', step_for('fnd_poc_plan').state == 'ready')

        print('\n14. Fallback: a second failing answer becomes a safe note')
        bad = {'content': 'Tell investors they will make 10x returns.', 'confidence': 'high',
               'drafts': [{'body': 'We accept your terms.'}]}
        fake.script['Proof-of-Concept Plan'] = [dict(bad), dict(bad)]
        poc = step_for('fnd_poc_plan')
        out = fas.execute(poc, founder)
        poc = step_for('fnd_poc_plan')
        check('the safe note is stored instead of the output', 'did not pass' in out.content
              and '10x' not in out.content)
        check('no drafts are created from a withheld answer and the step goes back to ready',
              VentureOutreachAction.query.filter_by(plan_step_id=poc.id).count() == 0 and poc.state == 'ready')
        check('the fallback is logged', ComplianceEvent.query.filter_by(agent='fnd_poc_plan', outcome='fallback').count() >= 1)

        print('\n15. Refusals: paused venture, guardrail, admin pause, daily cap, failure')
        vv = venture()
        vv.paused_at = now
        db.session.commit()
        n0 = len(fake.calls)
        r = client.post('/api/founder/steps/%s/run' % poc.id, json={})
        check('a paused venture refuses runs (409, code paused) and calls no model',
              r.status_code == 409 and (r.get_json() or {}).get('code') == 'paused' and len(fake.calls) == n0)
        vv.paused_at = None
        db.session.commit()
        from app.services import guardrail_service
        real_cd = guardrail_service.check_dispatch
        guardrail_service.check_dispatch = lambda *a, **k: guardrail_service.Decision(blocked=True)
        try:
            fas.execute(poc, founder)
            check('a guardrail block refuses the run', False)
        except fas.AgentError as exc:
            check('a guardrail block refuses the run and resets the step to ready',
                  exc.code == 'blocked' and step_for('fnd_poc_plan').state == 'ready' and len(fake.calls) == n0)
        guardrail_service.check_dispatch = real_cd
        try:
            from app.services import founder_obs_service
            founder_obs_service.set_agent_paused('fnd_poc_plan', True, A_ID)
            r = client.post('/api/founder/steps/%s/run' % poc.id, json={})
            check('an agent paused by an admin (WP7) refuses runs', r.status_code == 409
                  and (r.get_json() or {}).get('code') == 'blocked', r.get_data(as_text=True))
            founder_obs_service.set_agent_paused('fnd_poc_plan', False, A_ID)
        except ImportError:
            print('  (WP7 founder_obs_service not installed; admin pause check skipped)')
        founder_config.set('fnd_action_cap_per_day', 2, A_ID)
        r = client.post('/api/founder/steps/%s/run' % poc.id, json={})
        check('the daily action cap stops external agents (429, code cap)',
              r.status_code == 429 and (r.get_json() or {}).get('code') == 'cap' and len(fake.calls) == n0)
        founder_config.set('fnd_action_cap_per_day', 10, A_ID)
        fake.fail = True
        r = client.post('/api/founder/steps/%s/run' % poc.id, json={})
        check('a model failure leaves the step ready with nothing saved',
              step_for('fnd_poc_plan').state == 'ready'
              and VentureAgentOutput.query.filter_by(plan_step_id=poc.id).count() == 1)
        fake.fail = False
        r = client.post('/api/founder/steps/%s/run' % agree.id, json={})
        check('a non-agent step cannot be run', r.status_code in (400, 409))

        print('\n15b. Review fixes: one run per step, cap per run, closed steps, re-runs, pins')
        from app.models.founder import VentureStepEvent
        poc = step_for('fnd_poc_plan')
        agent_poc = __import__('app.services.founder_registry', fromlist=['get']).get('fnd_poc_plan')
        n0 = len(fake.calls)
        prior = fas._claim(poc, agent_poc, founder)
        check('a claim marks the step in progress with a live run marker',
              step_for('fnd_poc_plan').state == 'in_progress' and fas.run_active(step_for('fnd_poc_plan')))
        r = client.post('/api/founder/steps/%s/run' % poc.id, json={})
        check('a second run while the first works is refused (409, code running) and calls no model',
              r.status_code == 409 and (r.get_json() or {}).get('code') == 'running' and len(fake.calls) == n0,
              r.get_data(as_text=True)[:200])
        html = client.get(base + '/plan').get_data(as_text=True)
        check('the plan says the agent is working instead of offering another run',
              'The agent is working on this step' in html and 'Run again' not in html)
        # What a racing request read before the winner changed the row.
        cur = step_for('fnd_poc_plan')
        racing = SimpleNamespace(id=cur.id, venture_id=cur.venture_id, state=cur.state, completed_at=None,
                                 updated_at=cur.updated_at - timedelta(seconds=5))
        try:
            fas._claim(racing, agent_poc, founder)
            check('a racing claim that read an older row is refused', False)
        except fas.AgentError as exc:
            check('a racing claim that read an older row is refused', exc.code == 'running')
        db.session.rollback()
        fas._restore(step_for('fnd_poc_plan'), None, 'test reset', prior)
        check('closing the run returns the step to its prior state and clears the marker',
              step_for('fnd_poc_plan').state == 'ready' and not fas.run_active(step_for('fnd_poc_plan')))
        fas._mark(step_for('fnd_poc_plan'), 'agent_started', 'abandoned run')
        db.session.commit()
        mk = (VentureStepEvent.query.filter(VentureStepEvent.plan_step_id == poc.id,
                                            VentureStepEvent.note.like('{"event": "agent_started"%'))
              .order_by(VentureStepEvent.created_at.desc()).first())
        mk.created_at = datetime.utcnow() - timedelta(minutes=fas.RUN_TIMEOUT_MINUTES + 1)
        db.session.commit()
        check('a claim older than the timeout no longer blocks runs', not fas.run_active(step_for('fnd_poc_plan')))

        today = fas.drafts_today(venture())
        founder_config.set('fnd_action_cap_per_day', today + 1, A_ID)
        out = fas.execute(step_for('fnd_poc_plan'), founder)
        poc = step_for('fnd_poc_plan')
        kept = VentureOutreachAction.query.filter_by(plan_step_id=poc.id, status='awaiting_approval').all()
        check('a run stores only the drafts left under the daily cap (2 offered, 1 kept)',
              len(kept) == 1 and fas.drafts_today(venture()) == today + 1 and poc.state == 'awaiting_approval',
              (len(kept), fas.drafts_today(venture())))
        founder_config.set('fnd_action_cap_per_day', 10, A_ID)
        fo.skip(poc, founder, 'Not now, we have a plan already')
        kd = db.session.get(VentureOutreachAction, kept[0].id)
        check('skipping a step closes its undecided drafts', kd.status == 'blocked'
              and 'skipped' in (kd.block_reason or ''))
        check('the closed drafts no longer show as waiting',
              not any(c['drafts'] for L in fo.plan_view(venture())['lanes'] for c in L['steps'] if c['id'] == poc.id))

        tgt = step_for('fnd_runway_budget')
        ag = __import__('app.services.founder_registry', fromlist=['get']).get(tgt.agent_key)
        fas.preflight(tgt, venture(), 'rerun')
        pr = fas._claim(tgt, ag, founder)
        n_out = VentureAgentOutput.query.filter_by(plan_step_id=tgt.id).count()
        fo.complete(db.session.get(VenturePlanStep, tgt.id), founder, evidence_note='Finished it by hand')
        o2 = fas._run(db.session.get(VenturePlanStep, tgt.id), venture(), ag, founder, 'rerun', None, pr)
        t2 = db.session.get(VenturePlanStep, tgt.id)
        check('a step closed while its agent ran stays closed; the output is kept and the marker clears',
              t2.state == 'done' and o2 is not None and t2.evidence_note == 'Finished it by hand'
              and VentureAgentOutput.query.filter_by(plan_step_id=tgt.id).count() == n_out + 1
              and not fas.run_active(t2), t2.state)

        inc = step_for('fnd_incorporation_checklist')
        done_at = inc.completed_at
        fake.fail = True
        try:
            fas.execute(inc, None, trigger='rerun')
            check('a failed re-run raises', False)
        except fas.AgentError:
            inc = step_for('fnd_incorporation_checklist')
            check('a failed re-run of a finished step leaves it done with its completion time',
                  inc.state == 'done' and inc.completed_at == done_at, (inc.state, inc.completed_at, done_at))
        fake.fail = False
        guardrail_service.check_dispatch = lambda *a, **k: guardrail_service.Decision(model_override='claude-pinned-test')
        try:
            fas.execute(step_for('fnd_incorporation_checklist'), None, trigger='rerun')
        finally:
            guardrail_service.check_dispatch = real_cd
        check('a guardrail model pin is the model the agent calls', fake.calls[-1]['model'] == 'claude-pinned-test',
              fake.calls[-1]['model'])
        check('the re-run finishes done again', step_for('fnd_incorporation_checklist').state == 'done')

        check('assumptions and draft targets go through the compliance check',
              founder_compliance.check(fas._all_text({'content': 'Plan.', 'assumptions': [
                  'Investors will make 10x returns']}), 3)
              and founder_compliance.check(fas._all_text({'content': 'Plan.', 'drafts': [
                  {'target': 'Investors will make 10x returns', 'body': 'Hello.'}]}), 3))

        check('scope shown as "key: value; ..." is read back into separate criteria',
              fts._parse_scope('count: 8; stage: idea; source: pitch_deck@v1')
              == {'count': 8, 'stage': 'idea', 'source': 'pitch_deck@v1'}
              and fts._parse_scope('clinics in Ohio') == {'criteria': 'clinics in Ohio'})

        layer_bps = fts.blueprints(venture())
        if layer_bps:
            bp0 = layer_bps[0]
            bp0.status = 'declined'
            db.session.commit()
            fts.accept(venture(), founder, bp0.layer)
            check('accepting a layer leaves declined agents declined',
                  db.session.get(AgentBlueprint, bp0.id).status == 'declined')
            bp0 = db.session.get(AgentBlueprint, bp0.id)
            bp0.status = 'accepted'
            db.session.commit()

        # An L5 gate that closes while drafts wait: deciding the last draft must not 500.
        ir = step_for('fnd_investor_readiness')
        ir.state = 'awaiting_approval'
        db.session.add(VentureOutreachAction(venture_id=V_ID, plan_step_id=ir.id, action_type='research',
                                             status='awaiting_approval', body='Draft'))
        db.session.commit()
        ia = VentureOutreachAction.query.filter_by(plan_step_id=ir.id, status='awaiting_approval').first()
        holder = founder_autonomy.approver_for(ia)
        login(holder.user_id)
        r = client.post('/api/founder/actions/%s/approve' % ia.id)
        login(F_ID)
        ir = step_for('fnd_investor_readiness')
        check('approving the last draft on a gated L5 step answers 200 and the step relocks',
              r.status_code == 200 and ir.state == 'locked', (r.status_code, ir.state))

        print('\n16. Pages, live area and stream')
        for path in (base, base + '/plan', base + '/team'):
            r = client.get(path)
            check('GET %s renders' % path.replace(V_ID, '<id>'), r.status_code == 200, r.status_code)
        html = client.get(base).get_data(as_text=True)
        check('the workspace shows one next step, layer lanes and the activity log',
              'Your next step' in html and 'L1 Validate' in html and 'L5 Capitalize' in html and 'Activity' in html
              and 'founder_workspace.js' in html)
        html = client.get(base + '/plan').get_data(as_text=True)
        check('the plan page says why a step is locked', 'Waiting on' in html or 'Opens at' in html
              or 'investor connection' in html)
        r = client.get(api + '/live')
        check('the live fragment re-renders', r.status_code == 200 and 'Your next step' in r.get_data(as_text=True))
        r = client.get(api + '/live?view=plan')
        check('the plan live fragment re-renders', r.status_code == 200 and 'fw-steps' in r.get_data(as_text=True))
        r = client.get(api + '/stream')
        check('the SSE stream answers text/event-stream', r.status_code == 200
              and r.mimetype == 'text/event-stream')
        r.close()
        o = VentureAgentOutput.query.filter_by(plan_step_id=inc.id).first()
        r = client.get('/api/founder/outputs/%s' % o.id)
        check('an output is viewable by a member', r.status_code == 200
              and (r.get_json() or {}).get('output', {}).get('id') == o.id)
        login(O_ID)
        check('a non-member cannot read outputs', client.get('/api/founder/outputs/%s' % o.id).status_code == 403)
        login(F_ID)

        print('\n17. Copy and prompts carry no banned phrases')
        import test_compliance_phrases as tcp
        hits = []
        for t in NEW_TEMPLATES:
            hits += tcp.scan(tcp.visible(io.open(os.path.join(ROOT, 'app/templates/founder', t), encoding='utf-8').read()))
        for f in NEW_PY:
            hits += tcp.scan(tcp.py_strings(os.path.join(ROOT, f)))
        hits += tcp.scan(io.open(os.path.join(ROOT, 'app/static/js/founder_workspace.js'), encoding='utf-8').read())
        for path in (base, base + '/team', base + '/plan'):
            hits += tcp.scan(tcp.visible(client.get(path).get_data(as_text=True)))
        sysprompts = ' '.join(c['system'] for c in fake.calls)
        hits += tcp.scan(sysprompts)
        check('templates, JS, Python strings, rendered pages and system prompts are clean', not hits, hits)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
