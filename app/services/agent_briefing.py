"""
Agent briefing and planning — the context every agent run is grounded in.

Before this module, all 47 single-pass agents received the same generic prompt:
their label and description, the form inputs, and a resume excerpt. Upstream
artifacts declared in agents.json (`prerequisites[].data_flow`) never reached
the model, so the rate card fed nobody and the IPS never saw the fund picks.

Three things live here:

  build_briefing()  — gathers what the agent should work from: upstream
                      artifacts, the user's profile, existing CRM contacts for
                      update-only agents, projected income for Layer 5, and
                      deterministic figures where the maths shouldn't be left to
                      the model.
  plan_run()        — the planning pass that runs before every agent's
                      generation call. A cheap model reads the briefing and
                      returns a structured plan (objective, assumptions, gaps,
                      outline, quality checks) that the generation call then
                      follows. Planning failure never blocks a run.
  system_prompt() / grounding_rules() — the shared rules every agent follows.

All DB reads happen in build_briefing(), before any model call, because
claude._log_interaction() disposes the engine after each call.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_UPSTREAM_CHARS = 3000      # per prerequisite artifact
_HUB_CHARS = 1500           # hub-node artifact (rate card) given to every agent
_UPSTREAM_TOTAL_CHARS = 9000
_EXISTING_CONTACTS_LIMIT = 25

_BLOCK_RE = re.compile(r'<!--(CONTACTS|METRICS)\b.*?\1-->', re.S)


@dataclass
class Briefing:
    profile: str = ''
    upstream: str = ''
    upstream_used: list[str] = field(default_factory=list)
    upstream_missing: list[str] = field(default_factory=list)
    existing_contacts: str = ''
    existing_contact_count: int = 0
    income: str = ''
    computed: str = ''

    def sections(self) -> str:
        """The briefing as prompt text, in the order the agent should read it."""
        parts = []
        if self.profile:
            parts.append('PROFESSIONAL PROFILE:\n' + self.profile)
        if self.upstream:
            parts.append('UPSTREAM ARTIFACTS (outputs of earlier agents — build on these, '
                         'stay consistent with their numbers and positioning):\n' + self.upstream)
        if self.upstream_missing:
            parts.append('UPSTREAM NOT YET AVAILABLE: ' + ', '.join(self.upstream_missing)
                         + ' — state any assumption you make in their place.')
        if self.existing_contacts:
            parts.append(self.existing_contacts)
        if self.income:
            parts.append(self.income)
        if self.computed:
            parts.append(self.computed)
        return '\n\n'.join(parts)


# ---------------------------------------------------------------------------
# Briefing
# ---------------------------------------------------------------------------

def _clean_artifact(text: str, limit: int) -> str:
    """Strip machine blocks and compress an artifact into a prompt excerpt."""
    if not text:
        return ''
    text = _BLOCK_RE.sub('', text).strip()
    # Structured outreach artifacts are prospect lists — summarise, don't paste.
    if text.startswith('{'):
        try:
            data = json.loads(text)
            prospects = data.get('prospects') if isinstance(data, dict) else None
            if isinstance(prospects, list):
                companies = sorted({p.get('company_name', '') for p in prospects if p.get('company_name')})
                return '%d prospects drafted (companies: %s)' % (
                    len(prospects), ', '.join(companies[:12]) or 'n/a')
        except Exception:
            pass
    text = re.sub(r'\n{3,}', '\n\n', text)
    if len(text) > limit:
        text = text[:limit].rsplit('\n', 1)[0] + '\n[…truncated]'
    return text


def _latest_artifact(simulation_id: str, action_type: str):
    from app.models.agent_action import AgentAction
    from app.services.agent_registry import names_for
    return (
        AgentAction.query
        .filter(AgentAction.simulation_id == simulation_id,
                AgentAction.action_type.in_(names_for(action_type)),
                AgentAction.status == AgentAction.STATUS_COMPLETE,
                AgentAction.artifact.isnot(None))
        .order_by(AgentAction.completed_at.desc())
        .first()
    )


def _upstream_section(simulation_id: str, action_type: str, briefing: Briefing) -> None:
    from app.services.agent_registry import get_prerequisites, get_hub_agents, get_agent_meta

    wanted = [(p['action_type'], p.get('data_flow', ''), _UPSTREAM_CHARS)
              for p in get_prerequisites(action_type)]
    prereq_names = {w[0] for w in wanted}
    for hub in get_hub_agents():
        if hub != action_type and hub not in prereq_names:
            wanted.append((hub, 'Root positioning and pricing for every layer', _HUB_CHARS))

    blocks, total = [], 0
    for upstream_type, data_flow, limit in wanted:
        meta = get_agent_meta(upstream_type) or {}
        label = meta.get('label', upstream_type)
        row = _latest_artifact(simulation_id, upstream_type) if simulation_id else None
        if not row:
            if upstream_type in prereq_names:
                briefing.upstream_missing.append(label)
            continue
        excerpt = _clean_artifact(row.artifact, min(limit, _UPSTREAM_TOTAL_CHARS - total))
        if not excerpt:
            continue
        total += len(excerpt)
        briefing.upstream_used.append(label)
        blocks.append('### %s\nHow to use it: %s\n%s' % (label, data_flow or 'context', excerpt))
        if total >= _UPSTREAM_TOTAL_CHARS:
            break
    briefing.upstream = '\n\n'.join(blocks)


def _json_list(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except Exception:
        return []


def _profile_section(user_id: str, simulation, briefing: Briefing) -> None:
    from app.models.user import User
    from app.models.profile import UserProfile

    lines = []
    user = User.query.get(user_id) if user_id else None
    profile = UserProfile.query.filter_by(user_id=user_id).first() if user_id else None
    name = (profile.display_name if profile and profile.display_name else None) or (
        user.full_name if user else None)
    if name:
        lines.append('- Name: %s' % name)
    if profile is not None:
        if profile.tagline:
            lines.append('- Positioning / tagline: %s' % profile.tagline)
        if profile.location:
            lines.append('- Location: %s' % profile.location)
        if profile.booking_url:
            lines.append('- Booking URL: %s' % profile.booking_url)
        if profile.linkedin_url:
            lines.append('- LinkedIn: %s' % profile.linkedin_url)
        roles = list(profile.career_history or [])[:5]
        role_bits = []
        for r in roles:
            if isinstance(r, dict):
                bit = ' — '.join(str(x).strip() for x in (r.get('role'), r.get('company_name')) if x)
                if bit:
                    role_bits.append(bit)
        if role_bits:
            lines.append('- Recent roles: %s' % '; '.join(role_bits))
        zones = _json_list(getattr(profile, '_canonical_zones', None))
        cats = [z.get('category') for z in zones if isinstance(z, dict) and z.get('category')]
        if cats:
            lines.append('- Expertise categories: %s' % ', '.join(cats[:6]))
    if simulation is not None and getattr(simulation, 'expertise_zone', None):
        lines.append('- Expertise Zone: %s' % simulation.expertise_zone)
    briefing.profile = '\n'.join(lines)


def _existing_contacts_section(user_id: str, briefing: Briefing, limit: int,
                               simulation_id: str = None, action_type: str = None) -> None:
    """Update-only agents (alumni, win-back, lapsed buyers) write to people the
    user already knows. Without this list the model invents names."""
    from app.models.contact import Contact
    rows = (
        Contact.query
        .filter(Contact.user_id == user_id,
                Contact.is_archived.is_(False),
                Contact.do_not_contact.is_(False),
                Contact.pipeline_stage.in_(('active', 'client')))
        .order_by(Contact.last_contacted_at.desc())
        .all()
    )
    # Each person once per agent across cycles — skip those an earlier run of
    # this agent already wrote to.
    from app.services import recipient_ledger as _ledger
    used = _ledger.used_keys(simulation_id, action_type) if simulation_id and action_type else set()
    rows = [c for c in rows if _ledger.recipient_key(
        c.email, c.first_name, c.last_name, c.company_name) not in used][:limit]
    briefing.existing_contact_count = len(rows)
    if not rows:
        briefing.existing_contacts = (
            'EXISTING CONTACTS: none on file. Write the sequence as a reusable template '
            'with {{first_name}} placeholders and do not name any individual.')
        return
    lines = ['EXISTING CONTACTS (past clients / buyers — the only people you may address):']
    for c in rows:
        bits = [('%s %s' % (c.first_name or '', c.last_name or '')).strip()]
        for v in (c.job_title, c.company_name, c.pipeline_stage):
            if v:
                bits.append(v)
        lines.append('  - ' + ' | '.join(b for b in bits if b))
    briefing.existing_contacts = '\n'.join(lines)


def _income_section(simulation, briefing: Briefing) -> None:
    """Layer 5 deploys income from Layers 1-4 — give it the projected numbers."""
    rows, low_total, high_total = [], 0, 0
    for layer in sorted(getattr(simulation, 'layers', None) or [],
                        key=lambda l: getattr(l, 'layer_number', 0) or 0):
        n = getattr(layer, 'layer_number', None)
        if not n or n >= 5:
            continue
        for s in getattr(layer, 'income_streams', None) or []:
            lo, hi = s.est_monthly_low or 0, s.est_monthly_high or 0
            low_total += lo
            high_total += hi
            rows.append('  - L%d %s: $%s–$%s / month' % (n, s.name, format(lo, ','), format(hi, ',')))
    if rows:
        briefing.income = ('PROJECTED INCOME FROM LAYERS 1-4 (simulation estimates):\n'
                           + '\n'.join(rows)
                           + '\n  Total: $%s–$%s / month' % (format(low_total, ','), format(high_total, ',')))


_NUM_RE = re.compile(r'-?\d[\d,]*\.?\d*')


def _parse_number(value) -> float | None:
    if value is None:
        return None
    m = _NUM_RE.search(str(value))
    if not m:
        return None
    try:
        return float(m.group(0).replace(',', ''))
    except ValueError:
        return None


def compound_projection_table(monthly: float, annual_return_pct: float,
                              years=(1, 3, 5, 10, 15, 20)) -> str:
    """Future value of a monthly contribution under three return scenarios.
    Deterministic so the projections agent narrates numbers instead of doing
    compound-interest arithmetic in the model."""
    scenarios = [('Conservative', max(annual_return_pct - 2, 0.0)),
                 ('Base', annual_return_pct),
                 ('Optimistic', annual_return_pct + 2)]
    header = '| Year | Contributed | ' + ' | '.join(
        '%s (%.1f%%)' % (name, r) for name, r in scenarios) + ' |'
    sep = '|' + '---|' * (2 + len(scenarios))
    out = [header, sep]
    for y in years:
        months = 12 * y
        cells = []
        for _, r in scenarios:
            i = r / 100 / 12
            fv = monthly * months if i == 0 else monthly * (((1 + i) ** months - 1) / i)
            cells.append('$%s' % format(round(fv), ','))
        out.append('| %d | $%s | %s |' % (y, format(round(monthly * months), ','), ' | '.join(cells)))
    return '\n'.join(out)


def _computed_section(action_type: str, user_inputs: dict, briefing: Briefing) -> None:
    if action_type != 'projections':
        return
    monthly = _parse_number(user_inputs.get('monthly_deploy'))
    rate = _parse_number(user_inputs.get('return_assumption'))
    if not monthly or monthly <= 0:
        return
    if rate is None or not (0 <= rate <= 30):
        rate = 7.0
    briefing.computed = (
        'COMPUTED FIGURES (deterministic — reproduce these exactly, do not recalculate):\n'
        'Monthly contribution $%s, monthly compounding, return scenarios base ±2 points.\n%s'
        % (format(round(monthly), ','), compound_projection_table(monthly, rate)))


def build_briefing(action_type: str, layer_number: int, simulation_id: str,
                   user_id: str, user_inputs: dict, prospect_count: int | None = None) -> Briefing:
    """Gather everything the agent should ground its artifact in. Each source is
    independent and non-fatal: a failure leaves that section empty."""
    from app.services.agent_registry import UPDATE_ONLY_CONTACT_AGENTS

    briefing = Briefing()
    simulation = None
    try:
        if simulation_id:
            from app.models.simulation import Simulation
            simulation = Simulation.query.get(simulation_id)
            if not user_id and simulation is not None:
                user_id = simulation.user_id
    except Exception as exc:
        logger.warning('briefing: simulation load failed for %s: %s', simulation_id, exc)

    steps = [
        ('upstream', lambda: _upstream_section(simulation_id, action_type, briefing)),
        ('profile', lambda: _profile_section(user_id, simulation, briefing)),
        ('computed', lambda: _computed_section(action_type, user_inputs or {}, briefing)),
    ]
    if action_type in UPDATE_ONLY_CONTACT_AGENTS and user_id:
        steps.append(('existing_contacts', lambda: _existing_contacts_section(
            user_id, briefing, max(prospect_count or 0, _EXISTING_CONTACTS_LIMIT),
            simulation_id=simulation_id, action_type=action_type)))
    if layer_number == 5 and simulation is not None:
        steps.append(('income', lambda: _income_section(simulation, briefing)))

    for name, fn in steps:
        try:
            fn()
        except Exception as exc:
            logger.warning('briefing: %s section failed for %s: %s', name, action_type, exc)
    return briefing


# ---------------------------------------------------------------------------
# Shared rules
# ---------------------------------------------------------------------------

def system_prompt(meta: dict) -> str:
    rules = [
        'You are a specialist SimiSI career-wealth agent producing one finished, '
        'immediately usable artifact for a professional.',
        'Ground every claim in the supplied profile, resume, upstream artifacts, inputs '
        'and prospect lists. Keep numbers and positioning consistent with upstream artifacts.',
        'Never invent people, email addresses, phone numbers, URLs, testimonials, client '
        'names or statistics and present them as real. Where the user must fill something '
        'in, use a clearly marked [placeholder].',
        'When a required input is missing, make a reasonable assumption and state it in a '
        'short "Assumptions" note at the end of the artifact (at most 3 bullets).',
        'Be concise and skimmable: cover every required section, but do not pad — prefer '
        'tables and bullets over long prose, and do not restate the inputs or the plan.',
        'No meta-commentary, no preamble about what you are going to do — only the artifact.',
    ]
    if meta.get('disclaimer'):
        rules.append(
            'This is educational financial, tax or legal information, not individualised '
            'advice. End the artifact with a one-paragraph disclaimer recommending review by a '
            'licensed professional (CFP, CPA or attorney as relevant). Do not promise returns.')
    return '\n'.join('- ' + r for r in rules)


def grounding_rules(meta: dict) -> str:
    facts = meta.get('external_facts') or []
    if not facts:
        return ''
    return (
        'DATA LIMITS: you have no live data source for: ' + '; '.join(facts) + '. '
        'Where the artifact needs these, give representative examples, label them '
        '"(illustrative — verify)", and add a short "How to verify" note. Do not state them '
        'as current facts.'
    )


def output_spec_block(meta: dict) -> str:
    spec = meta.get('output_spec') or []
    if not spec:
        return ''
    return 'REQUIRED SECTIONS (all must appear):\n' + '\n'.join('- ' + s for s in spec)


# ---------------------------------------------------------------------------
# Planning pass
# ---------------------------------------------------------------------------

# A plain line format, not JSON: the plan is only rendered back into the
# generation prompt, and JSON's quotes, keys and indentation roughly doubled the
# tokens (and so the seconds) every agent spent planning.
PLAN_MAX_TOKENS = 700

_PLAN_FORMAT = """OBJECTIVE: <one sentence>
USE: <specific fact from the briefing>; <another>; <another>
ASSUME: <assumption for a missing input>; <another>
GAPS: <missing or unverifiable item and how the artifact handles it>; <another>
OUTLINE:
1. <section name> — <what goes in it, concrete, under 20 words>
2. <section name> — <...>"""

_PLAN_KEYS = {'OBJECTIVE': 'objective', 'USE': 'key_inputs',
              'ASSUME': 'assumptions', 'GAPS': 'gaps'}


def _plan_prompt(meta: dict, layer_name: str, expertise_zone: str, inputs_formatted: str,
                 briefing: Briefing, prospect_note: str, parsed_text: str) -> str:
    return f"""You are planning the work of a specialist agent before it writes its artifact.

AGENT: {meta.get('label')}
LAYER: {layer_name}
WHAT IT PRODUCES: {meta.get('description')}
EXPERTISE ZONE: {expertise_zone}

{output_spec_block(meta)}

{grounding_rules(meta)}

USER-PROVIDED INPUTS:
{inputs_formatted}

{briefing.sections()}

PROSPECTS: {prospect_note}

RESUME EXCERPT:
{(parsed_text or '')[:1500]}

Write a short, concrete execution plan. Name the specific details from the briefing the
artifact will use (rates, tiers, roles, outcomes, upstream figures). Map every required
section to an outline entry. List assumptions for missing inputs and how unverifiable data
is handled.

Keep it terse — this plan is read by another model, not a person:
- at most 6 outline entries; at most 4 items per line, separated by ";"
- the whole plan under 200 words
- never invent client results, percentages or statistics; use only figures in the briefing
- write "none" for a line with nothing to say

Reply with exactly this format and nothing else:
{_PLAN_FORMAT}"""


def format_plan(plan: dict) -> str:
    """Render a plan dict as prompt text for the generation call."""
    if not plan:
        return ''
    lines = ['EXECUTION PLAN (produced in the planning step — follow it):']
    if plan.get('objective'):
        lines.append('Objective: %s' % plan['objective'])
    if plan.get('audience'):
        lines.append('Audience: %s' % plan['audience'])
    for key, title in (('key_inputs', 'Use these specifics'), ('assumptions', 'Assumptions'),
                       ('gaps', 'Gaps and handling'), ('quality_checks', 'Quality checks')):
        items = [str(i) for i in (plan.get(key) or []) if i]
        if items:
            lines.append('%s:\n%s' % (title, '\n'.join('  - ' + i for i in items)))
    outline = [o for o in (plan.get('outline') or []) if isinstance(o, dict) and o.get('section')]
    if outline:
        lines.append('Outline:\n' + '\n'.join(
            '  %d. %s — %s' % (n, o['section'], o.get('content', '')) for n, o in enumerate(outline, 1)))
    return '\n'.join(lines)


def _parse_plan(raw: str) -> dict | None:
    """Parse the line-format plan into {objective, key_inputs, assumptions, gaps, outline}."""
    plan: dict = {'outline': []}
    in_outline = False
    for line in (raw or '').splitlines():
        line = line.strip().lstrip('*#').strip()
        if not line:
            continue
        head, sep, rest = line.partition(':')
        key = _PLAN_KEYS.get(head.strip().upper()) if sep else None
        if key:
            in_outline = False
            rest = rest.strip().strip('*').strip()   # tolerate **BOLD:** headings
            if key == 'objective':
                plan['objective'] = rest
            else:
                plan[key] = [i.strip() for i in rest.split(';')
                             if i.strip() and i.strip().lower() != 'none']
            continue
        if head.strip().upper() == 'OUTLINE' and sep:
            in_outline = True
            continue
        m = re.match(r'^\d+[.)]\s*(.+)$', line)
        if in_outline and m:
            parts = re.split(r'\s+[—–-]\s+', m.group(1), maxsplit=1)
            plan['outline'].append({'section': parts[0].strip(),
                                    'content': parts[1].strip() if len(parts) > 1 else ''})
    return plan if plan['outline'] else None


def plan_run(*, client, meta: dict, layer_name: str, expertise_zone: str,
             inputs_formatted: str, briefing: Briefing, prospect_note: str,
             parsed_text: str, user_id: str, simulation_id: str) -> dict | None:
    """The planning step that precedes every agent's generation call.

    Returns the plan dict, or None if planning failed — the caller then runs
    without a plan rather than failing the user's artifact.
    """
    from utils.model_router import get_model
    from app.models.ai_interaction import AIInteraction
    from app.services.claude import _log_interaction

    model = get_model('agent_planning')
    prompt = _plan_prompt(meta, layer_name, expertise_zone, inputs_formatted,
                          briefing, prospect_note, parsed_text)
    try:
        # Bounded so planning stays a few seconds. An unbounded plan ran to the
        # old 1,500-token cap on every run (~17s), came back truncated and
        # unparseable, and delayed every agent queued behind it.
        response = client.messages.create(
            model=model, max_tokens=PLAN_MAX_TOKENS,
            messages=[{'role': 'user', 'content': prompt}],
        )
    except Exception as exc:
        logger.warning('Planning call failed for %s: %s', meta.get('action_type'), exc)
        return None
    _log_interaction(AIInteraction.TYPE_AGENT_PLAN, user_id, simulation_id,
                     response.usage, model=model)
    plan = _parse_plan(response.content[0].text if response.content else '')
    if plan is None:
        logger.warning('Planning output unparseable for %s (stop_reason=%s, out_tokens=%s)',
                       meta.get('action_type'), response.stop_reason,
                       getattr(response.usage, 'output_tokens', '?'))
    return plan
