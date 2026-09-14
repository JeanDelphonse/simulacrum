"""
Agent Selector service — SIM-PRD-AGENTSEL-001.

Provides:
  calculate_agent_relevance()       — score all agents against user's expertise zones
  render_description()              — fill description_template with primary_expertise
  generate_personalized_descriptions() — Claude call to produce per-agent personalised blurbs
  get_persona_defaults()            — fallback agent sets per persona type
  get_selector_data()               — aggregate all data the selector UI needs
"""
from __future__ import annotations
import hashlib
import json
import logging

_log = logging.getLogger(__name__)

RELEVANCE_THRESHOLD = 0.40   # agents scoring >= this are pre-selected / "Recommended"

# Reserved key stored inside the cached descriptions dict holding a fingerprint
# of the inputs (expertise zones + primary expertise) used to generate them.
# It lets us regenerate only when those inputs change, instead of on every load.
# Safe to co-locate: descriptions are only ever read per-agent via .get(action_type).
_DESCRIPTIONS_SIG_KEY = '__input_sig__'


def _descriptions_input_sig(zone_names: list[str], primary_expertise: str) -> str:
    """Stable fingerprint of the inputs that drive personalized descriptions."""
    payload = json.dumps(
        {'zones': zone_names, 'primary': primary_expertise},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode('utf-8')).hexdigest()

TRIGGERED_AGENT_IDS = frozenset([
    'consulting_proposal',
    'sow_template',
    'agreement_template',
    'negotiation_script',
    'speaker_fee_rider',
])

# rate_card is the root node — always selected, never toggleable
ROOT_AGENT_ID = 'rate_card'

# Layer display names
LAYER_NAMES = {
    1: 'L1 · Active Income',
    2: 'L2 · Leveraged Delivery',
    3: 'L3 · Digital Products',
    4: 'L4 · Automation & IP',
    5: 'L5 · Wealth Deployment',
}

LAYER_DESCRIPTIONS = {
    1: 'Consulting, freelance and fractional engagements — directly validated by your resume.',
    2: 'Same expertise delivered to many people: workshops, corporate training, group coaching.',
    3: 'IP packaged into products sold without you present: courses, e-books, memberships.',
    4: 'Automated funnels, SEO content engines, SaaS tools and licensing.',
    5: 'Revenue from Layers 1–4 deployed into compounding wealth vehicles.',
}

LAYER_LOCK_CONDITIONS = {
    4: 'Unlocks after your first product sale',
    5: 'Unlocks after automated income is established',
}

# Persona template defaults (FR-SEL-11)
_PERSONA_DEFAULTS: dict[str, list[str]] = {
    'Consultant': [
        'rate_card', 'booking_page', 'consulting_outreach', 'consulting_proposal', 'referral_network',
        'workshop_curriculum', 'speaking_proposals', 'corporate_training',
        'course_curriculum', 'ebook_outline', 'testimonials',
        'seo_content_calendar', 'newsletter', 'lead_magnet_funnel',
        'income_allocation', 'entity_structure', 'tax_optimization',
        'projections', 'fund_recommendations', 'dca_schedule',
    ],
    'Engineer': [
        'rate_card', 'booking_page', 'cold_email_campaign', 'consulting_outreach', 'linkedin_optimization',
        'role_search', 'speaking_proposals', 'group_coaching', 'roi_calculator',
        'course_curriculum', 'saas_product_spec', 'membership',
        'seo_content_calendar', 'newsletter', 'community',
        'income_allocation', 'entity_structure', 'tax_optimization',
        'projections', 'fund_recommendations', 'dca_schedule',
    ],
    'Creative': [
        'rate_card', 'booking_page', 'linkedin_optimization', 'referral_network',
        'workshop_curriculum', 'speaking_proposals', 'waitlist_landing_page',
        'ebook_outline', 'course_curriculum', 'affiliate_program',
        'video_podcast', 'newsletter', 'community',
        'income_allocation', 'entity_structure', 'tax_optimization',
        'projections', 'fund_recommendations', 'dca_schedule',
    ],
    'Executive': [
        'rate_card', 'booking_page', 'consulting_outreach', 'referral_network', 'negotiation_script',
        'speaking_proposals', 'corporate_training', 'roi_calculator',
        'course_curriculum', 'ip_licensing', 'membership',
        'newsletter', 'community', 'client_winback',
        'income_allocation', 'entity_structure', 'tax_optimization',
        'projections', 'fund_recommendations', 'dca_schedule',
    ],
    'Educator': [
        'rate_card', 'booking_page', 'consulting_outreach', 'linkedin_optimization',
        'workshop_curriculum', 'group_coaching', 'corporate_training', 'alumni_reactivation',
        'course_curriculum', 'membership', 'ebook_outline', 'launch_sequence',
        'video_podcast', 'newsletter', 'lead_magnet_funnel',
        'income_allocation', 'entity_structure', 'tax_optimization',
        'projections', 'fund_recommendations', 'dca_schedule',
    ],
}


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------

def calculate_agent_relevance(
    expertise_zone_names: list[str],
    agents_flat: list[dict],
    threshold: float = RELEVANCE_THRESHOLD,
) -> dict[str, dict]:
    """
    Score every agent against the user's expertise zone names.

    Returns a dict keyed by action_type:
      {
        'score': float (0-1, normalised across zones),
        'matched_zones': int,
        'recommended': bool,
      }
    """
    zone_count = max(len(expertise_zone_names), 1)
    results: dict[str, dict] = {}

    for agent in agents_flat:
        at = agent['action_type']
        relevance_map: dict[str, float] = agent.get('expertise_relevance', {})

        total = 0.0
        matched = 0

        for zone in expertise_zone_names:
            zone_lower = zone.lower()
            zone_words = zone_lower.replace('-', ' ').split()
            for keyword, score in relevance_map.items():
                kw_lower = keyword.lower().replace('_', ' ')
                # Exact substring match (fast path)
                if kw_lower in zone_lower or zone_lower in kw_lower:
                    total += score
                    matched += 1
                    break
                # Word-stem match: first 5 chars of keyword vs each zone word
                kw_stem = kw_lower[:5]
                if any(w.startswith(kw_stem) or kw_stem.startswith(w[:5])
                       for w in zone_words if len(w) >= 4):
                    total += score
                    matched += 1
                    break

        normalised = round(total / zone_count, 3)
        results[at] = {
            'score': normalised,
            'matched_zones': matched,
            'recommended': normalised >= threshold,
        }

    return results


# ---------------------------------------------------------------------------
# Description helpers
# ---------------------------------------------------------------------------

def render_description(template: str, primary_expertise: str) -> str:
    """Fill {primary_expertise} placeholder in description_template."""
    return template.replace('{primary_expertise}', primary_expertise or 'your expertise')


def generate_personalized_descriptions(
    agents_flat: list[dict],
    expertise_zone_names: list[str],
    primary_expertise: str,
    user_id: str,
    simulation_id: str,
) -> dict[str, str]:
    """
    Use Claude to generate a one-line personalised description for each agent.
    Falls back to filling description_template if the Claude call fails.

    Returns dict keyed by action_type -> personalised description string.
    """
    try:
        from app.services.claude import _client, get_model, _log_interaction
        from app.models.ai_interaction import AIInteraction

        zone_text = ', '.join(expertise_zone_names[:5]) or primary_expertise

        agent_list = '\n'.join(
            f'- {a["action_type"]}: {a.get("description_template", a.get("description", "")[:80])}'
            for a in agents_flat
        )

        prompt = (
            f'You are personalising agent descriptions for a professional with expertise in: {zone_text}.\n\n'
            f'For each agent below, write a single concise phrase (max 8 words) that makes the generic '
            f'description specific to their background. Use concrete nouns from their expertise. '
            f'Do NOT start with a verb. Return ONLY valid JSON: {{"action_type": "description", ...}}\n\n'
            f'{agent_list}'
        )

        model = get_model('haiku')
        response = _client().messages.create(
            model=model,
            max_tokens=3000,
            messages=[{'role': 'user', 'content': prompt}],
        )

        from app.models.ai_interaction import AIInteraction
        from app.services.claude import _log_interaction
        _log_interaction(AIInteraction.TYPE_AGENT_SELECTOR, user_id, simulation_id,
                         response.usage, model=model)

        raw = response.content[0].text.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
        descriptions = json.loads(raw)

        # Validate and fill gaps with template fallback
        result: dict[str, str] = {}
        for agent in agents_flat:
            at = agent['action_type']
            desc = descriptions.get(at, '').strip()
            if not desc:
                desc = render_description(
                    agent.get('description_template', agent.get('description', '')),
                    primary_expertise,
                )
            result[at] = desc
        return result

    except Exception as exc:
        _log.warning('generate_personalized_descriptions failed (%s) — using templates', exc)
        return {
            a['action_type']: render_description(
                a.get('description_template', a.get('description', '')),
                primary_expertise,
            )
            for a in agents_flat
        }


# ---------------------------------------------------------------------------
# Persona defaults
# ---------------------------------------------------------------------------

def get_persona_defaults(persona: str) -> list[str]:
    """Return the agent action_type list for a named persona template."""
    return list(_PERSONA_DEFAULTS.get(persona, _PERSONA_DEFAULTS['Consultant']))


PERSONA_NAMES = list(_PERSONA_DEFAULTS.keys())


# ---------------------------------------------------------------------------
# Aggregate selector data
# ---------------------------------------------------------------------------

def expertise_zone_names(sim) -> list:
    """The zone names this simulation scores against — resume first, then its own."""
    from app.models.resume import Resume
    resume = Resume.query.get(sim.resume_id) if sim.resume_id else None
    raw_zones = (resume.expertise_zones or []) if resume else []
    names = [z.get('zone_name', '') for z in raw_zones if z.get('zone_name')]
    if not names and sim.expertise_zone:
        names = [sim.expertise_zone]
    return names


def relevance_scores(sim, agents_flat=None, persist: bool = False) -> dict:
    """Per-agent relevance for this simulation, from the cache when it exists.

    Pure scoring — no LLM — so a read-heavy caller can ask for this without the
    cost of the description generation in get_selector_data().
    """
    scores = sim.agent_relevance_scores
    if scores:
        return scores
    from app.services.agent_registry import get_all_agents
    scores = calculate_agent_relevance(expertise_zone_names(sim),
                                       agents_flat or get_all_agents())
    if persist:
        sim.agent_relevance_scores = scores
    return scores


def effective_selection(sim, scores: dict = None, agents_flat=None) -> set:
    """The agents Manage Agents treats as active for this simulation.

    Confirmed, with a saved selection: exactly what the user chose, plus the root
    agent and the event-triggered ones, which are never the user's to switch off.

    Not yet confirmed: the recommended default the selector itself would show —
    NOT every agent. Anything rendering "the active agents" has to agree with the
    Manage Agents screen, so this is the one place that decides.
    """
    from app.services.agent_registry import get_all_agents, resolve_alias
    agents_flat = agents_flat or get_all_agents()

    saved = sim.selected_agents
    if saved and sim.agent_selection_confirmed_at is not None:
        selection = {resolve_alias(at) for at in saved}
    else:
        scores = scores if scores is not None else relevance_scores(sim, agents_flat)
        selection = {
            agent['action_type'] for agent in agents_flat
            if scores.get(agent['action_type'], {}).get('recommended')
        }

    selection.add(ROOT_AGENT_ID)
    selection |= {resolve_alias(at) for at in TRIGGERED_AGENT_IDS}
    return selection


def get_selector_data(sim, force_regen_descriptions: bool = False) -> dict:
    """
    Return everything the agent selector UI needs for a given simulation.

    Lazily generates and caches:
      - agent_relevance_scores  (from resume expertise zones)
      - agent_personalized_descriptions  (Claude call)

    Returns a dict suitable for JSON serialisation.
    """
    from app.services.agent_registry import get_all_agents, ACTION_PREREQUISITES
    from app.models.resume import Resume

    agents_flat = get_all_agents()
    primary_expertise = sim.expertise_zone or ''

    # ── 1. Expertise zones from resume ──────────────────────────────────────
    resume = Resume.query.get(sim.resume_id) if sim.resume_id else None
    raw_zones = (resume.expertise_zones or []) if resume else []
    zone_names: list[str] = [z.get('zone_name', '') for z in raw_zones if z.get('zone_name')]
    if not zone_names and primary_expertise:
        zone_names = [primary_expertise]

    # ── 2. Relevance scores (compute once, cache on sim) ────────────────────
    needs_score_save = False
    scores = sim.agent_relevance_scores
    if not scores:
        scores = calculate_agent_relevance(zone_names, agents_flat)
        sim.agent_relevance_scores = scores
        needs_score_save = True

    # ── 3. Personalized descriptions (Claude call cached on the sim) ─────────
    # Regenerate only when there is no cache, when explicitly forced, or when the
    # inputs (expertise zones / primary expertise) changed since last generated.
    # A blocking Haiku call on every page load was the cause of slow /setup loads.
    needs_desc_save = False
    current_sig = _descriptions_input_sig(zone_names, primary_expertise)
    descriptions = sim.agent_personalized_descriptions
    cached_sig = descriptions.get(_DESCRIPTIONS_SIG_KEY) if descriptions else None
    if not descriptions or force_regen_descriptions or cached_sig != current_sig:
        descriptions = generate_personalized_descriptions(
            agents_flat, zone_names, primary_expertise,
            sim.user_id, sim.id,
        )
        descriptions[_DESCRIPTIONS_SIG_KEY] = current_sig
        sim.agent_personalized_descriptions = descriptions
        needs_desc_save = True

    if needs_score_save or needs_desc_save:
        try:
            from app.extensions import db
            db.session.commit()
        except Exception as exc:
            _log.warning('Could not save selector cache for sim %s: %s', sim.id, exc)
            try:
                from app.extensions import db
                db.session.rollback()
            except Exception:
                pass

    # ── 4. Build selected set (default = all recommended + triggered + root) ─
    # Shared with the Wealth Engine World, so the two surfaces cannot disagree
    # about which agents are active.
    selected_set = effective_selection(sim, scores=scores, agents_flat=agents_flat)

    # ── 5. Completed agents (for prerequisite + lock evaluation) ────────────
    from app.models.agent_action import AgentAction
    completed_types = set(
        r.action_type for r in AgentAction.query.filter_by(
            simulation_id=sim.id,
            status=AgentAction.STATUS_COMPLETE,
        ).with_entities(AgentAction.action_type).all()
    )

    # ── 6. Layer unlock status ───────────────────────────────────────────────
    layer_unlocked = _get_layer_unlock_status(sim, completed_types)

    # ── 7. Assemble per-layer structure ─────────────────────────────────────
    from app.models.agent_context import AgentContext
    layers_out = []
    for layer_num in range(1, 6):
        layer_agents = [a for a in agents_flat if a['layer'] == layer_num]
        unlocked = layer_unlocked.get(layer_num, True)
        # Stored parameter values for this layer (user-supplied answers reused by
        # the orchestrator via the prefill engine). Keyed by prompt-form field key.
        stored_ctx = AgentContext.get_for_layer(sim.id, layer_num)
        recommended_count = sum(
            1 for a in layer_agents
            if scores.get(a['action_type'], {}).get('recommended')
        )
        selected_count = sum(
            1 for a in layer_agents
            if a['action_type'] in selected_set
        )

        tiles = []
        for agent in layer_agents:
            at = agent['action_type']
            info = scores.get(at, {})
            prereqs = ACTION_PREREQUISITES.get(at, [])
            prereqs_met = all(p in completed_types for p in prereqs)
            prereqs_selected = all(p in selected_set for p in prereqs)
            is_triggered = at in TRIGGERED_AGENT_IDS
            is_root = at == ROOT_AGENT_ID

            # Lock status: unlocked if prereqs have completed OR are already selected
            agent_locked = not prereqs_met and not prereqs_selected and not is_triggered
            layer_locked_flag = not unlocked

            # Editable parameters (prompt-form fields + any stored value).
            params = []
            for field in agent.get('prompt_form', []):
                params.append({
                    'key': field['key'],
                    'label': field.get('label', field['key']),
                    'type': field.get('type', 'text'),
                    'options': field.get('options', []),
                    'required': bool(field.get('required')),
                    'value': stored_ctx.get(field['key']) or '',
                })

            tiles.append({
                'action_type': at,
                'label': agent['label'],
                'description': descriptions.get(at, agent.get('description', '')),
                'layer': layer_num,
                'selected': at in selected_set,
                'recommended': info.get('recommended', False),
                'score': info.get('score', 0.0),
                'is_triggered': is_triggered,
                'is_root': is_root,
                'agent_locked': agent_locked,
                'layer_locked': layer_locked_flag,
                'prerequisites': prereqs,
                'prerequisites_met': prereqs_met,
                'prerequisites_selected': prereqs_selected,
                'integrations': agent.get('integrations', []),
                'params': params,
            })

        layers_out.append({
            'layer_number': layer_num,
            'name': LAYER_NAMES[layer_num],
            'description': LAYER_DESCRIPTIONS[layer_num],
            'unlocked': unlocked,
            'lock_condition': LAYER_LOCK_CONDITIONS.get(layer_num),
            'recommended_count': recommended_count,
            'selected_count': selected_count,
            'total_count': len(layer_agents),
            'agents': tiles,
        })

    return {
        'sim_id': sim.id,
        'sim_name': sim.name,
        'expertise_zone': primary_expertise,
        'zone_names': zone_names,
        'selected_agents': list(selected_set),
        'layers': layers_out,
        'persona_names': PERSONA_NAMES,
        'already_confirmed': sim.agent_selection_confirmed_at is not None,
    }


def _get_layer_unlock_status(sim, completed_types: set) -> dict[int, bool]:
    """Layers 1-3 always unlocked. 4 and 5 depend on milestones or unlock_all_layers flag."""
    if sim.unlock_all_layers:
        return {1: True, 2: True, 3: True, 4: True, 5: True}

    # L4 unlocks after first product sale (any L3 agent has completed)
    from app.services.agent_registry import get_agents_by_layer
    l3_agents = {a['action_type'] for a in get_agents_by_layer(3)}
    l4_unlocked = bool(l3_agents & completed_types)

    # L5 unlocks after any L4 agent completes
    l4_agents = {a['action_type'] for a in get_agents_by_layer(4)}
    l5_unlocked = bool(l4_agents & completed_types)

    return {1: True, 2: True, 3: True, 4: l4_unlocked, 5: l5_unlocked}
