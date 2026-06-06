"""
Action item creation helpers for the GCC Action Queue.

Call create_action_item() from any system event handler (agent completion,
webhook, orchestrator, etc.) to surface an item in the user's Action Queue.
"""
from datetime import datetime

# Maps every item_type to its urgency tier, action label, and dismissability.
# title/description are caller-supplied (already plain-English formatted).
ACTION_ITEM_TEMPLATES = {
    'agent_complete': {
        'urgency_tier': 3,
        'action_label': 'View artifact',
        'is_dismissable': True,
    },
    'agent_approval_required': {
        'urgency_tier': 2,
        'action_label': 'Review and approve',
        'is_dismissable': False,
    },
    'escalation_tool_missing': {
        'urgency_tier': 2,
        'action_label': 'Connect tool',
        'is_dismissable': False,
    },
    'escalation_tool_expired': {
        'urgency_tier': 1,
        'action_label': 'Re-connect',
        'is_dismissable': False,
    },
    'agent_failure': {
        'urgency_tier': 1,
        'action_label': 'Retry',
        'is_dismissable': False,
    },
    'webhook_reply': {
        'urgency_tier': 3,
        'action_label': 'View contact',
        'is_dismissable': True,
    },
    'webhook_booking': {
        'urgency_tier': 3,
        'action_label': 'View booking',
        'is_dismissable': True,
    },
    'webhook_signed': {
        'urgency_tier': 3,
        'action_label': 'View contact',
        'is_dismissable': True,
    },
    'webhook_payment': {
        'urgency_tier': 3,
        'action_label': 'View income',
        'is_dismissable': True,
    },
    'webhook_purchase': {
        'urgency_tier': 3,
        'action_label': 'View income',
        'is_dismissable': True,
    },
    'income_missing': {
        'urgency_tier': 2,
        'action_label': 'Log income',
        'is_dismissable': False,
    },
    'blog_review': {
        'urgency_tier': 2,
        'action_label': 'Review articles',
        'is_dismissable': False,
    },
    'blog_published': {
        'urgency_tier': 4,
        'action_label': 'View article',
        'is_dismissable': True,
    },
    'stale_artifact': {
        'urgency_tier': 4,
        'action_label': 'Review',
        'is_dismissable': True,
    },
    'cycle_ready': {
        'urgency_tier': 2,
        'action_label': 'Start cycle',
        'is_dismissable': False,
    },
    'first_cycle': {
        'urgency_tier': 2,
        'action_label': 'Start my first cycle',
        'is_dismissable': False,
    },
    'orchestrator_recommendation': {
        'urgency_tier': 4,
        'action_label': 'View recommendation',
        'is_dismissable': True,
    },
    'milestone': {
        'urgency_tier': 4,
        'action_label': 'Celebrate',
        'is_dismissable': True,
    },
    'waitlist_threshold': {
        'urgency_tier': 3,
        'action_label': 'Review launch plan',
        'is_dismissable': True,
    },
    'contact_promote': {
        'urgency_tier': 3,
        'action_label': 'Promote contacts',
        'is_dismissable': True,
    },
    'bio_chat_started': {
        'urgency_tier': 3,
        'action_label': 'View chat',
        'is_dismissable': True,
    },
    'bio_page_review': {
        'urgency_tier': 3,
        'action_label': 'Review and publish',
        'is_dismissable': True,
    },
    'social_post_approval': {
        'urgency_tier': 2,
        'action_label': 'Review post',
        'is_dismissable': False,
    },
    'proactive_alert': {
        'urgency_tier': 3,
        'action_label': 'Take action',
        'is_dismissable': True,
    },
    'layer_unlocked': {
        'urgency_tier': 4,
        'action_label': 'Explore layer',
        'is_dismissable': True,
    },
}

LAYER_NAMES = {
    1: 'Active income',
    2: 'Leveraged income',
    3: 'Productized income',
    4: 'Automated income',
    5: 'Wealth deployment',
}


def create_action_item(
    simulation_id,
    user_id,
    item_type,
    title,
    action_url,
    description=None,
    layer_number=None,
    source_action_id=None,
    source_artifact_id=None,
    source_contact_id=None,
    source_income_id=None,
    emit_sse=True,
):
    """
    Create an ActionItem record and optionally push an SSE event.

    Returns the created ActionItem instance.
    """
    from app.models.layer6 import ActionItem
    from app.extensions import db

    tmpl = ACTION_ITEM_TEMPLATES.get(item_type)
    if tmpl is None:
        raise ValueError(f'Unknown action item type: {item_type!r}')

    item = ActionItem(
        simulation_id=simulation_id,
        user_id=user_id,
        item_type=item_type,
        urgency_tier=tmpl['urgency_tier'],
        title=title,
        description=description[:500] if description else None,
        layer_number=layer_number,
        action_label=tmpl['action_label'],
        action_url=action_url,
        source_action_id=source_action_id,
        source_artifact_id=source_artifact_id,
        source_contact_id=source_contact_id,
        source_income_id=source_income_id,
        is_dismissable=tmpl['is_dismissable'],
    )
    db.session.add(item)
    db.session.commit()

    if emit_sse:
        _emit_sse_created(item)

    return item


def resolve_action_item(item_id, emit_sse=True):
    """Mark an action item resolved. Called after the user completes the required action."""
    from app.models.layer6 import ActionItem
    from app.extensions import db

    item = ActionItem.query.get(item_id)
    if item and item.status == ActionItem.STATUS_ACTIVE:
        item.status = ActionItem.STATUS_RESOLVED
        item.resolved_at = datetime.utcnow()
        db.session.commit()
        if emit_sse:
            _emit_sse_resolved(item.simulation_id, item_id)
    return item


def dismiss_action_item(item_id, emit_sse=True):
    """Mark a dismissable action item dismissed."""
    from app.models.layer6 import ActionItem
    from app.extensions import db

    item = ActionItem.query.get(item_id)
    if item and item.status == ActionItem.STATUS_ACTIVE and item.is_dismissable:
        item.status = ActionItem.STATUS_DISMISSED
        item.dismissed_at = datetime.utcnow()
        db.session.commit()
        if emit_sse:
            _emit_sse_resolved(item.simulation_id, item_id)
    return item


def _emit_sse_created(item):
    """Push action_item_created SSE event if the SSE module is available."""
    try:
        from app.blueprints.simulations.sse import push_sse_event
        push_sse_event(item.simulation_id, {
            'event_type': 'action_item_created',
            'payload': {
                'id': item.id,
                'item_type': item.item_type,
                'urgency_tier': item.urgency_tier,
                'title': item.title,
                'description': item.description,
                'layer_number': item.layer_number,
                'action_label': item.action_label,
                'action_url': item.action_url,
                'is_dismissable': item.is_dismissable,
                'created_at': item.created_at.isoformat(),
            },
        })
    except Exception:
        pass


def _emit_sse_resolved(simulation_id, item_id):
    """Push action_item_resolved SSE event."""
    try:
        from app.blueprints.simulations.sse import push_sse_event
        push_sse_event(simulation_id, {
            'event_type': 'action_item_resolved',
            'payload': {'id': item_id},
        })
    except Exception:
        pass
