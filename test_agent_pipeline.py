"""Agent pipeline review fixes — naming, autonomy gates, briefing, planning, triggers.

Runs against an in-memory SQLite database with a fake Anthropic client, so it
touches no real data and makes no network calls. Covers:

  1. Name hygiene — every hardcoded agent-name table uses canonical names, and
     agents.json's `model` matches what utils/model_router actually routes.
  2. Autonomy — channel gating at dispatch (agents.json autonomy_channel), at
     step execution (held, not sent) and at wire deploy (outbound providers).
  3. Briefing — upstream artifacts, profile, existing contacts, L5 income and
     deterministic projections reach the prompt.
  4. Planning — every agent run makes a planning call before generation, and
     the plan, spec and grounding rules are in the generation prompt.
  5. Triggers and per-agent scoring.

    python test_agent_pipeline.py
"""
import json
import os
import sys
from types import SimpleNamespace

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


class FakeClient:
    """Stands in for anthropic.Anthropic: records calls, answers plan then artifact."""

    def __init__(self, plan_text, artifact_text):
        self.calls = []
        self._plan, self._artifact = plan_text, artifact_text
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        prompt = kw['messages'][0]['content']
        text = self._plan if prompt.startswith('You are planning') else self._artifact
        return SimpleNamespace(content=[SimpleNamespace(text=text)],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=10),
                               stop_reason='end_turn')


PLAN = """OBJECTIVE: Rewrite the LinkedIn profile around the Tier 2 retainer
USE: Tier 2 retainer $9,000/month; COO at Acme
ASSUME: Open to engagements selectively
GAPS: none
OUTLINE:
1. Headline options — three options under 220 characters
2. About section — fold-optimised first three lines
"""


def name_hygiene():
    print('\n1. Name hygiene')
    from app.services.agent_registry import CANONICAL_ACTION_TYPES, get_alias_map, get_all_agents
    from app.services import claude, layer6, wire_service, action_step_service
    from app.services.agent_selector import TRIGGERED_AGENT_IDS
    from app.services.prospect_research_engine import RESEARCH_ENABLED_AGENTS
    from utils.model_router import _ROUTING

    tables = {
        'claude.ACTION_MAX_TOKENS': set(claude.ACTION_MAX_TOKENS),
        'wire_service.AGENT_INTEGRATION_MAP': set(wire_service.AGENT_INTEGRATION_MAP),
        'action_step_service.AGENT_STEP_CONFIG': set(action_step_service.AGENT_STEP_CONFIG),
        'layer6.INTEGRATION_BOOST': {a for v in layer6.INTEGRATION_BOOST.values() for a in v},
        'layer6.SIGNAL_PREFIX_AGENTS': {a for v in layer6.SIGNAL_PREFIX_AGENTS.values() for a in v},
        'layer6._NO_DEDUP_ACTIONS': set(layer6._NO_DEDUP_ACTIONS),
        'RESEARCH_ENABLED_AGENTS': set(RESEARCH_ENABLED_AGENTS),
        'TRIGGERED_AGENT_IDS': set(TRIGGERED_AGENT_IDS),
    }
    for name, keys in tables.items():
        stray = sorted(keys - CANONICAL_ACTION_TYPES)
        check('%s uses canonical names only' % name, not stray, stray)

    aliases = set(get_alias_map())
    stale_routes = sorted(k for k in _ROUTING if k in aliases)
    check('model router has no alias keys (they can never match)', not stale_routes, stale_routes)

    mismatched = [(a['action_type'], a['model'], getattr(_ROUTING.get(a['action_type']), 'value', 'sonnet'))
                  for a in get_all_agents()
                  if getattr(_ROUTING.get(a['action_type']), 'value', 'sonnet') != a['model']]
    check('agents.json model matches the routed tier for all 49', not mismatched, mismatched)

    from utils.model_router import get_model
    check('legal templates route to Sonnet',
          'sonnet' in get_model('agreement_template') and 'sonnet' in get_model('sow_template'))
    check('planning step routes to Haiku', 'haiku' in get_model('agent_planning'))

    from app.services.prefill_engine import dependency_edges
    edges = dependency_edges()
    stray = sorted(({e['upstream_action_type'] for e in edges} | {e['downstream_action_type'] for e in edges})
                   - CANONICAL_ACTION_TYPES)
    check('dependency edges come from the prerequisite DAG', len(edges) >= 40 and not stray,
          '%d edges, stray=%s' % (len(edges), stray))

    for a in get_all_agents():
        if not a.get('output_spec'):
            check('%s has an output_spec' % a['action_type'], False)
    check('every agent has an output_spec', all(a.get('output_spec') for a in get_all_agents()))


def main():
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    name_hygiene()

    with app.app_context():
        db.create_all()

        from datetime import datetime, timedelta
        from app.models.user import User
        from app.models.profile import UserProfile
        from app.models.simulation import Simulation, SimulationLayer, IncomeStream
        from app.models.agent_action import AgentAction
        from app.models.layer6 import Layer6Config
        from app.models.contact import Contact
        from app.models.outreach_email import EmailLog
        from app.models.action_step import ActionStep
        from app.services import claude, layer6, wire_service, action_step_service
        from app.services import agent_briefing as brief

        # The real _log_interaction disposes the engine, which would drop the
        # in-memory database; the token log is not under test here.
        claude._log_interaction = lambda *a, **k: None

        user = User(id=generate_id(), email='owner@example.com', full_name='Dana Owner',
                    password_hash='x', is_admin=False)
        db.session.add(user)
        db.session.flush()
        profile = UserProfile(id=generate_id(), user_id=user.id, username='dana',
                              display_name='Dana Owner', tagline='Ops turnarounds for mid-market',
                              location='Chicago, IL')
        profile.career_history = [{'role': 'COO', 'company_name': 'Acme Manufacturing'}]
        db.session.add(profile)
        sim = Simulation(id=generate_id(), user_id=user.id, name='Engine',
                         expertise_zone='Operations Consulting', status='complete')
        db.session.add(sim)
        db.session.flush()
        config = Layer6Config(id=generate_id(), simulation_id=sim.id)
        db.session.add(config)
        rate_card = AgentAction(
            id=generate_id(), simulation_id=sim.id, layer_number=1, action_type='rate_card',
            status='complete', completed_at=datetime.utcnow(),
            artifact=('# Rate Card\nTier 2 retainer $9,000/month\n'
                      '<!--CONTACTS\n[{"first_name":"X"}]\nCONTACTS-->\n'
                      '<!--METRICS\n{"day_rate": 1800}\nMETRICS-->'))
        db.session.add(rate_card)
        db.session.commit()

        # ── 2. Autonomy ─────────────────────────────────────────────────────
        print('\n2. Autonomy gates')
        config.channel_approvals = {'email': True, 'email_funnels': False, 'linkedin': False,
                                    'calendar': False, 'content_publishing': False}
        db.session.commit()
        ok, why = layer6._check_autonomy_bounds('launch_sequence', config)
        check('launch_sequence escalates without email_funnels approval', not ok, why)
        ok, _ = layer6._check_autonomy_bounds('launch_email_sequence', config)
        check('legacy alias launch_email_sequence is gated the same way', not ok)
        ok, _ = layer6._check_autonomy_bounds('video_podcast', config)
        check('video_podcast escalates without content_publishing', not ok)
        ok, _ = layer6._check_autonomy_bounds('cold_email_campaign', config)
        check('cold_email_campaign drafts are not gated at dispatch', ok)

        sends = []
        action_step_service.execute_step = lambda step, *a, **k: sends.append(step.id) or True
        config.channel_approvals = dict(config.channel_approvals, email=False)
        db.session.commit()
        step = ActionStep(id=generate_id(), agent_action_id=rate_card.id, simulation_id=sim.id,
                          step_number=2, total_steps=3, action_type='cold_email_campaign',
                          step_type='email_followup', scheduled_for=datetime.utcnow() - timedelta(hours=1))
        step.payload = {'contact_id': 'c1'}
        db.session.add(step)
        db.session.commit()
        res = action_step_service.process_due_steps(sim.id, user.id)
        db.session.refresh(step)
        check('email step is held while email is not approved',
              res['held'] == 1 and not sends and step.status == 'scheduled', res)
        config.channel_approvals = dict(config.channel_approvals, email=True)
        db.session.commit()
        res = action_step_service.process_due_steps(sim.id, user.id)
        db.session.refresh(step)
        check('held step sends once email is approved',
              res['executed'] == 1 and sends and step.status == 'executed', res)

        deployed, escalations = [], []
        wire_service._load_integrations = lambda uid: {'pandadoc': object(), 'stripe': object()}
        wire_service._DEPLOYERS = {p: (lambda p: (lambda **kw: deployed.append(p)))(p)
                                   for p in wire_service._DEPLOYERS}
        wire_service._escalate = lambda *a, **k: escalations.append(a)
        config.channel_approvals = dict(config.channel_approvals, email=False)
        db.session.commit()
        res = wire_service.deploy_to_integration(user.id, sim.id, rate_card.id,
                                                 'consulting_agreement', 'x', 1)
        check('legacy alias consulting_agreement deploys as agreement_template',
              not res.get('no_chain'), res)
        check('PandaDoc send held without email approval; Stripe still deploys',
              deployed == ['stripe'] and 'pandadoc' in res['skipped'], res)
        res = wire_service.deploy_to_integration(user.id, sim.id, rate_card.id,
                                                 'group_coaching', 'x', 2)
        check('unconnected providers skip quietly (no notification spam)',
              not escalations and 'cal' in res['skipped'], res)

        # ── 3. Briefing ─────────────────────────────────────────────────────
        print('\n3. Briefing')
        b = brief.build_briefing('linkedin_optimization', 1, sim.id, user.id, {})
        check('upstream rate card reaches linkedin_optimization',
              '$9,000/month' in b.upstream and b.upstream_used, b.upstream_used)
        check('CONTACTS/METRICS blocks are stripped from upstream excerpts',
              'CONTACTS' not in b.upstream and 'METRICS' not in b.upstream)
        check('profile carries tagline, location and recent roles',
              'Ops turnarounds' in b.profile and 'Chicago' in b.profile and 'COO' in b.profile, b.profile)
        b = brief.build_briefing('dca_schedule', 5, sim.id, user.id, {})
        check('missing prerequisites are named for the agent',
              any('Fund' in m for m in b.upstream_missing), b.upstream_missing)

        db.session.add(Contact(id=generate_id(), user_id=user.id, first_name='Pat',
                               last_name='Client', email='pat@client.com', pipeline_stage='client',
                               company_name='Beta Corp', source='manual'))
        layer2 = SimulationLayer(id=generate_id(), simulation_id=sim.id, layer_number=2,
                                 layer_name='Leveraged Income')
        db.session.add(layer2)
        db.session.flush()
        db.session.add(IncomeStream(id=generate_id(), layer_id=layer2.id, name='Workshops',
                                    est_monthly_low=2000, est_monthly_high=4000, ai_reasoning='x'))
        db.session.commit()
        b = brief.build_briefing('client_winback', 4, sim.id, user.id, {})
        check('update-only agents get the existing client list',
              'Pat Client' in b.existing_contacts and b.existing_contact_count == 1, b.existing_contacts)
        db.session.expire_all()
        b = brief.build_briefing('income_allocation', 5, sim.id, user.id, {})
        check('Layer 5 sees projected Layer 1-4 income', 'Workshops' in b.income, b.income)
        b = brief.build_briefing('projections', 5, sim.id, user.id,
                                 {'monthly_deploy': '$1,000', 'return_assumption': '0%'})
        check('projections get a deterministic table (0%: 20y = $240,000)',
              '$240,000' in b.computed, b.computed[:200])
        table = brief.compound_projection_table(500, 6)
        check('compound table base case 10y ≈ $81,940', '$81,940' in table, table)

        # ── 4. Planning step precedes generation ───────────────────────────
        print('\n4. Planning step')
        fake = FakeClient(PLAN, '## Headline\nOps leader')
        claude._client = lambda: fake
        out = claude._execute_agent_action_body(
            action_type='linkedin_optimization', layer_number=1,
            expertise_zone='Operations Consulting', parsed_text='COO at Acme',
            user_inputs={'open_to_engagements': 'selectively'}, user_id=user.id,
            simulation_id=sim.id, action_id=None, prospect_count=5, model='m')
        check('two model calls: plan, then generate', len(fake.calls) == 2, len(fake.calls))
        plan_prompt = fake.calls[0]['messages'][0]['content']
        gen = fake.calls[1]
        gen_prompt = gen['messages'][0]['content']
        check('planner sees the upstream rate card', '$9,000/month' in plan_prompt)
        check('generation prompt carries the plan', 'EXECUTION PLAN' in gen_prompt
              and 'Tier 2 retainer' in gen_prompt)
        check('generation prompt carries REQUIRED SECTIONS', 'REQUIRED SECTIONS' in gen_prompt)
        check('generation has a system prompt with no-fabrication rule',
              'Never invent' in gen.get('system', ''))
        check('artifact returned', out.startswith('## Headline'))
        plan = brief._parse_plan('**OBJECTIVE:** Price it\nUSE: a; b\nGAPS: none\n'
                                 'OUTLINE:\n1. Tiers — three tiers\n2) Terms - net 30')
        check('line-format plan parses (bold headings, "none", both dash styles)',
              plan and plan['objective'] == 'Price it' and plan['key_inputs'] == ['a', 'b']
              and plan['gaps'] == [] and [o['section'] for o in plan['outline']] == ['Tiers', 'Terms'], plan)

        fake = FakeClient('not json', 'Allocation table')
        claude._client = lambda: fake
        claude._execute_agent_action_body(
            action_type='fund_recommendations', layer_number=5, expertise_zone='Ops',
            parsed_text='', user_inputs={}, user_id=user.id, simulation_id=sim.id, model='m')
        gen = fake.calls[-1]
        check('unparseable plan does not block the run', len(fake.calls) == 2)
        check('L5 system prompt requires a disclaimer', 'disclaimer' in gen['system'].lower())
        check('unverifiable facts flagged illustrative',
              'illustrative' in gen['messages'][0]['content'])

        fake = FakeClient(PLAN, 'Affiliate plan\n<!--CONTACTS [] CONTACTS-->')
        claude._client = lambda: fake
        claude._execute_agent_action_body(
            action_type='affiliate_program', layer_number=3, expertise_zone='Ops',
            parsed_text='', user_inputs={}, user_id=user.id, simulation_id=sim.id,
            prospect_count=5, model='m')
        gp = fake.calls[-1]['messages'][0]['content']
        check('contact agents may only list supplied people',
              'Include only people who appear in the supplied' in gp)
        check('count constraint forbids padding with invented people',
              'Never invent people' in gp and 'at most 5' in gp)

        # ── 5. Triggers and scoring ────────────────────────────────────────
        print('\n5. Triggers and per-agent scoring')
        from app.services.agent_triggers import check_trigger
        # The agent body drops the scoped session (as it must in production), so
        # objects loaded earlier are detached — reload them.
        config = Layer6Config.query.filter_by(simulation_id=sim.id).first()
        sim = Simulation.query.get(sim.id)
        config.channel_approvals = dict(config.channel_approvals, email=True)
        db.session.commit()
        fired, _, _ = check_trigger(sim.id, 'consulting_proposal')
        eligible = {e['action_type'] for e in layer6.build_eligible_actions(
            sim.id, config, {'rate_card'}, phase='explore')}
        check('consulting_proposal waits for a reply or booking',
              not fired and 'consulting_proposal' not in eligible)
        check('non-triggered agents unaffected', 'linkedin_optimization' in eligible)
        contact = Contact.query.filter_by(email='pat@client.com').first()
        db.session.add(EmailLog(id=generate_id(), simulation_id=sim.id, contact_id=contact.id,
                                subject='s', from_email='me@x.com', from_name='Me',
                                to_email='pat@client.com', status='sent',
                                replied_at=datetime.utcnow()))
        db.session.commit()
        fired, event, ctx = check_trigger(sim.id, 'consulting_proposal')
        eligible = {e['action_type'] for e in layer6.build_eligible_actions(
            sim.id, config, {'rate_card'}, phase='explore')}
        check('a reply fires the proposal trigger with client context',
              fired and event == 'email_replied_with_interest' and ctx.get('client_name') == 'Beta Corp', ctx)
        check('consulting_proposal becomes eligible after the reply', 'consulting_proposal' in eligible)
        inputs = layer6.build_dispatch_user_inputs('consulting_proposal', 1, sim, None, {})
        check('trigger context fills the proposal inputs', inputs.get('client_name') == 'Beta Corp', inputs)
        fired, _, _ = check_trigger(sim.id, 'negotiation_script')
        check('triggers with no detector stay manual-only', not fired)

        common = dict(source_layer=1, outcomes_for_layer=[], unblocks_count=0,
                      phase='exploit', layer_index=1, _noise_seed=0.5)
        hi = layer6.score_action('rate_card', **common)
        lo = layer6.score_action('negotiation_script', **common)
        check('agents in the same layer no longer share one yield estimate', hi > lo, (hi, lo))

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
