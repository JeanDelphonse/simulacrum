"""
Outreach Campaign Engine — SIM-PRD-OUTREACH-001.

Powers the admin outreach system: a 3-email new-user drip on a weekly cadence and
one-time broadcasts to computed segments. Shares SendGrid + EmailSuppression with the
rest of the platform, but is separate from the contact-oriented outreach_email_service
(that one emails simulation CRM contacts; this one emails platform users).

Responsibilities:
  - config (platform_settings) with sensible defaults
  - default template copy + DB-backed editable templates
  - token rendering ({{first_name}}, {{bio_url}}, {{dashboard_url}}, {{slug}})
  - segment computation (new/existing/by-phase/custom)
  - enrollment + graduation + drip queue processing
  - broadcast send/schedule
  - CAN-SPAM compliance: one-click unsubscribe, physical address, suppression respect,
    one-email-per-user-per-24h frequency cap
"""
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BASE_URL = 'https://simulacrumai.io'
BUSINESS_NAME = 'Bay Area Experiences Ventures'
BUSINESS_ADDRESS = 'Santa Clara, CA'

# ── Config defaults ──────────────────────────────────────────────────────────

CONFIG_DEFAULTS = {
    'outreach_enabled': 'true',
    'outreach_initial_delay_hours': '12',
    'outreach_cadence_days': '7',
    'outreach_require_approval': 'false',
}


def get_config() -> dict:
    """Return current outreach config, merging platform_settings over defaults."""
    from app.models.platform_settings import PlatformSetting
    cfg = dict(CONFIG_DEFAULTS)
    for key in CONFIG_DEFAULTS:
        val = PlatformSetting.get(key)
        if val is not None:
            cfg[key] = val
    return {
        'enabled': str(cfg['outreach_enabled']).lower() == 'true',
        'initial_delay_hours': _safe_int(cfg['outreach_initial_delay_hours'], 12),
        'cadence_days': _safe_int(cfg['outreach_cadence_days'], 7),
        'require_approval': str(cfg['outreach_require_approval']).lower() == 'true',
    }


def _safe_int(val, default):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ── Default template copy (SIM-PRD-OUTREACH-001 §3) ──────────────────────────

DEFAULT_TEMPLATES = {
    'drip_1': {
        'name': 'Drip 1 — Expose Your Expertise',
        'subject': "Your bio page is live — here's how to put it to work",
        'preview_text': 'A few places to add your page so the right people find it.',
        'body': (
            "Hi {{first_name}},\n\n"
            "Your Simulacrum bio page is live at {{bio_url}}. It's ready to introduce your "
            "expertise, answer questions from prospects, and book calls on your behalf — but it "
            "can only do that when people can find it.\n\n"
            "Here are three places to add your link today:\n\n"
            "1. LinkedIn — add it to your Featured section, your About, and your contact info. "
            "This is where most professionals will look first.\n\n"
            "2. Your email signature — every email you send becomes an introduction to your work.\n\n"
            "3. Your resume header and any professional profiles — Twitter/X, industry "
            "directories, speaker pages.\n\n"
            "The more places your page appears, the more often your expertise gets discovered. "
            "It takes five minutes and works for you around the clock.\n\n"
            "Best regards,\nThe Simulacrum Team"
        ),
    },
    'drip_2': {
        'name': 'Drip 2 — Start Your Simulation (Layers 1 & 2)',
        'subject': 'Turn your expertise into income — start with Layers 1 and 2',
        'preview_text': 'The fastest path to your first client, built from your resume.',
        'body': (
            "Hi {{first_name}},\n\n"
            "Now that your bio page is working for you, the next step is your wealth "
            "simulation — where Simulacrum turns your resume into real income opportunities.\n\n"
            "The fastest results come from the first two layers:\n\n"
            "Layer 1 — Active Income. AI agents build your rate card, research prospects, and "
            "send personalized outreach, then generate proposals and agreements when someone "
            "responds. This is your most direct path to a paying client.\n\n"
            "Layer 2 — Leveraged Income. Agents design workshops, pitch speaking engagements, "
            "and structure group coaching — ways to earn more without trading more hours.\n\n"
            "To begin, create a simulation from your dashboard and select the agents that match "
            "your expertise. The system recommends the right mix for you automatically. Most "
            "users see their first opportunities within the first few cycles.\n\n"
            "Start your simulation: {{dashboard_url}}\n\n"
            "Best regards,\nThe Simulacrum Team"
        ),
    },
    'drip_3': {
        'name': 'Drip 3 — The Full System (Layers 3-5 + Orchestrator)',
        'subject': 'How Simulacrum builds income while you sleep',
        'preview_text': 'Layers 3 to 5, and the engine that runs them automatically.',
        'body': (
            "Hi {{first_name}},\n\n"
            "Layers 1 and 2 build income you actively deliver. The deeper layers build income "
            "that compounds on its own.\n\n"
            "Layer 3 — Productized Income. Your expertise becomes courses, ebooks, and "
            "memberships — built once, sold repeatedly.\n\n"
            "Layer 4 — Automated Income. SEO content, newsletters, and licensing create revenue "
            "streams that run without your daily involvement.\n\n"
            "Layer 5 — Wealth Deployment. The system helps you allocate what you earn — "
            "investment strategy, tax structure, and entity planning — so your income builds "
            "lasting wealth.\n\n"
            "Tying it all together is the orchestrator: a reasoning engine that runs every 24 "
            "hours. It reviews what's working, prioritizes the highest-value actions, and "
            "dispatches the right agents automatically. You review and approve; it does the work.\n\n"
            "When you're ready to build the full system, your dashboard is here: {{dashboard_url}}\n\n"
            "Best regards,\nThe Simulacrum Team"
        ),
    },
}

# Which drip template goes with which step number.
STEP_TEMPLATE_KEY = {1: 'drip_1', 2: 'drip_2', 3: 'drip_3'}


def seed_default_templates(force: bool = False):
    """Insert the 3 default drip templates into the DB if missing. Idempotent."""
    from app.extensions import db
    from app.models.outreach_campaign import OutreachTemplate
    changed = False
    for key, tpl in DEFAULT_TEMPLATES.items():
        existing = OutreachTemplate.query.filter_by(template_key=key).first()
        if existing and not force:
            continue
        if existing:
            existing.name = tpl['name']
            existing.subject = tpl['subject']
            existing.preview_text = tpl['preview_text']
            existing.body = tpl['body']
        else:
            db.session.add(OutreachTemplate(
                template_key=key, name=tpl['name'], subject=tpl['subject'],
                preview_text=tpl['preview_text'], body=tpl['body'], is_drip=True,
            ))
        changed = True
    if changed:
        db.session.commit()


def get_template(template_key: str):
    """Return the effective template dict for a key: DB row if present, else default.

    Returns a dict with subject/preview_text/body/name, or None if unknown.
    """
    from app.models.outreach_campaign import OutreachTemplate
    row = OutreachTemplate.query.filter_by(template_key=template_key).first()
    if row:
        return {
            'template_key': row.template_key, 'name': row.name,
            'subject': row.subject, 'preview_text': row.preview_text,
            'body': row.body, 'is_drip': row.is_drip,
        }
    default = DEFAULT_TEMPLATES.get(template_key)
    if default:
        return {
            'template_key': template_key, 'name': default['name'],
            'subject': default['subject'], 'preview_text': default['preview_text'],
            'body': default['body'], 'is_drip': True,
        }
    return None


# ── Token rendering ──────────────────────────────────────────────────────────

def build_tokens(user) -> dict:
    """Personalization tokens for a real user."""
    from app.models.bio_page import BioPage
    first = (user.full_name or '').strip().split()[0] if user.full_name else 'there'
    bp = BioPage.query.filter_by(user_id=user.id).first()
    slug = bp.slug if bp else ''
    bio_url = f'{BASE_URL}/u/{slug}' if slug else f'{BASE_URL}'
    return {
        'first_name': first or 'there',
        'bio_url': bio_url,
        'slug': slug,
        'dashboard_url': f'{BASE_URL}/dashboard',
    }


def sample_tokens() -> dict:
    """Tokens for previews / test sends."""
    return {
        'first_name': 'Alex',
        'bio_url': f'{BASE_URL}/u/alex-rivera',
        'slug': 'alex-rivera',
        'dashboard_url': f'{BASE_URL}/dashboard',
    }


def render_tokens(text: str, tokens: dict) -> str:
    """Replace {{token}} placeholders. Unknown tokens are left intact."""
    if not text:
        return ''
    out = text
    for key, val in tokens.items():
        out = out.replace('{{' + key + '}}', str(val if val is not None else ''))
    return out


# ── HTML wrapper with CAN-SPAM footer ────────────────────────────────────────

def _paragraphs_to_html(body_text: str) -> str:
    """Turn a plain-text body (blank-line separated) into styled HTML paragraphs."""
    import html as _html
    blocks = [b.strip() for b in (body_text or '').split('\n\n') if b.strip()]
    html_parts = []
    for block in blocks:
        safe = _html.escape(block).replace('\n', '<br>')
        html_parts.append(
            f'<p style="font-size:15px;color:#374151;line-height:1.65;margin:0 0 16px;">{safe}</p>'
        )
    return ''.join(html_parts)


def render_email_html(body_text: str, unsubscribe_url: str, preheader: str = '') -> str:
    """Wrap the outreach body in the branded HTML shell + CAN-SPAM footer."""
    pre = (f'<span style="display:none;max-height:0;overflow:hidden;mso-hide:all;">'
           f'{preheader}&nbsp;</span>') if preheader else ''
    body_html = _paragraphs_to_html(body_text)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>SimulacrumAI.io</title>
</head>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:'Helvetica Neue',Arial,sans-serif;">
{pre}
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f6f8;padding:40px 16px;">
  <tr><td align="center">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;">
      <tr><td align="center" style="padding-bottom:24px;">
        <a href="{BASE_URL}" style="text-decoration:none;display:inline-flex;align-items:center;gap:8px;">
          <span style="display:inline-block;width:32px;height:32px;background:#14b8a6;border-radius:7px;text-align:center;line-height:32px;font-size:16px;color:#fff;">&#10022;</span>
          <span style="font-size:15px;font-weight:700;color:#111827;letter-spacing:-0.2px;">SimulacrumAI.io</span>
        </a>
      </td></tr>
      <tr><td style="background:#ffffff;border-radius:12px;padding:40px 40px 32px;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
        {body_html}
      </td></tr>
      <tr><td align="center" style="padding-top:24px;font-size:12px;color:#9ca3af;line-height:1.6;">
        SimulacrumAI.io &middot; {BUSINESS_NAME}<br>
        {BUSINESS_ADDRESS}<br>
        <a href="{unsubscribe_url}" style="color:#9ca3af;text-decoration:underline;">Unsubscribe</a>
        from Simulacrum outreach emails.
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


# ── Unsubscribe token ────────────────────────────────────────────────────────

def _serializer():
    from itsdangerous import URLSafeSerializer
    from flask import current_app
    return URLSafeSerializer(current_app.config['SECRET_KEY'], salt='outreach-unsub')


def make_unsubscribe_token(email: str) -> str:
    return _serializer().dumps({'e': (email or '').lower().strip()})


def parse_unsubscribe_token(token: str):
    from itsdangerous import BadSignature
    try:
        data = _serializer().loads(token)
        return (data or {}).get('e')
    except (BadSignature, Exception):
        return None


def unsubscribe_url_for(email: str) -> str:
    from flask import url_for
    token = make_unsubscribe_token(email)
    try:
        return url_for('pages.outreach_unsubscribe', token=token, _external=True)
    except Exception:
        return f'{BASE_URL}/outreach/unsubscribe/{token}'


# ── Segments ─────────────────────────────────────────────────────────────────

def _new_user_ids() -> set:
    """Users with a published bio page AND no simulation."""
    from app.models.bio_page import BioPage
    from app.models.simulation import Simulation
    published = {bp.user_id for bp in BioPage.query.filter_by(
        status=BioPage.STATUS_PUBLISHED).all()}
    with_sim = {row[0] for row in db_session().query(Simulation.user_id).distinct().all()}
    return published - with_sim


def _existing_user_ids(phase: str = None) -> set:
    """Users with >=1 simulation, optionally filtered by lifecycle phase."""
    from app.models.simulation import Simulation
    q = db_session().query(Simulation.user_id)
    if phase in (Simulation.LIFECYCLE_ACTIVE, Simulation.LIFECYCLE_MAINTENANCE,
                 Simulation.LIFECYCLE_DORMANT):
        q = q.filter(Simulation.lifecycle_phase == phase)
    return {row[0] for row in q.distinct().all()}


def db_session():
    from app.extensions import db
    return db.session


def user_has_simulation(user_id: str) -> bool:
    from app.models.simulation import Simulation
    return db_session().query(
        db_session().query(Simulation.id).filter_by(user_id=user_id).exists()
    ).scalar()


def resolve_segment(segment: str, phase: str = None, signup_from=None,
                    signup_to=None) -> list:
    """Return a list of eligible (not suppressed / not opted-out) User rows for a segment.

    segment: 'new' | 'existing' | 'existing_phase' | 'all'
    """
    from app.models.user import User
    from app.models.outreach_email import EmailSuppression

    if segment == 'new':
        ids = _new_user_ids()
    elif segment == 'existing':
        ids = _existing_user_ids()
    elif segment == 'existing_phase':
        ids = _existing_user_ids(phase=phase)
    elif segment == 'all':
        ids = None
    else:
        ids = set()

    q = User.query.filter(User.deleted_at.is_(None))
    if ids is not None:
        if not ids:
            return []
        q = q.filter(User.id.in_(ids))
    if signup_from:
        q = q.filter(User.created_at >= signup_from)
    if signup_to:
        q = q.filter(User.created_at <= signup_to)

    users = q.all()
    # Drop suppressed / opted-out addresses and users without email.
    suppressed = {s.email for s in EmailSuppression.query.all()}
    return [u for u in users
            if u.email and u.email.lower().strip() not in suppressed]


def segment_counts() -> dict:
    """Live recipient counts for the admin overview."""
    from app.models.simulation import Simulation
    return {
        'new': len(resolve_segment('new')),
        'existing': len(resolve_segment('existing')),
        'existing_active': len(resolve_segment('existing_phase',
                                               phase=Simulation.LIFECYCLE_ACTIVE)),
        'existing_maintenance': len(resolve_segment('existing_phase',
                                                    phase=Simulation.LIFECYCLE_MAINTENANCE)),
        'existing_dormant': len(resolve_segment('existing_phase',
                                                phase=Simulation.LIFECYCLE_DORMANT)),
    }


# ── Frequency cap & suppression ──────────────────────────────────────────────

def _recently_sent(user_id: str, within_hours: int = 24) -> bool:
    """True if the user received any outreach email within the window."""
    from app.models.outreach_campaign import OutreachSend
    cutoff = datetime.utcnow() - timedelta(hours=within_hours)
    return db_session().query(
        db_session().query(OutreachSend.id).filter(
            OutreachSend.user_id == user_id,
            OutreachSend.status == OutreachSend.STATUS_SENT,
            OutreachSend.sent_at >= cutoff,
        ).exists()
    ).scalar()


def is_suppressed(email: str) -> bool:
    from app.models.outreach_email import EmailSuppression
    return EmailSuppression.is_suppressed(email)


# ── Enrollment & graduation ──────────────────────────────────────────────────

def enroll_new_user(user):
    """Enroll a user in the new-user drip when they publish a bio page (no simulation).

    Idempotent: existing active/graduated/completed enrollments are left alone.
    Creates the first queued send (step 1) scheduled at now + initial_delay_hours.
    """
    from app.extensions import db
    from app.models.outreach_campaign import OutreachEnrollment

    cfg = get_config()
    if not cfg['enabled']:
        return None
    if user_has_simulation(user.id):
        return None

    existing = OutreachEnrollment.query.filter_by(
        user_id=user.id, sequence='new_user_drip').first()
    if existing:
        # Re-activate only if previously removed/paused with no progress and no sim.
        return existing

    enrollment = OutreachEnrollment(
        user_id=user.id,
        sequence='new_user_drip',
        current_step=0,
        next_send_at=datetime.utcnow() + timedelta(hours=cfg['initial_delay_hours']),
        status=OutreachEnrollment.STATUS_ACTIVE,
    )
    db.session.add(enrollment)
    db.session.flush()
    _materialize_next_send(enrollment, step=1, cfg=cfg)
    db.session.commit()
    logger.info('Enrolled user %s in new_user_drip', user.id)
    return enrollment


def graduate_user(user_id: str):
    """Exit a user from the drip when they create a simulation. Cancels pending sends."""
    from app.extensions import db
    from app.models.outreach_campaign import OutreachEnrollment, OutreachSend

    enrollment = OutreachEnrollment.query.filter_by(
        user_id=user_id, sequence='new_user_drip',
        status=OutreachEnrollment.STATUS_ACTIVE).first()
    if not enrollment:
        return
    enrollment.status = OutreachEnrollment.STATUS_GRADUATED
    OutreachSend.query.filter(
        OutreachSend.enrollment_id == enrollment.id,
        OutreachSend.status.in_(OutreachSend.PENDING_STATUSES),
    ).update({OutreachSend.status: OutreachSend.STATUS_SKIPPED},
             synchronize_session=False)
    db.session.commit()
    logger.info('Graduated user %s from new_user_drip', user_id)


def _materialize_next_send(enrollment, step: int, cfg: dict):
    """Create the queued OutreachSend row for the given drip step (pre-rendered)."""
    from app.extensions import db
    from app.models.user import User
    from app.models.outreach_campaign import OutreachSend

    user = User.query.get(enrollment.user_id)
    if not user or not user.email:
        return None
    tkey = STEP_TEMPLATE_KEY[step]
    tpl = get_template(tkey)
    tokens = build_tokens(user)
    subject = render_tokens(tpl['subject'], tokens)
    body = render_tokens(tpl['body'], tokens)

    status = (OutreachSend.STATUS_AWAITING_APPROVAL
              if cfg['require_approval'] else OutreachSend.STATUS_QUEUED)

    send = OutreachSend(
        user_id=user.id,
        enrollment_id=enrollment.id,
        kind=OutreachSend.KIND_DRIP,
        template_key=tkey,
        step_number=step,
        subject=subject,
        preview_text=render_tokens(tpl.get('preview_text') or '', tokens),
        body_snapshot=body,
        to_email=user.email.lower().strip(),
        status=status,
        scheduled_at=enrollment.next_send_at,
    )
    db.session.add(send)
    db.session.flush()
    return send


def _ensure_pending_sends(cfg: dict) -> int:
    """Re-materialize the next drip send for active enrollments missing it.

    Detection is keyed on the existence of ANY OutreachSend row for the enrollment's
    next step — so 'skipped' and 'paused' rows (which still exist) are preserved and
    are NOT recreated. Only a physically deleted row triggers re-materialization,
    which is exactly the semantics of "delete a queued send so it re-materializes".
    """
    from app.models.outreach_campaign import OutreachEnrollment, OutreachSend
    active = OutreachEnrollment.query.filter_by(
        status=OutreachEnrollment.STATUS_ACTIVE).all()
    created = 0
    for e in active:
        next_step = (e.current_step or 0) + 1
        if next_step > 3:
            continue
        exists = db_session().query(
            db_session().query(OutreachSend.id).filter(
                OutreachSend.enrollment_id == e.id,
                OutreachSend.step_number == next_step,
            ).exists()
        ).scalar()
        if exists:
            continue
        if e.next_send_at is None:
            e.next_send_at = datetime.utcnow()
        if _materialize_next_send(e, step=next_step, cfg=cfg):
            created += 1
    if created:
        db_session().commit()
    return created


# ── Sending ──────────────────────────────────────────────────────────────────

def _dispatch_send(send) -> dict:
    """Actually deliver an OutreachSend via SendGrid. Updates status + timestamps.

    Enforces suppression + 24h frequency cap. Returns {'status': ...}.
    """
    from flask import current_app
    from app.extensions import db

    from app.models.outreach_campaign import OutreachSend

    to_email = (send.to_email or '').lower().strip()
    if not to_email:
        send.status = OutreachSend.STATUS_SKIPPED
        db.session.commit()
        return {'status': 'skipped', 'reason': 'no_email'}

    if is_suppressed(to_email):
        send.status = OutreachSend.STATUS_SUPPRESSED
        db.session.commit()
        return {'status': 'suppressed'}

    if _recently_sent(send.user_id, within_hours=24):
        # Frequency cap (max one outreach email per user per 24h across all sources) —
        # reschedule 24h out rather than dropping. Re-queue into the status the
        # relevant scheduler picks up: drips via process_drip_queue (queued),
        # broadcasts via process_scheduled_broadcasts (scheduled).
        send.scheduled_at = datetime.utcnow() + timedelta(hours=24)
        send.status = (OutreachSend.STATUS_SCHEDULED
                       if send.kind == OutreachSend.KIND_BROADCAST
                       else OutreachSend.STATUS_QUEUED)
        db.session.commit()
        return {'status': 'deferred', 'reason': 'frequency_cap'}

    api_key = current_app.config.get('SENDGRID_API_KEY')
    sender_email = current_app.config.get('MAIL_DEFAULT_SENDER', 'simi@simulacrumai.io')
    sender_name = current_app.config.get('MAIL_DEFAULT_SENDER_NAME', 'SimulacrumAI.io')

    unsub_url = unsubscribe_url_for(to_email)
    html = render_email_html(send.body_snapshot, unsub_url, preheader=send.preview_text or '')

    if not api_key:
        logger.warning('SENDGRID_API_KEY not set — outreach send %s skipped', send.id)
        send.status = OutreachSend.STATUS_FAILED
        db.session.commit()
        return {'status': 'error', 'reason': 'no_api_key'}

    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import (
            Mail, From, TrackingSettings, OpenTracking, ClickTracking,
            SubscriptionTracking, CustomArg,
        )
        message = Mail(
            from_email=From(sender_email, sender_name),
            to_emails=to_email,
            subject=send.subject,
            html_content=html,
        )
        message.custom_arg = [
            CustomArg('outreach_send_id', send.id),
            CustomArg('outreach_user_id', send.user_id),
            CustomArg('outreach_kind', send.kind),
        ]
        message.tracking_settings = TrackingSettings(
            open_tracking=OpenTracking(enable=True),
            click_tracking=ClickTracking(enable=True, enable_text=True),
        )
        client = sg_module.SendGridAPIClient(api_key)
        response = client.send(message)
        send.provider_message_id = response.headers.get('X-Message-Id')
        send.status = OutreachSend.STATUS_SENT
        send.sent_at = datetime.utcnow()
        db.session.commit()
        logger.info('Outreach %s sent to %s (kind=%s)', send.id, to_email, send.kind)
        return {'status': 'sent', 'message_id': send.provider_message_id}
    except Exception as exc:
        logger.error('Outreach send %s failed: %s', send.id, exc, exc_info=True)
        try:
            db.session.rollback()
            send.status = OutreachSend.STATUS_FAILED
            db.session.commit()
        except Exception:
            db.session.rollback()
        return {'status': 'error', 'reason': str(exc)}


def send_now(send) -> dict:
    """Dispatch a queued/paused/awaiting send immediately (admin action)."""
    return _dispatch_send(send)


# ── Drip queue processing (scheduled hourly) ─────────────────────────────────

def process_drip_queue() -> dict:
    """Send all due drip emails and advance enrollments. Called on a schedule."""
    from app.extensions import db
    from app.models.outreach_campaign import OutreachEnrollment, OutreachSend

    cfg = get_config()
    if not cfg['enabled']:
        return {'processed': 0, 'skipped': 'disabled'}

    now = datetime.utcnow()
    sent_count = 0
    graduated = 0

    # 1. Graduate any active enrollments whose user now has a simulation.
    active = OutreachEnrollment.query.filter_by(
        status=OutreachEnrollment.STATUS_ACTIVE).all()
    for e in active:
        if user_has_simulation(e.user_id):
            graduate_user(e.user_id)
            graduated += 1

    # 1b. Self-heal: re-materialize the next drip send for any active enrollment
    #     whose current pending step has no OutreachSend row (e.g. an admin deleted
    #     the queued row). Skipped/paused rows still exist, so those are preserved.
    _ensure_pending_sends(cfg)

    # 2. Find due queued sends (approval-pending and paused rows are NOT auto-sent).
    due = OutreachSend.query.filter(
        OutreachSend.kind == OutreachSend.KIND_DRIP,
        OutreachSend.status == OutreachSend.STATUS_QUEUED,
        OutreachSend.scheduled_at <= now,
    ).all()

    for send in due:
        enrollment = OutreachEnrollment.query.get(send.enrollment_id) if send.enrollment_id else None
        if not enrollment or enrollment.status != OutreachEnrollment.STATUS_ACTIVE:
            send.status = OutreachSend.STATUS_SKIPPED
            db.session.commit()
            continue
        # Last-chance graduation check.
        if user_has_simulation(send.user_id):
            graduate_user(send.user_id)
            graduated += 1
            continue

        result = _dispatch_send(send)
        if result.get('status') != 'sent':
            # deferred/suppressed/skipped — leave enrollment as-is for next tick.
            continue

        sent_count += 1
        step = send.step_number or (enrollment.current_step + 1)
        enrollment.current_step = step
        if step >= 3:
            enrollment.status = OutreachEnrollment.STATUS_COMPLETED
            enrollment.next_send_at = None
        else:
            enrollment.next_send_at = datetime.utcnow() + timedelta(days=cfg['cadence_days'])
            _materialize_next_send(enrollment, step=step + 1, cfg=cfg)
        db.session.commit()

    return {'processed': sent_count, 'graduated': graduated, 'due': len(due)}


def process_scheduled_broadcasts() -> dict:
    """Dispatch broadcast sends whose scheduled time has arrived."""
    from app.models.outreach_campaign import OutreachSend
    now = datetime.utcnow()
    due = OutreachSend.query.filter(
        OutreachSend.kind == OutreachSend.KIND_BROADCAST,
        OutreachSend.status == OutreachSend.STATUS_SCHEDULED,
        OutreachSend.scheduled_at <= now,
    ).all()
    sent = 0
    for send in due:
        if _dispatch_send(send).get('status') == 'sent':
            sent += 1
    return {'processed': sent, 'due': len(due)}


# ── Broadcast ────────────────────────────────────────────────────────────────

def send_test_email(to_email: str, subject: str, body: str,
                    preview_text: str = '') -> dict:
    """Send a single test copy with sample tokens to the admin (not logged/capped)."""
    from flask import current_app
    tokens = sample_tokens()
    rendered_subject = render_tokens(subject, tokens)
    rendered_body = render_tokens(body, tokens)
    unsub_url = unsubscribe_url_for(to_email)
    html = render_email_html(rendered_body, unsub_url,
                             preheader=render_tokens(preview_text or '', tokens))

    api_key = current_app.config.get('SENDGRID_API_KEY')
    sender_email = current_app.config.get('MAIL_DEFAULT_SENDER', 'simi@simulacrumai.io')
    sender_name = current_app.config.get('MAIL_DEFAULT_SENDER_NAME', 'SimulacrumAI.io')
    if not api_key:
        return {'status': 'error', 'reason': 'SENDGRID_API_KEY not configured'}
    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import Mail, From
        message = Mail(
            from_email=From(sender_email, sender_name),
            to_emails=to_email,
            subject=f'[TEST] {rendered_subject}',
            html_content=html,
        )
        client = sg_module.SendGridAPIClient(api_key)
        resp = client.send(message)
        return {'status': 'sent', 'code': resp.status_code}
    except Exception as exc:
        logger.error('Broadcast test send failed: %s', exc, exc_info=True)
        return {'status': 'error', 'reason': str(exc)}


def create_broadcast(recipients: list, subject: str, body: str, preview_text: str,
                     template_key: str = 'broadcast', schedule_at=None,
                     dispatch: bool = True) -> dict:
    """Create OutreachSend rows for a broadcast segment and (optionally) dispatch now.

    If schedule_at is set, rows are created STATUS_SCHEDULED and dispatched later by
    the scheduler. Suppression + opt-out already filtered in resolve_segment.
    """
    from app.extensions import db
    from app.models.outreach_campaign import OutreachSend

    created = 0
    sent = 0
    skipped_cap = 0
    for user in recipients:
        to_email = (user.email or '').lower().strip()
        if not to_email or is_suppressed(to_email):
            continue
        tokens = build_tokens(user)
        send = OutreachSend(
            user_id=user.id,
            enrollment_id=None,
            kind=OutreachSend.KIND_BROADCAST,
            template_key=template_key,
            step_number=None,
            subject=render_tokens(subject, tokens),
            preview_text=render_tokens(preview_text or '', tokens),
            body_snapshot=render_tokens(body, tokens),
            to_email=to_email,
            scheduled_at=schedule_at,
            status=(OutreachSend.STATUS_SCHEDULED if schedule_at
                    else OutreachSend.STATUS_QUEUED),
        )
        db.session.add(send)
        db.session.flush()
        created += 1
        if dispatch and not schedule_at:
            result = _dispatch_send(send)
            if result.get('status') == 'sent':
                sent += 1
            elif result.get('reason') == 'frequency_cap':
                skipped_cap += 1
    db.session.commit()
    return {'created': created, 'sent': sent, 'deferred_frequency_cap': skipped_cap}
