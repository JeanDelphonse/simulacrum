"""
SIM-PRD-CHAT-001 — Simulation Chat Co-pilot service.
Three-tier context architecture + Haiku intent classification + Sonnet streaming.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, date, timedelta

from flask import current_app

logger = logging.getLogger(__name__)

INTENT_CLASSIFIER_MODEL = 'claude-haiku-4-5-20251001'
CHAT_MODEL              = 'claude-sonnet-4-6'

SYSTEM_PROMPT_TEMPLATE = """\
You are the Simulacrum Simulation Co-pilot — a personal wealth strategy advisor \
embedded in the user's Growth Command Center.

You have full knowledge of the user's wealth simulation. Your role is to:
1. Answer questions about the simulation with precise, data-grounded responses
2. Provide strategic advice based on the simulation's actual performance data
3. Identify when the user wants to take an action (dispatch agents, log income, manage contacts)

=== SIMULATION CONTEXT ===
User: {user_display_name}
Expertise Zone: {expertise_zone}
Simulation: {simulation_name}
Phase: {current_phase} — Cycle {cycle_number}

Layer Progress:
{layer_progress_formatted}

Income Confirmed (all time):
{income_summary_formatted}

Contact Pipeline: {contact_summary}

Orchestrator last cycle reasoning:
{orchestrator_reasoning_latest}

Next scheduled cycle: {next_scheduled_cycle}
=== END SIMULATION CONTEXT ===

RESPONSE RULES:
- Be concise. This is a panel, not a document. 2-4 sentences for advice, 1-2 for info answers.
- Always ground advice in the simulation's actual data. Never give generic business advice.
- Format numbers as currency: $8,500 not 8500.
- When referencing layers, use their full names: 'Layer 1 — Active income'.
- Address the user by first name.
- Never discuss your own architecture, model type, or context window.
- If asked to take an action, describe what you'd do and indicate you can do it — do not actually execute.
"""

INTENT_PROMPT = """\
Classify the following user message for a wealth simulation co-pilot.

Return ONLY valid JSON (no prose, no markdown) with this exact structure:
{
  "intent": "action" | "query" | "advice" | "general",
  "action_type": null | "log_income" | "add_contact" | "advance_contact" | "dispatch_agent" | "pause_orchestrator" | "resume_orchestrator" | "promote_contacts",
  "parameters": {},
  "missing_required": [],
  "confidence": 0.0
}

For "log_income" extract: amount (number), layer_number (1-5), description (string), income_date (YYYY-MM-DD or null)
For "add_contact" extract: first_name, last_name, email, job_title, company_name (all strings, null if missing)
For "advance_contact" extract: contact_name (string), new_stage ("prospect"|"active"|"client"|"closed_lost")
For "dispatch_agent" extract: action_type_name (string matching layer agent action names)
For others: no required parameters

Required fields for "log_income": amount, layer_number
Required fields for "add_contact": first_name, last_name, email

User message: {message}
"""


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])


# ── Intent classification ──────────────────────────────────────────────────

def classify_intent(user_message: str) -> dict:
    """Call Haiku 4.5 to classify the user's message intent. Returns intent dict."""
    try:
        prompt = INTENT_PROMPT.format(message=user_message)
        resp = _client().messages.create(
            model=INTENT_CLASSIFIER_MODEL,
            max_tokens=256,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = resp.content[0].text.strip()
        # Strip any accidental markdown fences
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        logger.debug('Intent classification failed: %s', e)
        return {'intent': 'general', 'action_type': None, 'parameters': {}, 'missing_required': [], 'confidence': 0.0}


# ── Context building ───────────────────────────────────────────────────────

def build_tier1_context(sim_id: str, user_id: str) -> dict:
    """Build permanent Tier 1 context from DB. ~3-5k tokens. Cache-eligible."""
    from app.extensions import db as _db
    from app.models.simulation import Simulation
    from app.models.layer6 import Layer6Cycle, Layer6Config
    from app.models.agent_action import AgentAction
    from sqlalchemy import func

    sim = Simulation.query.get(sim_id)
    if not sim:
        return {}

    # Layer progress
    layer_progress = {}
    LAYER_TOTALS = {1: 11, 2: 8, 3: 10, 4: 10, 5: 10}
    for ln in range(1, 6):
        try:
            done = AgentAction.query.filter_by(
                simulation_id=sim_id, layer_number=ln, status='complete'
            ).count()
        except Exception:
            done = 0
        layer_progress[f'L{ln}'] = {'completed': done, 'total': LAYER_TOTALS[ln]}

    # Income summary by layer (table may not exist yet)
    income_by_layer = {}
    all_time_total = 0.0
    try:
        from app.models.income import LayerIncomeRecord
        income_rows = _db.session.query(
            LayerIncomeRecord.layer_number,
            func.sum(LayerIncomeRecord.amount).label('total')
        ).filter_by(simulation_id=sim_id, is_void=False).group_by(
            LayerIncomeRecord.layer_number
        ).all()
        income_by_layer = {r.layer_number: float(r.total or 0) for r in income_rows}
        all_time_total = sum(income_by_layer.values())
    except Exception:
        _db.session.rollback()

    # Contact pipeline (table may not exist yet)
    total_contacts = 0
    stage_counts = {s: 0 for s in ('prospect', 'active', 'client', 'closed_lost')}
    try:
        from app.models.contact import Contact
        total_contacts = Contact.query.filter_by(user_id=user_id, is_archived=False).count()
        for stage in stage_counts:
            stage_counts[stage] = Contact.query.filter_by(
                user_id=user_id, pipeline_stage=stage, is_archived=False
            ).count()
    except Exception:
        _db.session.rollback()

    # Latest cycle
    cycle = None
    config = None
    try:
        cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
            Layer6Cycle.cycle_number.desc()
        ).first()
        config = Layer6Config.query.filter_by(simulation_id=sim_id).first()
    except Exception:
        _db.session.rollback()

    next_cycle_text = 'Not scheduled'
    if config and cycle and cycle.cycle_started_at:
        cadence_days = {'daily': 1, 'every_3_days': 3, 'weekly': 7}
        days = cadence_days.get(config.cadence, 1)
        next_run = cycle.cycle_started_at + timedelta(days=days)
        if next_run > datetime.utcnow():
            next_cycle_text = next_run.strftime('%Y-%m-%d %H:%M UTC')

    return {
        'expertise_zone':               sim.expertise_zone or 'Not set',
        'simulation_name':              sim.name,
        'current_phase':                cycle.phase if cycle else 'explore',
        'cycle_number':                 cycle.cycle_number if cycle else 0,
        'layer_progress':               layer_progress,
        'income_by_layer':              income_by_layer,
        'all_time_total':               all_time_total,
        'contact_total':                total_contacts,
        'contact_stages':               stage_counts,
        'orchestrator_reasoning':       (cycle.orchestrator_reasoning or '')[:500] if cycle else '',
        'next_scheduled_cycle':         next_cycle_text,
    }


def build_tier2_context(sim_id: str, intent_category: str) -> str:
    """Build dynamic Tier 2 context based on intent. Returns formatted string."""
    try:
        if intent_category == 'income_query':
            return _income_context(sim_id)
        elif intent_category == 'contact_query':
            return _contact_context(sim_id)
        elif intent_category == 'agent_query':
            return _agent_context(sim_id)
        elif intent_category == 'strategy_query':
            return _strategy_context(sim_id)
        return ''
    except Exception as e:
        logger.debug('Tier2 context error: %s', e)
        return ''


def _income_context(sim_id: str) -> str:
    from app.models.income import LayerIncomeRecord
    cutoff = date.today() - timedelta(days=30)
    records = LayerIncomeRecord.query.filter(
        LayerIncomeRecord.simulation_id == sim_id,
        LayerIncomeRecord.is_void == False,
        LayerIncomeRecord.income_date >= cutoff,
    ).order_by(LayerIncomeRecord.income_date.desc()).limit(30).all()
    if not records:
        return '\nNo income records in the last 30 days.'
    lines = ['\nRecent income (last 30 days):']
    for r in records:
        lines.append(f'  L{r.layer_number} | {r.income_date} | ${float(r.amount):,.2f} | {r.description or ""}')
    return '\n'.join(lines)


def _contact_context(sim_id: str) -> str:
    from app.models.contact import Contact, ContactActivity
    from app.extensions import db as _db
    from app.models.simulation import Simulation
    sim = Simulation.query.get(sim_id)
    if not sim:
        return ''
    recent = ContactActivity.query.filter_by(
        simulation_id=sim_id
    ).order_by(ContactActivity.activity_date.desc()).limit(10).all()
    lines = ['\nRecent contact activity:']
    for a in recent:
        c = Contact.query.get(a.contact_id)
        name = c.display_name if c else 'Unknown'
        lines.append(f'  {a.activity_date.strftime("%Y-%m-%d")} | {name} | {a.activity_type}')
    return '\n'.join(lines) if len(lines) > 1 else ''


def _agent_context(sim_id: str) -> str:
    from app.models.layer6 import Layer6ActionQueue, Layer6Cycle
    cycle = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).first()
    if not cycle:
        return ''
    actions = Layer6ActionQueue.query.filter_by(cycle_id=cycle.id).limit(20).all()
    lines = [f'\nCurrent cycle {cycle.cycle_number} action queue:']
    for a in actions:
        lines.append(f'  [{a.status}] L{a.source_layer} {a.action_type}')
    return '\n'.join(lines)


def _strategy_context(sim_id: str) -> str:
    from app.models.layer6 import Layer6Cycle, Layer6Outcome
    cycles = Layer6Cycle.query.filter_by(simulation_id=sim_id).order_by(
        Layer6Cycle.cycle_number.desc()
    ).limit(3).all()
    lines = ['\nLast 3 cycle orchestrator reasoning:']
    for c in cycles:
        if c.orchestrator_reasoning:
            lines.append(f'  Cycle {c.cycle_number} ({c.phase}): {c.orchestrator_reasoning[:200]}')
    return '\n'.join(lines)


def _format_system_prompt(tier1: dict, user_display_name: str) -> str:
    lp = tier1.get('layer_progress', {})
    layer_names = {1: 'Active income', 2: 'Leveraged income', 3: 'Productized',
                   4: 'Automated', 5: 'Wealth platforms'}
    lp_lines = [f'  L{n} — {layer_names[n]}: {v.get("completed", 0)}/{v.get("total", "?")} actions'
                for n, v in [(1, lp.get('L1', {})), (2, lp.get('L2', {})),
                             (3, lp.get('L3', {})), (4, lp.get('L4', {})),
                             (5, lp.get('L5', {}))]]
    inc = tier1.get('income_by_layer', {})
    inc_lines = [f'  L{n}: ${inc.get(n, 0):,.2f}' for n in range(1, 6)]
    inc_lines.append(f'  Total: ${tier1.get("all_time_total", 0):,.2f}')

    cs = tier1.get('contact_stages', {})
    contact_summary = (
        f'{tier1.get("contact_total", 0)} contacts — '
        f'{cs.get("prospect", 0)} prospects, {cs.get("active", 0)} active, '
        f'{cs.get("client", 0)} clients'
    )

    return SYSTEM_PROMPT_TEMPLATE.format(
        user_display_name=user_display_name,
        expertise_zone=tier1.get('expertise_zone', 'Not set'),
        simulation_name=tier1.get('simulation_name', ''),
        current_phase=tier1.get('current_phase', 'explore'),
        cycle_number=tier1.get('cycle_number', 0),
        layer_progress_formatted='\n'.join(lp_lines),
        income_summary_formatted='\n'.join(inc_lines),
        contact_summary=contact_summary,
        orchestrator_reasoning_latest=tier1.get('orchestrator_reasoning', 'No data yet.'),
        next_scheduled_cycle=tier1.get('next_scheduled_cycle', 'Not scheduled'),
    )


def get_recent_history(sim_id: str, user_id: str, limit: int = 10,
                       session_id: str | None = None) -> list:
    """Return the last N messages as the Anthropic messages array format."""
    from app.models.chat import SimulationChatMessage
    q = SimulationChatMessage.query.filter_by(
        simulation_id=sim_id, user_id=user_id, is_archived=False,
    ).filter(SimulationChatMessage.role.in_(['user', 'assistant']))
    if session_id:
        q = q.filter_by(session_id=session_id)
    msgs = q.order_by(SimulationChatMessage.created_at.desc()).limit(limit).all()
    msgs = list(reversed(msgs))
    result = []
    for m in msgs:
        content = m.content
        if not content:
            continue
        result.append({'role': m.role, 'content': content})
    return result


# ── Streaming response ─────────────────────────────────────────────────────

def chat_response(sim_id: str, user_id: str, user_message: str, user_display_name: str,
                  session_id: str | None = None) -> dict:
    """Synchronous (non-streaming) chat. Returns a dict with 'type' and payload fields.

    Returns:
        {'type': 'text',        'content': '...', 'message_id': '...', 'session_id': '...'}
        {'type': 'action_card', 'content': '...', 'message_id': '...', 'action_type': '...', 'parameters': {...}, 'session_id': '...'}
        {'type': 'error',       'message': '...'}
    """
    from app.extensions import db as _db
    from app.models.chat import SimulationChatMessage
    from utils.id_gen import generate_id

    intent_data     = classify_intent(user_message)
    intent_category = intent_data.get('intent', 'general')
    action_type     = intent_data.get('action_type')
    parameters      = intent_data.get('parameters', {})
    missing         = intent_data.get('missing_required', [])
    confidence      = intent_data.get('confidence', 0.0)

    if intent_category == 'action' and confidence > 0.85 and action_type and not missing:
        card_text = _action_card_text(action_type, parameters)
        msg = SimulationChatMessage(
            id=generate_id(), session_id=session_id, simulation_id=sim_id, user_id=user_id,
            role='assistant', content=card_text,
            intent=intent_category, action_type=action_type,
            action_status=SimulationChatMessage.ACTION_PENDING,
            model_used=INTENT_CLASSIFIER_MODEL,
        )
        msg.action_params = parameters
        _db.session.add(msg)
        _db.session.commit()
        return {'type': 'action_card', 'content': card_text, 'session_id': session_id,
                'message_id': msg.id, 'action_type': action_type, 'parameters': parameters}

    tier1       = build_tier1_context(sim_id, user_id)
    tier2       = build_tier2_context(sim_id, _map_intent_to_tier2(intent_category, user_message))
    history     = get_recent_history(sim_id, user_id, limit=10, session_id=session_id)
    system_text = _format_system_prompt(tier1, user_display_name)
    if tier2:
        system_text += f'\n\n=== ADDITIONAL CONTEXT ===\n{tier2}\n'

    system_blocks = [{'type': 'text', 'text': system_text, 'cache_control': {'type': 'ephemeral'}}]

    client = _client()
    resp = client.messages.create(
        model=CHAT_MODEL,
        max_tokens=800,
        system=system_blocks,
        messages=[*history, {'role': 'user', 'content': user_message}],
    )
    assistant_text = resp.content[0].text if resp.content else ''
    tokens_in  = resp.usage.input_tokens
    tokens_out = resp.usage.output_tokens

    msg = SimulationChatMessage(
        id=generate_id(), session_id=session_id, simulation_id=sim_id, user_id=user_id,
        role='assistant', content=assistant_text,
        intent=intent_category, model_used=CHAT_MODEL,
        tokens_input=tokens_in, tokens_output=tokens_out,
    )
    _db.session.add(msg)
    _db.session.commit()
    return {'type': 'text', 'content': assistant_text, 'message_id': msg.id, 'session_id': session_id}


def stream_chat_response(sim_id: str, user_id: str, user_message: str, user_display_name: str):
    """Generator that yields SSE events for the chat response."""
    try:
        yield from _stream_chat_response_inner(sim_id, user_id, user_message, user_display_name)
    except Exception as e:
        logger.error('Unhandled chat stream error: %s', e, exc_info=True)
        yield f'data: {json.dumps({"type": "error", "message": str(e)})}\n\n'


def _stream_chat_response_inner(sim_id: str, user_id: str, user_message: str, user_display_name: str):
    from app.extensions import db as _db
    from app.models.chat import SimulationChatMessage
    from utils.id_gen import generate_id

    # Step 1: Classify intent (Haiku — fast, cheap)
    intent_data = classify_intent(user_message)
    intent_category = intent_data.get('intent', 'general')
    action_type = intent_data.get('action_type')
    parameters = intent_data.get('parameters', {})
    missing = intent_data.get('missing_required', [])
    confidence = intent_data.get('confidence', 0.0)

    # Step 2: If high-confidence action intent, yield action card
    if intent_category == 'action' and confidence > 0.85 and action_type and not missing:
        # Save assistant action message
        msg = SimulationChatMessage(
            id=generate_id(), simulation_id=sim_id, user_id=user_id,
            role='assistant', content=_action_card_text(action_type, parameters),
            intent=intent_category, action_type=action_type,
            action_status=SimulationChatMessage.ACTION_PENDING,
            model_used=INTENT_CLASSIFIER_MODEL,
        )
        msg.action_params = parameters
        _db.session.add(msg)
        _db.session.commit()
        yield f'data: {json.dumps({"type": "action_card", "message_id": msg.id, "action_type": action_type, "parameters": parameters})}\n\n'
        yield f'data: {json.dumps({"type": "done", "message_id": msg.id})}\n\n'
        return

    # If action intent but low confidence or missing params, fall through to conversational
    # with action awareness

    # Step 3: Build context
    tier1 = build_tier1_context(sim_id, user_id)
    tier2 = build_tier2_context(sim_id, _map_intent_to_tier2(intent_category, user_message))
    history = get_recent_history(sim_id, user_id, limit=10)
    system_text = _format_system_prompt(tier1, user_display_name)
    if tier2:
        system_text += f'\n\n=== ADDITIONAL CONTEXT ===\n{tier2}\n'

    system_blocks = [
        {
            'type': 'text',
            'text': system_text,
            'cache_control': {'type': 'ephemeral'},
        }
    ]

    # Step 4: Stream Sonnet response
    client = _client()
    assistant_text = ''
    tokens_in = tokens_out = 0

    try:
        with client.messages.stream(
            model=CHAT_MODEL,
            max_tokens=800,
            system=system_blocks,
            messages=[*history, {'role': 'user', 'content': user_message}],
        ) as stream:
            for text in stream.text_stream:
                assistant_text += text
                yield f'data: {json.dumps({"type": "text", "delta": text})}\n\n'
            final = stream.get_final_message()
            tokens_in  = final.usage.input_tokens
            tokens_out = final.usage.output_tokens
    except Exception as e:
        logger.error('Chat stream error: %s', e)
        yield f'data: {json.dumps({"type": "error", "message": "Response failed. Please try again."})}\n\n'
        return

    # Step 5: Persist assistant message
    msg = SimulationChatMessage(
        id=generate_id(), simulation_id=sim_id, user_id=user_id,
        role='assistant', content=assistant_text,
        intent=intent_category, model_used=CHAT_MODEL,
        tokens_input=tokens_in, tokens_output=tokens_out,
    )
    _db.session.add(msg)
    _db.session.commit()

    yield f'data: {json.dumps({"type": "done", "message_id": msg.id})}\n\n'


def _map_intent_to_tier2(intent: str, message: str) -> str:
    """Map intent category to Tier 2 context type."""
    msg_lower = message.lower()
    if any(w in msg_lower for w in ('income', 'earned', 'revenue', 'money', 'paid', 'payment')):
        return 'income_query'
    if any(w in msg_lower for w in ('contact', 'replied', 'prospect', 'client', 'outreach')):
        return 'contact_query'
    if any(w in msg_lower for w in ('agent', 'action', 'run', 'campaign', 'dispatch', 'cycle')):
        return 'agent_query'
    if any(w in msg_lower for w in ('should', 'recommend', 'focus', 'next', 'priority', 'strategy')):
        return 'strategy_query'
    return 'general'


def _action_card_text(action_type: str, parameters: dict) -> str:
    readable = {
        'log_income':          'Log income entry',
        'add_contact':         'Add contact',
        'advance_contact':     'Update contact stage',
        'dispatch_agent':      'Dispatch agent action',
        'pause_orchestrator':  'Pause orchestrator',
        'resume_orchestrator': 'Resume orchestrator',
        'promote_contacts':    'Promote contacts',
    }
    return f'I can {readable.get(action_type, action_type).lower()} with these details. Please confirm:'


# ── Action execution ───────────────────────────────────────────────────────

def execute_chat_action(sim_id: str, user_id: str, message_id: str,
                        action_type: str, parameters: dict) -> dict:
    """Execute a confirmed action and return result dict."""
    from app.models.chat import SimulationChatMessage
    from app.extensions import db as _db

    msg = SimulationChatMessage.query.filter_by(id=message_id, simulation_id=sim_id).first()

    try:
        result = _dispatch_action(sim_id, user_id, action_type, parameters)
        if msg:
            msg.action_status = SimulationChatMessage.ACTION_EXECUTED
            msg.action_result = result
            _db.session.commit()
        return {'ok': True, 'result': result}
    except Exception as e:
        logger.error('Chat action execution failed: %s', e)
        if msg:
            msg.action_status = SimulationChatMessage.ACTION_EXECUTED
            msg.action_result = {'error': str(e)}
            _db.session.commit()
        return {'ok': False, 'error': str(e)}


def _dispatch_action(sim_id: str, user_id: str, action_type: str, parameters: dict) -> dict:
    from app.extensions import db as _db

    if action_type == 'log_income':
        from app.models.income import LayerIncomeRecord
        from app.blueprints.income.routes import _update_layer_outcome
        from utils.id_gen import generate_id as _gen
        from decimal import Decimal
        amount = Decimal(str(parameters.get('amount', 0)))
        layer  = int(parameters.get('layer_number', 1))
        desc   = parameters.get('description', '')
        raw_date = parameters.get('income_date') or parameters.get('date')
        try:
            income_date = date.fromisoformat(raw_date) if raw_date else date.today()
        except ValueError:
            income_date = date.today()
        rec = LayerIncomeRecord(
            id=_gen(), simulation_id=sim_id, layer_number=layer,
            amount=amount, description=desc, income_date=income_date,
            source='chat', recorded_by=user_id,
        )
        _db.session.add(rec)
        _db.session.flush()
        _update_layer_outcome(sim_id, layer, income_date.strftime('%Y-%m'))
        _db.session.commit()
        return {'created': rec.id, 'amount': float(amount), 'layer': layer}

    elif action_type == 'add_contact':
        from app.models.contact import Contact
        from utils.id_gen import generate_id as _gen
        email = (parameters.get('email') or '').strip().lower()
        if not email:
            raise ValueError('Email is required to add a contact')
        existing = Contact.query.filter_by(user_id=user_id, email=email).first()
        if existing:
            return {'exists': True, 'contact_id': existing.id}
        c = Contact(
            id=_gen(), user_id=user_id,
            first_name=parameters.get('first_name', ''),
            last_name=parameters.get('last_name', ''),
            email=email,
            job_title=parameters.get('job_title'),
            company_name=parameters.get('company_name'),
            source='chat',
            pipeline_stage='prospect',
        )
        _db.session.add(c)
        _db.session.commit()
        return {'created': c.id, 'name': c.display_name}

    elif action_type == 'advance_contact':
        from app.models.contact import Contact
        name = parameters.get('contact_name', '')
        stage = parameters.get('new_stage', 'active')
        # Find by name (best-effort)
        parts = name.split()
        q = Contact.query.filter_by(user_id=user_id, is_archived=False)
        if len(parts) >= 2:
            q = q.filter(Contact.first_name.ilike(parts[0]), Contact.last_name.ilike(parts[-1]))
        elif parts:
            q = q.filter(Contact.first_name.ilike(parts[0]))
        c = q.first()
        if not c:
            raise ValueError(f'Contact "{name}" not found')
        c.advance_stage(stage, created_by='chat')
        _db.session.commit()
        return {'updated': c.id, 'stage': stage}

    elif action_type == 'pause_orchestrator':
        from app.models.layer6 import Layer6Config
        cfg = Layer6Config.query.filter_by(simulation_id=sim_id).first()
        if cfg:
            cfg.is_active = False
            _db.session.commit()
        return {'paused': True}

    elif action_type == 'resume_orchestrator':
        from app.models.layer6 import Layer6Config
        cfg = Layer6Config.query.filter_by(simulation_id=sim_id).first()
        if cfg:
            cfg.is_active = True
            _db.session.commit()
        return {'resumed': True}

    raise ValueError(f'Unknown action type: {action_type}')
