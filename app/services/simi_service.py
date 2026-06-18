"""
SIM-PRD-CHAT-001 v1.2 — Simi GCC Co-pilot service.
13-source context · 12 tools · Haiku/Sonnet routing · token budget · tab-aware opening.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from flask import current_app

logger = logging.getLogger(__name__)

HAIKU_MODEL  = 'claude-haiku-4-5-20251001'
SONNET_MODEL = 'claude-sonnet-4-6'

TOKEN_BUDGET_HARD  = 50_000
TOKEN_BUDGET_WARN  = 40_000   # 80 %

# Keywords that route to Sonnet (complex analysis)
_SONNET_KEYWORDS = {
    'compare', 'why', 'draft', 'brief', 'explain', 'suggest', 'recommend',
    'analyze', 'analyse', 'interpret', 'strategy', 'pricing', 'should',
    'walk me through', 'break down', 'summarize', 'help me respond',
    'help me write', 'what do you think', 'how am i doing',
}

# Tab → suggested pills
_TAB_SUGGESTIONS: dict[str, list[str]] = {
    'journey':     ['What should I do next?', 'Explain my last cycle',
                    'How is my income growing?', 'Which agents should I run?'],
    'queue':       ['What\'s most urgent?', 'Brief me for my next call',
                    'Who should I follow up with?', 'Draft a reply to a contact'],
    'income':      ['Break down my income by layer', 'What\'s my ROI?',
                    'Which agent generated the most revenue?', 'What\'s my monthly recurring?'],
    'momentum':    ['How is my momentum trending?', 'What\'s my email open rate?',
                    'Which metric should I focus on?', 'Explain my Bayesian scores'],
    'cycle':       ['Explain this cycle\'s decisions', 'Compare to last cycle',
                    'What follow-ups are coming?', 'Why was this agent dispatched?'],
    'visuals':     ['Walk me through my wealth pyramid', 'Which layer is strongest?',
                    'What\'s my path to passive income?', 'Explain my layer progress'],
    'network':     ['Which agents haven\'t run yet?', 'What\'s blocking a locked agent?',
                    'Show me agent scores', 'What does an agent do?'],
    'escalations': ['Are there any issues?', 'Which integration is expiring?',
                    'What escalations need my attention?', 'Is everything running?'],
}

# Tab → opening message template keys
_TAB_OPENING: dict[str, str] = {
    'journey':     'journey',
    'queue':       'queue',
    'income':      'income',
    'momentum':    'momentum',
    'cycle':       'cycle',
    'visuals':     'visuals',
    'network':     'network',
    'escalations': 'escalations',
}

# Tool navigation mapping: PRD tab names → actual HTML panel names
_NAV_TAB_MAP = {
    'journey':      'journey',
    'action_queue': 'queue',
    'income':       'income',
    'momentum':     'momentum',
    'cycle':        'cycle',
    'visuals':      'visuals',
    'agent_network': 'network',
    'escalations':  'escalations',
}


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])


# ── Model routing ──────────────────────────────────────────────────────────────

def route_model(message: str) -> str:
    """Return HAIKU_MODEL for simple lookups, SONNET_MODEL for complex analysis."""
    lower = message.lower()
    for kw in _SONNET_KEYWORDS:
        if kw in lower:
            return SONNET_MODEL
    return HAIKU_MODEL


# ── Context building ───────────────────────────────────────────────────────────

def build_simi_context(sim_id: str, user_id: str) -> dict:
    """Build the full 13-source context for the Simi system prompt."""
    from app.extensions import db as _db
    from sqlalchemy import func

    ctx: dict = {}

    # ── 1. User profile ────────────────────────────────────────────────────────
    try:
        from app.models.user import User
        from app.models.profile import UserProfile
        user = User.query.get(user_id)
        profile = UserProfile.query.filter_by(user_id=user_id).first()
        ctx['user_name']       = (user.full_name or '').strip() if user else ''
        ctx['user_first_name'] = ctx['user_name'].split()[0] if ctx['user_name'] else 'there'
        ctx['user_title']      = getattr(profile, 'professional_title', '') or ''
        ctx['expertise_zones'] = getattr(profile, 'expertise_zones', '') or ''
        ctx['positioning']     = (getattr(profile, 'positioning_paragraph', '') or '')[:300]
    except Exception as e:
        logger.debug('Context user profile error: %s', e)
        _db.session.rollback()
        ctx.setdefault('user_name', '')
        ctx.setdefault('user_first_name', 'there')
        ctx.setdefault('user_title', '')
        ctx.setdefault('expertise_zones', '')
        ctx.setdefault('positioning', '')

    # ── 2. Simulation state ────────────────────────────────────────────────────
    try:
        from app.models.simulation import Simulation
        from app.models.layer6 import Layer6Cycle, Layer6Config
        sim    = Simulation.query.get(sim_id)
        cycle  = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
            Layer6Cycle.cycle_number.desc()).first()
        config = Layer6Config.query.filter_by(simulation_id=sim_id).first()

        ctx['sim_name']    = sim.name if sim else ''
        ctx['sim_status']  = sim.status if sim else 'active'
        ctx['cycle_num']   = cycle.cycle_number if cycle else 0
        ctx['phase']       = cycle.phase if cycle else 'explore'
        ctx['trust_level'] = getattr(config, 'trust_level', 'balanced') or 'balanced' if config else 'balanced'
        ctx['prospect_tier'] = getattr(sim, 'prospect_tier', 1) or 1 if sim else 1

        # Next cycle
        next_cycle_text = 'Not scheduled'
        if config and cycle and cycle.cycle_started_at:
            cadence_days = {'daily': 1, 'every_3_days': 3, 'weekly': 7}
            days = cadence_days.get(getattr(config, 'cadence', 'weekly'), 7)
            next_run = cycle.cycle_started_at + timedelta(days=days)
            if next_run > datetime.utcnow():
                next_cycle_text = next_run.strftime('%b %d at %I:%M %p UTC')
        ctx['next_cycle_at'] = next_cycle_text

        # Latest cycle reasoning
        ctx['last_cycle_reasoning']  = (cycle.orchestrator_reasoning or '')[:600] if cycle else ''
        ctx['last_cycle_agents']     = getattr(cycle, 'actions_dispatched', '') or '' if cycle else ''
        ctx['last_cycle_id']         = cycle.id if cycle else ''
    except Exception as e:
        logger.debug('Context sim state error: %s', e)
        _db.session.rollback()
        ctx.setdefault('sim_name', '')
        ctx.setdefault('cycle_num', 0)
        ctx.setdefault('phase', 'explore')
        ctx.setdefault('trust_level', 'balanced')
        ctx.setdefault('prospect_tier', 1)
        ctx.setdefault('next_cycle_at', 'Not scheduled')
        ctx.setdefault('last_cycle_reasoning', '')
        ctx.setdefault('last_cycle_agents', '')
        ctx.setdefault('last_cycle_id', '')

    # ── 3. Rate card (artifact) ────────────────────────────────────────────────
    try:
        from app.models.artifact import ArtifactVersion
        rc = ArtifactVersion.query.filter_by(
            simulation_id=sim_id, action_type='rate_card', is_current=True
        ).first()
        ctx['rate_card_summary'] = (rc.content or '')[:500] if rc else 'Not generated yet.'
    except Exception as e:
        logger.debug('Context rate card error: %s', e)
        _db.session.rollback()
        ctx['rate_card_summary'] = 'Not generated yet.'

    # ── 4. Agent status ────────────────────────────────────────────────────────
    try:
        from app.models.agent_action import AgentAction
        actions = AgentAction.query.filter_by(simulation_id=sim_id).order_by(
            AgentAction.layer_number, AgentAction.action_type).limit(60).all()
        agent_lines = []
        for a in actions:
            agent_lines.append(
                f'L{a.layer_number} {a.action_type}: {a.status}'
                + (f' (cycle {a.cycle_number})' if getattr(a, "cycle_number", None) else '')
            )
        ctx['agent_status_block'] = '\n'.join(agent_lines) if agent_lines else 'No agents run yet.'
    except Exception as e:
        logger.debug('Context agent status error: %s', e)
        _db.session.rollback()
        ctx['agent_status_block'] = 'No agents run yet.'

    # ── 5. Artifacts list ──────────────────────────────────────────────────────
    try:
        from app.models.artifact import ArtifactVersion
        artifacts = ArtifactVersion.query.filter_by(
            simulation_id=sim_id, is_current=True
        ).order_by(ArtifactVersion.created_at.desc()).limit(20).all()
        art_lines = [f'  {a.action_type} (v{a.version_number}, {a.created_at.strftime("%b %d")})'
                     for a in artifacts]
        ctx['artifacts_list'] = '\n'.join(art_lines) if art_lines else 'No artifacts yet.'
    except Exception as e:
        logger.debug('Context artifacts error: %s', e)
        _db.session.rollback()
        ctx['artifacts_list'] = 'No artifacts yet.'

    # ── 6. Bayesian scores ─────────────────────────────────────────────────────
    try:
        from app.models.bayesian import BayesianPosterior
        scores = BayesianPosterior.query.filter_by(simulation_id=sim_id).order_by(
            BayesianPosterior.value.desc()).limit(10).all()
        score_lines = [f'  {s.posterior_key}: {float(s.value):.3f}' for s in scores]
        ctx['bayesian_scores'] = '\n'.join(score_lines) if score_lines else 'No scores yet.'
    except Exception as e:
        logger.debug('Context bayesian error: %s', e)
        _db.session.rollback()
        ctx['bayesian_scores'] = 'No scores yet.'

    # ── 7. CRM contacts ────────────────────────────────────────────────────────
    try:
        from app.models.contact import Contact
        total  = Contact.query.filter_by(user_id=user_id, is_archived=False).count()
        stages = {}
        for stage in ('prospect', 'active', 'client', 'closed_lost'):
            stages[stage] = Contact.query.filter_by(
                user_id=user_id, pipeline_stage=stage, is_archived=False).count()
        ctx['contact_total']  = total
        ctx['contact_stages'] = stages
        # Recent 5 contacts for quick reference
        recent = Contact.query.filter_by(user_id=user_id, is_archived=False).order_by(
            Contact.created_at.desc()).limit(5).all()
        ctx['recent_contacts'] = ', '.join(
            c.display_name for c in recent if c.display_name) or 'None yet.'
    except Exception as e:
        logger.debug('Context contacts error: %s', e)
        _db.session.rollback()
        ctx['contact_total']   = 0
        ctx['contact_stages']  = {'prospect': 0, 'active': 0, 'client': 0, 'closed_lost': 0}
        ctx['recent_contacts'] = 'None yet.'

    # ── 8. Email activity ──────────────────────────────────────────────────────
    try:
        from app.models.outreach_email import EmailLog
        logs = EmailLog.query.filter_by(simulation_id=sim_id).all()
        sent      = len(logs)
        opened    = sum(1 for l in logs if l.open_count > 0)
        replied   = sum(1 for l in logs if l.replied_at)
        bounced   = sum(1 for l in logs if l.bounced_at)
        open_rate  = round(opened / sent * 100, 1)  if sent else 0.0
        reply_rate = round(replied / sent * 100, 1) if sent else 0.0
        ctx['email_sent']       = sent
        ctx['email_opened']     = opened
        ctx['email_replied']    = replied
        ctx['email_bounced']    = bounced
        ctx['email_open_rate']  = open_rate
        ctx['email_reply_rate'] = reply_rate
    except Exception as e:
        logger.debug('Context email error: %s', e)
        _db.session.rollback()
        ctx.update({'email_sent': 0, 'email_opened': 0, 'email_replied': 0,
                    'email_bounced': 0, 'email_open_rate': 0.0, 'email_reply_rate': 0.0})

    # ── 9. Income by layer ─────────────────────────────────────────────────────
    try:
        from app.models.income import LayerIncomeRecord
        income_rows = _db.session.query(
            LayerIncomeRecord.layer_number,
            func.sum(LayerIncomeRecord.amount).label('total')
        ).filter_by(simulation_id=sim_id, is_void=False).group_by(
            LayerIncomeRecord.layer_number
        ).all()
        income_by_layer = {int(r.layer_number): float(r.total or 0) for r in income_rows}
        ctx['income_by_layer'] = income_by_layer
        ctx['total_income']    = sum(income_by_layer.values())
    except Exception as e:
        logger.debug('Context income error: %s', e)
        _db.session.rollback()
        ctx['income_by_layer'] = {}
        ctx['total_income']    = 0.0

    # ── 10. Action steps (pending) ─────────────────────────────────────────────
    try:
        from app.models.action_step import ActionStep
        steps = ActionStep.query.filter_by(
            simulation_id=sim_id, status=ActionStep.STATUS_SCHEDULED
        ).order_by(ActionStep.scheduled_for).limit(10).all()
        step_lines = []
        for s in steps:
            step_lines.append(
                f'  {s.scheduled_for.strftime("%b %d")} | {s.action_type} | {s.step_type}'
                + (f' | condition: {s.condition_type}' if s.condition_type else '')
            )
        ctx['pending_steps'] = '\n'.join(step_lines) if step_lines else 'No pending steps.'
        ctx['pending_steps_count'] = len(steps)
    except Exception as e:
        logger.debug('Context steps error: %s', e)
        _db.session.rollback()
        ctx['pending_steps']       = 'No pending steps.'
        ctx['pending_steps_count'] = 0

    # ── 11. Integration status ─────────────────────────────────────────────────
    try:
        from app.models.integration import UserIntegration
        integ = UserIntegration.query.filter_by(user_id=user_id).all()
        integ_lines = []
        for i in integ:
            status = i.connection_status
            expiry_note = ''
            if i.token_expires_at:
                days_left = (i.token_expires_at - datetime.utcnow()).days
                if 0 < days_left <= 7:
                    expiry_note = f' (expires in {days_left}d!)'
            integ_lines.append(f'  {i.provider}: {status}{expiry_note}')
        ctx['integration_status'] = '\n'.join(integ_lines) if integ_lines else 'No integrations connected.'
    except Exception as e:
        logger.debug('Context integrations error: %s', e)
        _db.session.rollback()
        ctx['integration_status'] = 'No integrations connected.'

    return ctx


def build_system_prompt(ctx: dict, current_tab: str, user_first_name: str) -> str:
    """Format the Simi system prompt from the built context."""
    income = ctx.get('income_by_layer', {})
    income_lines = '\n'.join(
        f'  L{n}: ${income.get(n, 0):,.2f}' for n in range(1, 6)
    )
    income_lines += f'\n  Total: ${ctx.get("total_income", 0):,.2f}'

    cs = ctx.get('contact_stages', {})
    return f"""\
You are Simi, the AI co-pilot for {user_first_name}'s Simulacrum wealth simulation.

ROLE: You are an advisor, navigator, and form assistant. You explain what happened, why it happened, and what to do next. You do NOT take actions. You do NOT send emails, dispatch agents, modify artifacts, or execute transactions. You navigate the interface and pre-fill forms for the user to confirm.

VOICE: Warm, direct, data-grounded. Use the user's first name. Reference specific numbers, agent names, and contact names. Never hedge when you have data. Say "your reply rate is 6.7%" not "your reply rate appears to be around 6-7%."

GUARDRAILS:
- NEVER dispatch agents or trigger any integration
- NEVER send emails or make API calls
- NEVER modify artifacts, scores, or settings (fill_form populates fields only — user must confirm)
- NEVER access other simulations or other users' data
- NEVER expose API keys or credentials
- NEVER provide legal/tax/medical advice without disclaimer: "This is advisory only."
- If data is not in context, say "I don't have that information in this simulation" — never hallucinate

USER CONTEXT:
{user_first_name} — {ctx.get('user_title', '')}
Expertise: {ctx.get('expertise_zones', '')}
Positioning: {ctx.get('positioning', '')}

SIMULATION STATE:
Name: {ctx.get('sim_name', '')}
Cycle: {ctx.get('cycle_num', 0)} | Phase: {ctx.get('phase', 'explore')}
Next cycle: {ctx.get('next_cycle_at', 'Not scheduled')} | Prospect tier: {ctx.get('prospect_tier', 1)}
Trust controls: {ctx.get('trust_level', 'balanced')} | Status: {ctx.get('sim_status', 'active')}
Total confirmed income: ${ctx.get('total_income', 0):,.2f}

CURRENT GCC TAB: {current_tab}

RATE CARD SUMMARY:
{ctx.get('rate_card_summary', 'Not generated yet.')}

AGENTS:
{ctx.get('agent_status_block', 'No agents run yet.')}

ARTIFACTS GENERATED:
{ctx.get('artifacts_list', 'No artifacts yet.')}

BAYESIAN SCORES (top):
{ctx.get('bayesian_scores', 'No scores yet.')}

LATEST CYCLE REASONING:
{ctx.get('last_cycle_reasoning', 'No cycle run yet.')}

CRM SUMMARY:
{ctx.get('contact_total', 0)} contacts — {cs.get('prospect', 0)} prospects, {cs.get('active', 0)} active, {cs.get('client', 0)} clients
Recent: {ctx.get('recent_contacts', 'None')}

EMAIL SUMMARY:
{ctx.get('email_sent', 0)} sent | {ctx.get('email_opened', 0)} opened ({ctx.get('email_open_rate', 0)}%) | {ctx.get('email_replied', 0)} replied ({ctx.get('email_reply_rate', 0)}%) | {ctx.get('email_bounced', 0)} bounced

INCOME BY LAYER:
{income_lines}

UPCOMING STEPS:
{ctx.get('pending_steps', 'No pending steps.')}

INTEGRATIONS:
{ctx.get('integration_status', 'No integrations connected.')}
"""


# ── Tool definitions ───────────────────────────────────────────────────────────

SIMI_TOOLS = [
    {
        'name': 'get_artifact',
        'description': 'Get the full text of a specific artifact by agent/action type. Use when the user asks to see the full rate card, LinkedIn copy, email sequence, course curriculum, etc.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'action_type': {'type': 'string', 'description': 'The agent/action type, e.g. "rate_card", "linkedin_headline", "email_sequence_1"'}
            },
            'required': ['action_type'],
        },
    },
    {
        'name': 'get_contact',
        'description': 'Get the full detail of a CRM contact by name or contact_id: email, company, pipeline stage, outreach history, qualifying notes.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'name_or_id': {'type': 'string', 'description': 'Contact full name or contact ID'}
            },
            'required': ['name_or_id'],
        },
    },
    {
        'name': 'get_email_history',
        'description': 'Get all emails sent to a specific contact or during a specific cycle. Returns subject, sent date, status, open/reply details.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'contact_id': {'type': 'string', 'description': 'Contact ID (optional)'},
                'cycle_id':   {'type': 'string', 'description': 'Cycle ID (optional)'},
            },
        },
    },
    {
        'name': 'get_cycle_detail',
        'description': 'Get the full detail for a specific cycle: agents dispatched, reasoning, signals, scores, contacts added, emails sent.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'cycle_id': {'type': 'string', 'description': 'Cycle ID or cycle number (e.g. "9")'}
            },
            'required': ['cycle_id'],
        },
    },
    {
        'name': 'get_agent_status',
        'description': 'Get the detailed status of a specific agent: dispatched cycles, prerequisites, current Bayesian score, score trend.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'agent_name': {'type': 'string', 'description': 'Agent/action type name, e.g. "consulting_outreach"'}
            },
            'required': ['agent_name'],
        },
    },
    {
        'name': 'get_pending_steps',
        'description': 'Get all scheduled action steps with urgency, suggested date, condition type, and contact name. Grouped by urgency.',
        'input_schema': {
            'type': 'object',
            'properties': {},
        },
    },
    {
        'name': 'get_income_detail',
        'description': 'Get all income records for the simulation, grouped by layer with per-record detail.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'layer': {'type': 'integer', 'description': 'Filter to a specific layer (1-5), optional'}
            },
        },
    },
    {
        'name': 'search_contacts',
        'description': 'Search CRM contacts by name, company, or pipeline stage.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'Name or company to search for'},
                'stage': {'type': 'string', 'description': 'Optional: prospect, active, client, closed_lost'},
            },
            'required': ['query'],
        },
    },
    {
        'name': 'navigate_to_tab',
        'description': 'Navigate the GCC to a specific tab while keeping the Simi panel open. Always narrate: "I have opened the X tab."',
        'input_schema': {
            'type': 'object',
            'properties': {
                'tab_name': {
                    'type': 'string',
                    'enum': ['journey', 'action_queue', 'income', 'momentum', 'cycle', 'visuals', 'agent_network', 'escalations'],
                    'description': 'The tab to navigate to',
                }
            },
            'required': ['tab_name'],
        },
    },
    {
        'name': 'highlight_element',
        'description': 'Place a pulsing teal border around a specific UI element for 5 seconds. Use when referencing a specific item.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'element_type': {
                    'type': 'string',
                    'enum': ['agent_card', 'contact_row', 'cycle_row', 'income_row', 'step_row'],
                },
                'element_id': {'type': 'string', 'description': 'The ID of the element to highlight'},
            },
            'required': ['element_type', 'element_id'],
        },
    },
    {
        'name': 'fill_form',
        'description': 'Pre-fill form fields on the currently open tab. Fields are populated but NOT submitted. The user must click the submit/run button.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'form_id': {'type': 'string', 'description': 'The form/panel ID to fill'},
                'fields':  {
                    'type': 'object',
                    'description': 'Key-value pairs of field names and values to pre-fill',
                },
            },
            'required': ['form_id', 'fields'],
        },
    },
    {
        'name': 'get_form_fields',
        'description': 'Get the available form fields on the currently open tab. Returns field names, types, current values, and options. Call this before fill_form.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'form_id': {'type': 'string', 'description': 'Optional: form/panel ID. Omit to get all forms on the current tab.'}
            },
        },
    },
]

# UI tools — executed client-side, not server-side
_UI_TOOLS = {'navigate_to_tab', 'highlight_element', 'fill_form', 'get_form_fields'}


# ── Server-side tool execution ─────────────────────────────────────────────────

def _exec_get_artifact(sim_id: str, action_type: str) -> str:
    try:
        from app.models.artifact import ArtifactVersion
        av = ArtifactVersion.query.filter_by(
            simulation_id=sim_id, action_type=action_type, is_current=True
        ).first()
        if not av:
            return f'No artifact found for action type "{action_type}".'
        return av.content or f'Artifact "{action_type}" exists but has no content.'
    except Exception as e:
        return f'Error fetching artifact: {e}'


def _exec_get_contact(user_id: str, name_or_id: str) -> str:
    try:
        from app.models.contact import Contact
        c = Contact.query.filter_by(id=name_or_id, user_id=user_id, is_archived=False).first()
        if not c:
            parts = name_or_id.split()
            q = Contact.query.filter_by(user_id=user_id, is_archived=False)
            if len(parts) >= 2:
                q = q.filter(Contact.first_name.ilike(f'%{parts[0]}%'),
                             Contact.last_name.ilike(f'%{parts[-1]}%'))
            else:
                q = q.filter(
                    (Contact.first_name.ilike(f'%{name_or_id}%')) |
                    (Contact.last_name.ilike(f'%{name_or_id}%'))
                )
            c = q.first()
        if not c:
            return f'Contact "{name_or_id}" not found.'
        return (
            f'Name: {c.display_name}\n'
            f'Email: {c.email}\n'
            f'Company: {c.company_name or "N/A"}\n'
            f'Title: {c.job_title or "N/A"}\n'
            f'Stage: {c.pipeline_stage}\n'
            f'Source: {c.source or "N/A"}\n'
            f'Created: {c.created_at.strftime("%b %d, %Y") if c.created_at else "N/A"}\n'
        )
    except Exception as e:
        return f'Error fetching contact: {e}'


def _exec_get_email_history(sim_id: str, contact_id: str = None, cycle_id: str = None) -> str:
    try:
        from app.models.outreach_email import EmailLog
        q = EmailLog.query.filter_by(simulation_id=sim_id)
        if contact_id:
            q = q.filter_by(contact_id=contact_id)
        logs = q.order_by(EmailLog.sent_at.desc()).limit(20).all()
        if not logs:
            return 'No email history found.'
        lines = []
        for l in logs:
            lines.append(
                f'{l.sent_at.strftime("%b %d") if l.sent_at else "N/A"} | '
                f'{l.subject[:60]} | {l.status}'
                + (f' | opened {l.open_count}x' if l.open_count else '')
                + (' | replied' if l.replied_at else '')
            )
        return '\n'.join(lines)
    except Exception as e:
        return f'Error fetching email history: {e}'


def _exec_get_cycle_detail(sim_id: str, cycle_id: str) -> str:
    try:
        from app.models.layer6 import Layer6Cycle
        cycle = None
        if cycle_id.isdigit():
            cycle = Layer6Cycle.query.filter_by(
                simulation_id=sim_id, cycle_number=int(cycle_id)).first()
        if not cycle:
            cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id, id=cycle_id).first()
        if not cycle:
            return f'Cycle "{cycle_id}" not found.'
        return (
            f'Cycle {cycle.cycle_number} | Phase: {cycle.phase}\n'
            f'Started: {cycle.cycle_started_at}\n'
            f'Reasoning: {cycle.orchestrator_reasoning or "N/A"}\n'
            f'Agents dispatched: {getattr(cycle, "actions_dispatched", "N/A")}\n'
        )
    except Exception as e:
        return f'Error fetching cycle: {e}'


def _exec_get_agent_status(sim_id: str, agent_name: str) -> str:
    try:
        from app.models.agent_action import AgentAction
        from app.models.bayesian import BayesianPosterior
        actions = AgentAction.query.filter_by(
            simulation_id=sim_id, action_type=agent_name
        ).order_by(AgentAction.created_at.desc()).all()
        if not actions:
            return f'Agent "{agent_name}" has not been dispatched in this simulation.'
        lines = [f'Agent: {agent_name}']
        for a in actions[:5]:
            lines.append(f'  Run: {a.status} | {a.created_at.strftime("%b %d")}')
        # Bayesian score
        score = BayesianPosterior.query.filter(
            BayesianPosterior.simulation_id == sim_id,
            BayesianPosterior.posterior_key.like(f'%{agent_name}%')
        ).first()
        if score:
            lines.append(f'Bayesian score: {float(score.value):.3f} (updates: {score.update_count})')
        return '\n'.join(lines)
    except Exception as e:
        return f'Error fetching agent status: {e}'


def _exec_get_pending_steps(sim_id: str) -> str:
    try:
        from app.models.action_step import ActionStep
        steps = ActionStep.query.filter_by(
            simulation_id=sim_id, status=ActionStep.STATUS_SCHEDULED
        ).order_by(ActionStep.scheduled_for).limit(20).all()
        if not steps:
            return 'No pending steps.'
        lines = []
        for s in steps:
            lines.append(
                f'{s.scheduled_for.strftime("%b %d")} | {s.action_type} | {s.step_type}'
                + (f' | cond: {s.condition_type}' if s.condition_type else '')
            )
        return '\n'.join(lines)
    except Exception as e:
        return f'Error fetching steps: {e}'


def _exec_get_income_detail(sim_id: str, layer: int = None) -> str:
    try:
        from app.models.income import LayerIncomeRecord
        q = LayerIncomeRecord.query.filter_by(simulation_id=sim_id, is_void=False)
        if layer:
            q = q.filter_by(layer_number=layer)
        records = q.order_by(LayerIncomeRecord.income_date.desc()).limit(30).all()
        if not records:
            return 'No income records found.'
        lines = []
        for r in records:
            lines.append(
                f'L{r.layer_number} | {r.income_date} | ${float(r.amount):,.2f}'
                + (f' | {r.description}' if r.description else '')
            )
        return '\n'.join(lines)
    except Exception as e:
        return f'Error fetching income: {e}'


def _exec_search_contacts(user_id: str, query: str, stage: str = None) -> str:
    try:
        from app.models.contact import Contact
        q = Contact.query.filter_by(user_id=user_id, is_archived=False)
        q = q.filter(
            (Contact.first_name.ilike(f'%{query}%')) |
            (Contact.last_name.ilike(f'%{query}%')) |
            (Contact.company_name.ilike(f'%{query}%'))
        )
        if stage:
            q = q.filter_by(pipeline_stage=stage)
        contacts = q.limit(10).all()
        if not contacts:
            return f'No contacts matching "{query}".'
        lines = [f'{c.display_name} | {c.company_name or "N/A"} | {c.pipeline_stage}' for c in contacts]
        return '\n'.join(lines)
    except Exception as e:
        return f'Error searching contacts: {e}'


def _exec_get_form_fields(current_tab: str, form_id: str = None) -> str:
    """Return available form fields for the current tab (static manifest)."""
    forms = {
        'l6_settings': {
            'cadence':            'select — daily, every_3_days, weekly',
            'actions_per_cycle':  'number — 1-10',
            'spend_ceiling':      'number — max spend per cycle in USD',
            'contact_scope':      'select — warm_only, warm_and_cold, all',
            'trust_level':        'select — full_auto, balanced, review_all',
        },
    }
    if form_id and form_id in forms:
        fields = forms[form_id]
        lines = [f'Form: {form_id}'] + [f'  {k}: {v}' for k, v in fields.items()]
        return '\n'.join(lines)
    # Return all forms for the tab
    lines = []
    for fid, fields in forms.items():
        lines.append(f'Form: {fid}')
        lines += [f'  {k}: {v}' for k, v in fields.items()]
    return '\n'.join(lines) if lines else 'No fillable forms on this tab.'


def execute_tool(tool_name: str, tool_input: dict, sim_id: str, user_id: str,
                 current_tab: str) -> str:
    """Execute a server-side read-only tool. Returns text result."""
    if tool_name == 'get_artifact':
        return _exec_get_artifact(sim_id, tool_input.get('action_type', ''))
    if tool_name == 'get_contact':
        return _exec_get_contact(user_id, tool_input.get('name_or_id', ''))
    if tool_name == 'get_email_history':
        return _exec_get_email_history(sim_id,
                                        tool_input.get('contact_id'),
                                        tool_input.get('cycle_id'))
    if tool_name == 'get_cycle_detail':
        return _exec_get_cycle_detail(sim_id, tool_input.get('cycle_id', ''))
    if tool_name == 'get_agent_status':
        return _exec_get_agent_status(sim_id, tool_input.get('agent_name', ''))
    if tool_name == 'get_pending_steps':
        return _exec_get_pending_steps(sim_id)
    if tool_name == 'get_income_detail':
        return _exec_get_income_detail(sim_id, tool_input.get('layer'))
    if tool_name == 'search_contacts':
        return _exec_search_contacts(user_id, tool_input.get('query', ''),
                                      tool_input.get('stage'))
    if tool_name == 'get_form_fields':
        return _exec_get_form_fields(current_tab, tool_input.get('form_id'))
    return f'Unknown tool: {tool_name}'


# ── Tab-aware opening message ──────────────────────────────────────────────────

def build_opening_message(ctx: dict, current_tab: str, user_first_name: str) -> str:
    """Generate a tab-aware opening message from simulation context."""
    name = user_first_name
    tab  = current_tab or 'journey'

    income       = ctx.get('income_by_layer', {})
    total_income = ctx.get('total_income', 0)
    cycle_num    = ctx.get('cycle_num', 0)
    phase        = ctx.get('phase', 'explore')
    cs           = ctx.get('contact_stages', {})
    steps_count  = ctx.get('pending_steps_count', 0)

    if tab == 'journey':
        return (
            f"Hi {name}. Your simulation is in {phase} phase, cycle {cycle_num}. "
            f"You have ${total_income:,.0f} confirmed income across all layers. "
            f"What would you like to know?"
        )
    if tab == 'cycle':
        return (
            f"Hi {name}. Cycle {cycle_num} is your most recent. "
            f"I have the full reasoning here. Want me to walk through the decisions?"
        )
    if tab == 'queue':
        urgent = steps_count
        return (
            f"Hi {name}. You have {urgent} upcoming action step{'s' if urgent != 1 else ''}. "
            f"Want me to brief you on any of them?"
        )
    if tab == 'income':
        l1 = income.get(1, 0)
        return (
            f"Hi {name}. Your confirmed income is ${total_income:,.0f}. "
            f"Layer 1 is your strongest at ${l1:,.0f}. "
            f"Want a full breakdown?"
        )
    if tab == 'network':
        return (
            f"Hi {name}. I can see the status of all agents in your simulation. "
            f"Want to know what's blocking a specific agent or see score trends?"
        )
    if tab == 'escalations':
        return (
            f"Hi {name}. Let me check on your escalations and integrations. "
            f"What would you like to review?"
        )
    if tab == 'momentum':
        return (
            f"Hi {name}. I can see your momentum metrics and Bayesian score trends. "
            f"What would you like to dig into?"
        )
    if tab == 'visuals':
        return (
            f"Hi {name}. I can walk you through your wealth pyramid and layer progress. "
            f"What would you like to understand?"
        )
    return (
        f"Hi {name}. I'm Simi, your simulation co-pilot. "
        f"You're in {phase} phase, cycle {cycle_num}. What can I help you with?"
    )


def get_suggestions(current_tab: str) -> list[str]:
    """Return 3-4 contextual suggested questions for the current tab."""
    return _TAB_SUGGESTIONS.get(current_tab or 'journey', _TAB_SUGGESTIONS['journey'])


# ── Main chat function ─────────────────────────────────────────────────────────

def simi_chat(sim_id: str, user_id: str, conv_id: str, user_message: str,
              current_tab: str, user_first_name: str) -> dict:
    """
    Send a message to Simi and return the response.

    Returns:
        {
            'content':     str,          # text response
            'ui_actions':  list[dict],   # navigate_to_tab / highlight_element / fill_form
            'model':       str,
            'tokens_used': int,
            'budget_warn': bool,         # True if >80% of 50k used
            'budget_stop': bool,         # True if >=50k used
        }
    """
    from app.extensions import db as _db
    from app.models.chat import SimiConversation, SimiMessage
    from utils.id_gen import generate_id

    # ── Budget check ───────────────────────────────────────────────────────────
    conv = SimiConversation.query.get(conv_id)
    if not conv:
        return {'content': 'Conversation not found.', 'ui_actions': [], 'model': '', 'tokens_used': 0,
                'budget_warn': False, 'budget_stop': False}

    if conv.total_tokens >= TOKEN_BUDGET_HARD:
        summary = _auto_summary(conv)
        return {
            'content': f'This conversation has reached its token budget. Here is a summary of what we covered: {summary} Click New Conversation to continue.',
            'ui_actions': [], 'model': '', 'tokens_used': 0,
            'budget_warn': False, 'budget_stop': True,
        }

    # ── Build context + system prompt ─────────────────────────────────────────
    ctx         = build_simi_context(sim_id, user_id)
    system_text = build_system_prompt(ctx, current_tab, user_first_name)

    # ── Load conversation history ──────────────────────────────────────────────
    history_msgs = []
    messages_q = SimiMessage.query.filter_by(conversation_id=conv_id).order_by(
        SimiMessage.created_at.asc()
    ).limit(30).all()
    for m in messages_q:
        if m.role in ('user', 'assistant') and m.content:
            history_msgs.append({'role': m.role, 'content': m.content})

    # ── Model routing ──────────────────────────────────────────────────────────
    model = route_model(user_message)

    # ── Agentic tool-use loop ──────────────────────────────────────────────────
    client   = _client()
    messages = [*history_msgs, {'role': 'user', 'content': user_message}]
    ui_actions: list[dict] = []
    total_tokens = 0
    final_text   = ''

    for _round in range(4):  # max 3 tool rounds + 1 final
        resp = client.messages.create(
            model=model,
            max_tokens=1000,
            system=[{'type': 'text', 'text': system_text,
                     'cache_control': {'type': 'ephemeral'}}],
            tools=SIMI_TOOLS,
            messages=messages,
        )
        total_tokens += (resp.usage.input_tokens or 0) + (resp.usage.output_tokens or 0)

        if resp.stop_reason == 'end_turn':
            # Extract text
            for block in resp.content:
                if hasattr(block, 'text'):
                    final_text += block.text
            break

        if resp.stop_reason == 'tool_use':
            tool_results = []
            for block in resp.content:
                if hasattr(block, 'text'):
                    final_text += block.text  # narration before tool call
                if block.type != 'tool_use':
                    continue
                tool_name  = block.name
                tool_input = block.input or {}

                if tool_name in _UI_TOOLS:
                    # Package as a UI action for the frontend
                    mapped_tab = _NAV_TAB_MAP.get(tool_input.get('tab_name', ''), tool_input.get('tab_name', ''))
                    action = {'tool': tool_name, **tool_input}
                    if tool_name == 'navigate_to_tab':
                        action['tab_name'] = mapped_tab
                    ui_actions.append(action)
                    # Return a canned result so Claude can continue
                    if tool_name == 'get_form_fields':
                        result_text = _exec_get_form_fields(current_tab, tool_input.get('form_id'))
                    else:
                        result_text = f'UI action {tool_name} dispatched to client.'
                else:
                    result_text = execute_tool(tool_name, tool_input, sim_id, user_id, current_tab)

                tool_results.append({
                    'type':        'tool_result',
                    'tool_use_id': block.id,
                    'content':     result_text,
                })

            # Append assistant turn + tool results
            messages = [*messages,
                        {'role': 'assistant', 'content': resp.content},
                        {'role': 'user',      'content': tool_results}]
        else:
            # Unexpected stop reason — collect any text and break
            for block in resp.content:
                if hasattr(block, 'text'):
                    final_text += block.text
            break

    if not final_text.strip():
        final_text = "I couldn't generate a response. Please try again."

    # ── Persist assistant message ──────────────────────────────────────────────
    try:
        msg = SimiMessage(
            id=generate_id(), conversation_id=conv_id,
            role='assistant', content=final_text,
            tokens_used=total_tokens, model=model,
        )
        if ui_actions:
            msg.tool_calls = ui_actions
        _db.session.add(msg)
        conv.total_tokens = (conv.total_tokens or 0) + total_tokens
        conv.last_message_at = datetime.utcnow()
        _db.session.commit()
    except Exception as e:
        logger.error('Failed to persist Simi message: %s', e)
        _db.session.rollback()

    # ── Budget status ──────────────────────────────────────────────────────────
    new_total    = (conv.total_tokens or 0)
    budget_warn  = new_total >= TOKEN_BUDGET_WARN
    budget_stop  = new_total >= TOKEN_BUDGET_HARD

    return {
        'content':     final_text,
        'ui_actions':  ui_actions,
        'model':       model,
        'tokens_used': total_tokens,
        'budget_warn': budget_warn,
        'budget_stop': budget_stop,
        'total_tokens': new_total,
    }


def _auto_summary(conv) -> str:
    """Generate a short summary of the conversation for the budget-stop message."""
    try:
        from app.models.chat import SimiMessage
        msgs = SimiMessage.query.filter_by(conversation_id=conv.id).order_by(
            SimiMessage.created_at.asc()
        ).limit(20).all()
        topics = []
        for m in msgs:
            if m.role == 'user' and m.content:
                topics.append(m.content[:60])
        if topics:
            return 'Topics covered: ' + '; '.join(topics[:5])
    except Exception:
        pass
    return 'Multiple topics were covered in this conversation.'
