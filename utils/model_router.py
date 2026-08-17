# utils/model_router.py
# Single source of truth for Claude API model selection.
# SIM-ENG-MODEL-001 — never hardcode model strings elsewhere.
# Import: from utils.model_router import get_model, get_tier, use_batch_api, build_system_prompt_block
from enum import Enum


class ModelTier(str, Enum):
    HAIKU  = 'haiku'
    SONNET = 'sonnet'
    OPUS   = 'opus'
    FABLE  = 'fable'


# ── Model IDs ────────────────────────────────────────────────────────────────
# DO NOT use claude-opus-4-7: new tokenizer adds up to 35% tokens for same input.
# Pin Opus to 4.6 until tokenizer benchmarking is complete against Simulacrum prompts.

MODELS = {
    ModelTier.HAIKU:  'claude-haiku-4-5-20251001',
    ModelTier.SONNET: 'claude-sonnet-4-6',
    ModelTier.OPUS:   'claude-opus-4-6',
    ModelTier.FABLE:  'claude-fable-5',
}

# ── Action → Tier routing table ──────────────────────────────────────────────
# Keys match action_type values in layer6_action_queue and system call names.
# Any action_type not listed here defaults to SONNET (safe fallback).

_ROUTING: dict[str, ModelTier] = {
    # ── HAIKU — extraction, classification, structured output ─────────────
    'expertise_zone_extraction':   ModelTier.HAIKU,
    # L1
    'role_search':                 ModelTier.HAIKU,
    'booking_page':                ModelTier.HAIKU,
    'roi_calculator':              ModelTier.HAIKU,
    'linkedin_optimization':       ModelTier.HAIKU,
    'pitch_deck_outline':          ModelTier.HAIKU,
    'sow_template':                ModelTier.HAIKU,
    'negotiation_script':          ModelTier.HAIKU,
    'agreement_template':          ModelTier.HAIKU,
    'referral_network':            ModelTier.HAIKU,
    # L2 — structured lists, templates, email sequences
    'speaker_fee_rider':           ModelTier.HAIKU,
    'speaking_proposals':          ModelTier.HAIKU,
    'group_coaching':              ModelTier.HAIKU,
    'workshop_curriculum':         ModelTier.HAIKU,
    'corporate_training':          ModelTier.HAIKU,
    'waitlist_landing_page':       ModelTier.HAIKU,
    'alumni_reactivation':         ModelTier.HAIKU,
    # L3 — outlines, frameworks, email sequences
    'ab_test_plan':                ModelTier.HAIKU,
    'pricing_research':            ModelTier.HAIKU,
    'ebook_outline':               ModelTier.HAIKU,
    'membership':                  ModelTier.HAIKU,
    'launch_sequence':             ModelTier.HAIKU,
    'affiliate_program':           ModelTier.HAIKU,
    'testimonials':                ModelTier.HAIKU,
    'lapsed_buyer':                ModelTier.HAIKU,
    # L4 — strategy plans, calendars, funnels
    'affiliate_partnerships':      ModelTier.HAIKU,
    'client_winback':              ModelTier.HAIKU,
    'newsletter':                  ModelTier.HAIKU,
    'lead_magnet_funnel':          ModelTier.HAIKU,
    'community':                   ModelTier.HAIKU,
    'ip_licensing':                ModelTier.HAIKU,
    'programmatic_ads':            ModelTier.HAIKU,
    'seo_content_calendar':        ModelTier.HAIKU,
    # L5 — financial strategy, projections, allocation
    'income_allocation':           ModelTier.HAIKU,
    'projections':                 ModelTier.HAIKU,
    'fund_recommendations':        ModelTier.HAIKU,
    'real_estate_strategy':        ModelTier.HAIKU,
    'insurance_review':            ModelTier.HAIKU,
    # Structured calculation/analysis helpers (all layers)
    'compound_projections':        ModelTier.HAIKU,
    'dca_schedule':                ModelTier.HAIKU,
    'workshop_roi':                ModelTier.HAIKU,
    'competitive_pricing':         ModelTier.HAIKU,
    'testimonial_system':          ModelTier.HAIKU,
    'lapsed_buyer_reactivation':   ModelTier.HAIKU,
    'template_pack_spec':          ModelTier.HAIKU,
    'compound_growth':             ModelTier.HAIKU,
    'insurance_gap_analysis':      ModelTier.HAIKU,
    'competitor_research':         ModelTier.HAIKU,
    # Orchestrator / system calls
    'orchestrator_reasoning':      ModelTier.HAIKU,
    'prefill_input_generation':    ModelTier.HAIKU,
    'stale_route_evaluation':      ModelTier.HAIKU,
    'artifact_change_summary':     ModelTier.HAIKU,
    'resume_consent_disclosure':   ModelTier.HAIKU,
    'contact_score':               ModelTier.HAIKU,
    'intent_classify':             ModelTier.HAIKU,
    'consulting_outreach_research': ModelTier.HAIKU,
    # SIM-PRD-CAL-001 — cohort keys and numeric-field extraction are pure
    # structured-output tasks; Haiku is the right tier and keeps the Calibration
    # Layer's marginal cost per artifact effectively nil.
    'cohort_classification':       ModelTier.HAIKU,
    'calibration_metric_extract':  ModelTier.HAIKU,
    # ── SONNET — conversational, creative, long-form copy ────────────────
    'consulting_outreach_email':   ModelTier.SONNET,  # 10 deeply personalized emails
    'chat_copilot':                ModelTier.SONNET,
    'bio_chat_complex':            ModelTier.SONNET,
    'course_curriculum':           ModelTier.SONNET,  # full multi-module curriculum
    'sales_page':                  ModelTier.SONNET,  # long-form persuasive copy
    'video_podcast':               ModelTier.SONNET,  # 3 full episode scripts
    'saas_product_spec':           ModelTier.SONNET,  # complex product specification
    # ── HAIKU — bio chat classification and simple questions ─────────────
    'bio_chat_classify':           ModelTier.HAIKU,
    'bio_chat_simple':             ModelTier.HAIKU,
    # ── OPUS — high-precision legal/financial documents ───────────────────
    'investment_policy':           ModelTier.OPUS,   # detailed IPS document
    'tax_optimization':            ModelTier.OPUS,
    'entity_structure':            ModelTier.OPUS,
    'estate_planning':             ModelTier.OPUS,
    # All remaining action_types → SONNET (default, see get_model())
}


def _canonical(action_type: str) -> str:
    """Resolve legacy action_type aliases so routing never depends on which
    name variant was stored. Lazy import avoids a module-load cycle."""
    try:
        from app.services.agent_registry import resolve_alias
        return resolve_alias(action_type)
    except Exception:
        return action_type


def get_model(action_type: str) -> str:
    """Return the Claude model ID for a given action_type.

    Aliases are resolved to their canonical name first, then falls back to
    SONNET for any action_type not in the routing table.

    Examples:
        get_model('cold_email_campaign')       → 'claude-sonnet-4-6'
        get_model('dca_schedule')              → 'claude-haiku-4-5-20251001'
        get_model('tax_optimization')          → 'claude-opus-4-6'
    """
    tier = _ROUTING.get(_canonical(action_type), ModelTier.SONNET)
    return MODELS[tier]


def get_tier(action_type: str) -> ModelTier:
    """Return the ModelTier enum for an action_type. Useful for logging."""
    return _ROUTING.get(_canonical(action_type), ModelTier.SONNET)


def use_batch_api(dispatch_source: str) -> bool:
    """Return True if the Batch API (50% discount) should be used.

    dispatch_source values:
        'orchestrator'  → Batch API (async, 50% discount)
        'user_run_now'  → standard API (real-time required)
        'user_rerun'    → standard API (real-time required)
    """
    return dispatch_source == 'orchestrator'


def build_system_prompt_block(expertise_zone: str, extracted_data: dict) -> dict:
    """Return the system prompt as a cache-eligible content block.

    Pass this block as the system prompt for all actions dispatched in the same
    cycle. The Expertise Zone and extracted_data are identical across all actions
    in a cycle, so Anthropic caches this block after the first call (90% off
    on subsequent cache hits within the same cycle).
    """
    return {
        'type': 'text',
        'text': (
            f'Expertise Zone: {expertise_zone}\n\n'
            f'Career extracted data:\n{extracted_data}'
        ),
        'cache_control': {'type': 'ephemeral'},
    }
