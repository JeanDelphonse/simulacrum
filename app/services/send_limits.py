"""Send limits for commercial email (COMPLIANCE_PLAN C3).

Admin settings (platform_settings) with safe defaults; nothing is hardcoded at
the call sites. Enforcement lives in check_send() and the auto-pause in
review_campaign().
"""
from __future__ import annotations

DEFAULTS = {
    'send_cap_per_user_day': '50',          # commercial emails per member per UTC day
    'send_cap_per_campaign_day': '25',      # per agent action (campaign) per UTC day
    'warmup_schedule': '10,15,20,25,30,40,50',   # daily cap for days 1..7 of a new sender
    'pause_bounce_rate_pct': '5',           # 7-day bounce rate that pauses a campaign
    'pause_complaint_rate_pct': '0.3',      # 7-day spam-complaint rate that pauses it
    'pause_min_sample': '20',               # sends needed before the rates are judged
}


def parse(key: str, value: str):
    """Validate an Admin value; returns the parsed form."""
    v = (value or '').strip()
    if key == 'warmup_schedule':
        try:
            steps = [int(x) for x in v.split(',') if x.strip()]
        except ValueError:
            raise ValueError('Warm-up schedule must be whole numbers separated by commas.')
        if not steps or any(n < 1 for n in steps):
            raise ValueError('Warm-up schedule needs at least one positive number.')
        return steps
    try:
        n = float(v)
    except ValueError:
        raise ValueError('%s must be a number.' % key)
    if n < 0:
        raise ValueError('%s cannot be negative.' % key)
    return n


def setting(key: str):
    from app.models.platform_settings import PlatformSetting
    try:
        raw = PlatformSetting.get(key, DEFAULTS[key])
        return parse(key, raw if raw not in (None, '') else DEFAULTS[key])
    except Exception:
        return parse(key, DEFAULTS[key])


# ── Enforcement ─────────────────────────────────────────────────────────────
from datetime import datetime, timedelta  # noqa: E402


def _day_start(now=None):
    now = now or datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _sent_q(user_id):
    from app.models.outreach_email import EmailLog
    from app.models.simulation import Simulation
    return (EmailLog.query.join(Simulation, Simulation.id == EmailLog.simulation_id)
            .filter(Simulation.user_id == user_id, EmailLog.sent_at.isnot(None)))


def daily_cap(user_id: str, now=None) -> int:
    """Today's cap for this member: the Admin cap, lowered during warm-up."""
    from app.models.outreach_email import EmailLog
    cap = int(setting('send_cap_per_user_day'))
    first = _sent_q(user_id).order_by(EmailLog.sent_at.asc()).first()
    day = 0 if not first else (_day_start(now) - _day_start(first.sent_at)).days
    schedule = setting('warmup_schedule')
    if day < len(schedule):
        cap = min(cap, schedule[day])
    return cap


def is_paused(action_id) -> bool:
    if not action_id:
        return False
    from app.models.layer6 import ActionItem
    return ActionItem.query.filter_by(item_type='campaign_paused', source_action_id=action_id,
                                      status=ActionItem.STATUS_ACTIVE).first() is not None


def check_send(user_id: str, action_id, now=None):
    """'campaign_paused' | 'over_daily_cap' | 'over_campaign_cap' | None."""
    from app.models.outreach_email import EmailLog
    if is_paused(action_id):
        return 'campaign_paused'
    today = _day_start(now)
    if _sent_q(user_id).filter(EmailLog.sent_at >= today).count() >= daily_cap(user_id, now):
        return 'over_daily_cap'
    if action_id and EmailLog.query.filter(EmailLog.action_id == action_id, EmailLog.sent_at >= today).count() \
            >= int(setting('send_cap_per_campaign_day')):
        return 'over_campaign_cap'
    return None


def campaign_rates(action_id: str, now=None) -> dict:
    from app.models.outreach_email import EmailLog
    since = (now or datetime.utcnow()) - timedelta(days=7)
    logs = EmailLog.query.filter(EmailLog.action_id == action_id, EmailLog.sent_at >= since).all()
    sent = len(logs)
    bounced = sum(1 for log in logs if log.bounced_at)
    complaints = sum(1 for log in logs if log.status == 'complaint')
    return {'sent': sent, 'bounced': bounced, 'complaints': complaints,
            'bounce_rate': round(100.0 * bounced / sent, 1) if sent else 0.0,
            'complaint_rate': round(100.0 * complaints / sent, 2) if sent else 0.0}


def review_campaign(action_id) -> bool:
    """Pause a campaign whose 7-day bounce or complaint rate is above the Admin
    thresholds, and tell the member why. Returns True if it was paused now."""
    if not action_id or is_paused(action_id):
        return False
    from app.extensions import db
    from app.models.action_step import ActionStep
    from app.models.agent_action import AgentAction
    from app.models.audit_log import AuditLog
    from app.models.simulation import Simulation
    from app.services import legal_text
    r = campaign_rates(action_id)
    if r['sent'] < int(setting('pause_min_sample')):
        return False
    if r['bounce_rate'] <= setting('pause_bounce_rate_pct') and \
            r['complaint_rate'] <= setting('pause_complaint_rate_pct'):
        return False
    action = AgentAction.query.get(action_id)
    if not action:
        return False
    ActionStep.query.filter(ActionStep.agent_action_id == action_id,
                            ActionStep.status == ActionStep.STATUS_SCHEDULED).update(
        {'status': ActionStep.STATUS_SUSPENDED, 'skip_reason': 'Campaign paused: bounce/complaint rate'},
        synchronize_session=False)
    sim = Simulation.query.get(action.simulation_id)
    owner_id = sim.user_id if sim else None
    AuditLog.log('campaign_paused', user_id=owner_id, resource_id=action_id, metadata=r)
    db.session.commit()
    from utils.action_items import create_action_item
    create_action_item(simulation_id=action.simulation_id, user_id=owner_id or '',
                       item_type='campaign_paused', title='A campaign was paused',
                       action_url='/simulations/%s/gcc' % action.simulation_id,
                       description=legal_text.get('campaign_paused', bounce_rate=r['bounce_rate'],
                                                  complaint_rate=r['complaint_rate']),
                       source_action_id=action_id, emit_sse=False)
    return True


def resume_campaign(action_id: str, user_id: str) -> int:
    """The member resumes after checking their list. Returns steps resumed."""
    from app.extensions import db
    from app.models.action_step import ActionStep
    from app.models.layer6 import ActionItem
    from app.models.audit_log import AuditLog
    for item in ActionItem.query.filter_by(item_type='campaign_paused', source_action_id=action_id,
                                           status=ActionItem.STATUS_ACTIVE).all():
        item.status = ActionItem.STATUS_RESOLVED
        item.resolved_at = datetime.utcnow()
    n = ActionStep.query.filter(ActionStep.agent_action_id == action_id,
                                ActionStep.status == ActionStep.STATUS_SUSPENDED,
                                ActionStep.skip_reason.like('Campaign paused%')).update(
        {'status': ActionStep.STATUS_SCHEDULED, 'skip_reason': None}, synchronize_session=False)
    AuditLog.log('campaign_resumed', user_id=user_id, resource_id=action_id, metadata={'steps': n})
    db.session.commit()
    return n
