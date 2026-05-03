# utils/model_router.py
# Single source of truth for Claude API model selection.
# SIM-ENG-MODEL-001 — never hardcode model strings elsewhere.
# Import: from utils.model_router import get_model, get_tier, use_batch_api, build_system_prompt_block
from enum import Enum


class ModelTier(str, Enum):
    HAIKU  = 'haiku'
    SONNET = 'sonnet'
    OPUS   = 'opus'


# ── Model IDs ────────────────────────────────────────────────────────────────
# DO NOT use claude-opus-4-7: new tokenizer adds up to 35% tokens for same input.
# Pin Opus to 4.6 until tokenizer benchmarking is complete against Simulacrum prompts.

MODELS = {
    ModelTier.HAIKU:  'claude-haiku-4-5-20251001',
    ModelTier.SONNET: 'claude-sonnet-4-6',
    ModelTier.OPUS:   'claude-opus-4-6',
}

# ── Action → Tier routing table ──────────────────────────────────────────────
# Keys match action_type values in layer6_action_queue and system call names.
# Any action_type not listed here defaults to SONNET (safe fallback).

_ROUTING: dict[str, ModelTier] = {
    # ── HAIKU — extraction, classification, structured output ─────────────
    'expertise_zone_extraction':   ModelTier.HAIKU,
    'role_search':                 ModelTier.HAIKU,
    'booking_page':                ModelTier.HAIKU,
    'roi_calculator':              ModelTier.HAIKU,
    'speaker_fee_rider':           ModelTier.HAIKU,
    'ab_test_plan':                ModelTier.HAIKU,
    'competitor_research':         ModelTier.HAIKU,
    'affiliate_partnerships':      ModelTier.HAIKU,
    'compound_projections':        ModelTier.HAIKU,
    'dca_schedule':                ModelTier.HAIKU,
    # Orchestrator / system calls
    'orchestrator_reasoning':      ModelTier.HAIKU,
    'prefill_input_generation':    ModelTier.HAIKU,
    'stale_route_evaluation':      ModelTier.HAIKU,
    'artifact_change_summary':     ModelTier.HAIKU,
    'resume_consent_disclosure':   ModelTier.HAIKU,
    # ── OPUS — high-precision legal/financial documents ───────────────────
    'investment_policy_statement': ModelTier.OPUS,
    'tax_optimization':            ModelTier.OPUS,
    'entity_structure':            ModelTier.OPUS,
    'estate_planning_checklist':   ModelTier.OPUS,
    # All remaining action_types → SONNET (default, see get_model())
}


def get_model(action_type: str) -> str:
    """Return the Claude model ID for a given action_type.

    Falls back to SONNET for any action_type not in the routing table.

    Examples:
        get_model('cold_email_campaign')       → 'claude-sonnet-4-6'
        get_model('dca_schedule')              → 'claude-haiku-4-5-20251001'
        get_model('tax_optimization')          → 'claude-opus-4-6'
    """
    tier = _ROUTING.get(action_type, ModelTier.SONNET)
    return MODELS[tier]


def get_tier(action_type: str) -> ModelTier:
    """Return the ModelTier enum for an action_type. Useful for logging."""
    return _ROUTING.get(action_type, ModelTier.SONNET)


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
