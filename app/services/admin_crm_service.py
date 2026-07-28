"""SIM-PRD-CRM-001 — Admin Outreach Pipeline service.

The semi-automated loop: the system drafts and reminds, the founder sends and
confirms. Nothing here sends a LinkedIn touch on its own — automation stops at
drafting, because LinkedIn is relationship-first and automated sending risks both
the account and the relationship (PRD section 3).
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models.admin_prospect import AdminProspect, AdminProspectTouch, AdminStageRule

logger = logging.getLogger(__name__)

# Sales copy goes to real prospects, so the touch drafts use the Sonnet tier the
# rest of the app uses for user-facing text; the research prompt is mechanical
# enough for Haiku.
DRAFT_MODEL = 'claude-sonnet-4-6'
RESEARCH_MODEL = 'claude-haiku-4-5-20251001'

# Touch 1 is a LinkedIn connection request: hard platform limit, and no link.
TOUCH1_MAX_CHARS = 300

# Fallback stage rules. admin_stage_rules overrides these per stage; anything the
# table is missing falls back here so a partial seed cannot break the pipeline.
# followup_days = wait after logging a touch at this stage before it resurfaces.
# drafts_touch  = what the briefing writes for a prospect sitting here
#                 (None = waiting stage: remind, do not draft).
_DEFAULT_RULES = {
    AdminProspect.STAGE_NOT_STARTED:    {'followup_days': 0, 'drafts_touch': 'research'},
    AdminProspect.STAGE_RESEARCHED:     {'followup_days': 0, 'drafts_touch': 'touch1'},
    AdminProspect.STAGE_TOUCH_1_SENT:   {'followup_days': 4, 'drafts_touch': None},
    AdminProspect.STAGE_CONNECTED:      {'followup_days': 0, 'drafts_touch': 'touch2'},
    AdminProspect.STAGE_TOUCH_2_SENT:   {'followup_days': 3, 'drafts_touch': None},
    AdminProspect.STAGE_REPLIED:        {'followup_days': 3, 'drafts_touch': 'touch3'},
    AdminProspect.STAGE_MEETING_BOOKED: {'followup_days': 1, 'drafts_touch': None},
    AdminProspect.STAGE_ONBOARDED:      {'followup_days': 0, 'drafts_touch': None},
    AdminProspect.STAGE_PASSED:         {'followup_days': 0, 'drafts_touch': None},
}

# Where a prospect lands when the founder clicks 'Logged' (FR-CRM-04).
#
# Stages absent from this map hold their position and only get a new follow-up
# date. That is deliberate for 'replied': sending the Touch 3 pitch does not book
# a meeting, and 'meeting_booked' is an event only the founder can confirm — so
# logging the pitch reschedules the chase instead of inventing a booking. The
# waiting stages behave the same way, where a logged touch is an extra nudge.
_ADVANCE_ON_LOG = {
    AdminProspect.STAGE_NOT_STARTED: AdminProspect.STAGE_RESEARCHED,
    AdminProspect.STAGE_RESEARCHED:  AdminProspect.STAGE_TOUCH_1_SENT,
    AdminProspect.STAGE_CONNECTED:   AdminProspect.STAGE_TOUCH_2_SENT,
}


# ── Stage rules ───────────────────────────────────────────────────────────────

def stage_rules() -> dict:
    """Stage → rule dict, DB rows layered over the built-in defaults."""
    rules = {k: dict(v) for k, v in _DEFAULT_RULES.items()}
    try:
        for row in AdminStageRule.query.all():
            if row.stage in rules:
                rules[row.stage] = {
                    'followup_days': row.followup_days,
                    'drafts_touch': row.drafts_touch,
                }
    except Exception as exc:
        # Table missing (migration not run) — defaults keep the pipeline usable.
        logger.warning('admin_crm: stage rules unavailable, using defaults: %s', exc)
    return rules


def _rule_for(stage: str) -> dict:
    return stage_rules().get(stage, {'followup_days': 3, 'drafts_touch': None})


# ── Counters & queries ────────────────────────────────────────────────────────

def counters(today=None) -> dict:
    """Top-line pipeline metrics for the dashboard strip (FR-CRM-06)."""
    today = today or date.today()
    rows = AdminProspect.query.all()
    return {
        'total': len(rows),
        'active': sum(1 for p in rows if p.is_active),
        'replied': sum(1 for p in rows if p.stage == AdminProspect.STAGE_REPLIED),
        'due_today': sum(1 for p in rows if p.is_due(today)),
        'overdue': sum(1 for p in rows if p.is_overdue(today)),
        'onboarded': sum(1 for p in rows if p.stage == AdminProspect.STAGE_ONBOARDED),
        'passed': sum(1 for p in rows if p.stage == AdminProspect.STAGE_PASSED),
    }


def due_queue(today=None, draft: bool = False) -> list:
    """Prospects due a touch, soonest first (PRD build_outreach_queue).

    draft=False keeps this cheap for page loads — it returns whatever draft is
    already cached. The morning briefing passes draft=True to generate.
    """
    today = today or date.today()
    due = []
    candidates = (
        AdminProspect.query
        .filter(~AdminProspect.stage.in_(AdminProspect.TERMINAL_STAGES))
        .filter(AdminProspect.next_followup.isnot(None))
        .filter(AdminProspect.next_followup <= today)
        .order_by(AdminProspect.next_followup.asc())
        .all()
    )
    for p in candidates:
        text = draft_touch(p) if draft else _cached_draft(p)
        due.append({
            'prospect': p.to_dict(),
            'draft': text,
            'draft_kind': _rule_for(p.stage)['drafts_touch'],
            'overdue': p.next_followup < today,
        })
    return due


def pipeline_by_stage() -> dict:
    """Kanban payload: stage → prospects, in pipeline order (FR-CRM-06)."""
    board = {s: [] for s in AdminProspect.STAGES}
    for p in AdminProspect.query.order_by(AdminProspect.next_followup.asc().nullslast(),
                                          AdminProspect.firm_name.asc()).all():
        board.setdefault(p.stage, []).append(p.to_dict())
    return board


def search(q: str = '', fit: str = '', category: str = '', stage: str = '') -> list:
    """Filterable list view — search across firm, lead and category (FR-CRM-06)."""
    query = AdminProspect.query
    if q:
        like = '%{}%'.format(q.strip())
        query = query.filter(db.or_(
            AdminProspect.firm_name.ilike(like),
            AdminProspect.lead_name.ilike(like),
            AdminProspect.category.ilike(like),
        ))
    if fit:
        query = query.filter(AdminProspect.fit == fit)
    if category:
        query = query.filter(AdminProspect.category == category)
    if stage:
        query = query.filter(AdminProspect.stage == stage)
    rows = query.order_by(AdminProspect.created_at.desc()).all()
    return [p.to_dict() for p in rows]


def categories() -> list:
    rows = db.session.query(AdminProspect.category).distinct().all()
    return sorted({r[0] for r in rows if r[0]})


# ── Drafting ──────────────────────────────────────────────────────────────────

def _cached_draft(p) -> str:
    """Cached draft, but only if it was written for the stage the prospect is in."""
    if p.draft_text and p.draft_for_stage == p.stage:
        return p.draft_text
    return ''


def _enforce_touch1(text: str) -> str:
    """Touch 1 must fit LinkedIn's connection-request limit and carry no link.

    The model is told both constraints, but they are product rules from FR-CRM-03
    rather than suggestions, so they are enforced here too.
    """
    text = re.sub(r'https?://\S+', '', text or '')
    # Bare links too — models write "www.acme.com" and "acme.com/pricing" without a
    # scheme, and FR-CRM-03 says no link, not no https.
    text = re.sub(r'\bwww\.\S+', '', text)
    # Whole email addresses before bare domains, or stripping the domain would
    # leave a dangling "someone@".
    text = re.sub(r'\b[\w.+-]+@[\w-]+\.[\w.]+', '', text)
    text = re.sub(r'\b[\w-]+\.(?:com|io|net|org|co|ai|dev)\b(?:/\S*)?', '', text,
                  flags=re.IGNORECASE)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    if len(text) <= TOUCH1_MAX_CHARS:
        return text
    clipped = text[:TOUCH1_MAX_CHARS]
    # Prefer a sentence end, else a word boundary, so the request never ends mid-word.
    for sep in ('. ', '! ', '? '):
        idx = clipped.rfind(sep)
        if idx > TOUCH1_MAX_CHARS * 0.5:
            return clipped[:idx + 1].strip()
    idx = clipped.rfind(' ')
    return (clipped[:idx] if idx > 0 else clipped).strip()


def _prospect_brief(p) -> str:
    bits = ['Firm: {}'.format(p.firm_name)]
    if p.lead_name:
        bits.append('Lead: {}'.format(p.lead_name))
    if p.category:
        bits.append('Category: {}'.format(p.category))
    if p.website:
        bits.append('Website: {}'.format(p.website))
    if p.notes:
        bits.append('Notes: {}'.format(p.notes[:400]))
    return '\n'.join(bits)


_PROMPTS = {
    'research': (
        "You are prepping a sales research task for Simulacrum, an AI career-wealth "
        "simulation platform sold to consultancies as a corporate pilot.\n\n{brief}\n\n"
        "List 3-4 short bullet checks to confirm this firm is a fit: rough headcount, "
        "whether it is independent (not a big-4 subsidiary), and who the right "
        "decision-maker is. Plain text bullets, no preamble."
    ),
    'touch1': (
        "Write a LinkedIn connection request note to {lead} at {firm}.\n\n{brief}\n\n"
        "Hard rules: under {limit} characters. No pitch. No link. No mention of a "
        "product, demo, pilot or price. Its only job is to get the request accepted. "
        "Reference something specific and credible about their firm or focus. Warm, "
        "peer-to-peer, lowercase-friendly, no marketing voice. Return only the note."
    ),
    'touch2': (
        "Write a short LinkedIn DM to {lead} at {firm}, who just accepted a "
        "connection request.\n\n{brief}\n\n"
        "Hard rules: this is a genuine question, NOT a pitch. Do not mention "
        "Simulacrum, a product, a demo, a pilot or a price. Ask one specific question "
        "about how they handle career or compensation planning for their consultants. "
        "2-3 sentences. Return only the message."
    ),
    'touch3': (
        "Write a LinkedIn message to {lead} at {firm}, who replied to a question about "
        "career and compensation planning for their consultants.\n\n{brief}\n\n"
        "This is the earned pitch. Introduce Simulacrum as an AI career-wealth "
        "simulation platform, name one concrete benefit for a consultancy of their "
        "type, and propose a 20-minute call. Mention that a sell sheet and pilot "
        "offer are attached. 4-6 sentences, direct, no hype, no bullet lists. "
        "Return only the message."
    ),
}


def draft_touch(p, force: bool = False) -> str:
    """Stage-aware draft for one prospect (FR-CRM-03).

    Waiting stages return '' — they get a reminder, not a draft. Results are cached
    on the prospect so opening the tab does not re-bill a Claude call per row.
    """
    kind = _rule_for(p.stage)['drafts_touch']
    if not kind:
        return ''
    if not force:
        cached = _cached_draft(p)
        if cached:
            return cached

    try:
        import anthropic
        from flask import current_app
        client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
        prompt = _PROMPTS[kind].format(
            brief=_prospect_brief(p),
            firm=p.firm_name,
            lead=p.lead_name or 'the practice lead',
            limit=TOUCH1_MAX_CHARS,
        )
        resp = client.messages.create(
            model=RESEARCH_MODEL if kind == 'research' else DRAFT_MODEL,
            max_tokens=500,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = (resp.content[0].text or '').strip()
    except Exception as exc:
        logger.warning('admin_crm: draft failed for %s (%s): %s', p.id, kind, exc)
        return ''

    if kind == 'touch1':
        text = _enforce_touch1(text)

    p.draft_text = text
    p.draft_for_stage = p.stage
    p.draft_generated_at = datetime.utcnow()
    db.session.commit()
    return text


# ── Mutations ─────────────────────────────────────────────────────────────────

def _followup_from(stage: str, base=None) -> date:
    base = base or date.today()
    return base + timedelta(days=int(_rule_for(stage)['followup_days'] or 0))


def create_prospect(data: dict):
    p = AdminProspect(
        firm_name=(data.get('firm_name') or '').strip(),
        lead_name=(data.get('lead_name') or '').strip() or None,
        lead_linkedin=(data.get('lead_linkedin') or '').strip() or None,
        website=(data.get('website') or '').strip() or None,
        contact_path=(data.get('contact_path') or '').strip() or None,
        fit=data.get('fit') if data.get('fit') in AdminProspect.FITS else AdminProspect.FIT_MEDIUM,
        category=(data.get('category') or '').strip() or None,
        stage=data.get('stage') if data.get('stage') in AdminProspect.STAGES
              else AdminProspect.STAGE_NOT_STARTED,
        notes=(data.get('notes') or '').strip() or None,
    )
    if not p.firm_name:
        raise ValueError('firm_name is required')
    # New prospects are due immediately so they surface in the first briefing.
    p.next_followup = _followup_from(p.stage)
    db.session.add(p)
    db.session.commit()
    return p


def update_prospect(p, data: dict):
    for field in ('firm_name', 'lead_name', 'lead_linkedin', 'website',
                  'contact_path', 'category', 'notes'):
        if field in data:
            val = (data.get(field) or '').strip()
            setattr(p, field, val or (None if field != 'firm_name' else p.firm_name))
    if data.get('fit') in AdminProspect.FITS:
        p.fit = data['fit']
    if 'next_followup' in data:
        p.next_followup = _parse_date(data.get('next_followup'))
    db.session.commit()
    return p


def log_touch(p, channel: str = AdminProspectTouch.CHANNEL_LINKEDIN,
              summary: str = '', drafted_by: str = AdminProspectTouch.BY_MANUAL,
              advance: bool = True):
    """Record a touch the founder actually sent (FR-CRM-04).

    Appends to the touch log, stamps last_contact, advances the stage where the
    pipeline defines a next step, and sets the next follow-up from the stage rule.
    """
    if channel not in AdminProspectTouch.CHANNELS:
        channel = AdminProspectTouch.CHANNEL_LINKEDIN

    stage_at = p.stage
    db.session.add(AdminProspectTouch(
        prospect_id=p.id,
        channel=channel,
        stage_at=stage_at,
        summary=(summary or p.draft_text or '').strip() or None,
        drafted_by=drafted_by,
    ))

    today = date.today()
    p.last_contact = today
    if advance:
        p.stage = _ADVANCE_ON_LOG.get(stage_at, stage_at)
    p.next_followup = _followup_from(p.stage, today)
    if p.stage != stage_at:
        # Stale draft: it was written for the stage we just left.
        p.draft_text = None
        p.draft_for_stage = None
    db.session.commit()
    return p


def set_stage(p, stage: str, reason: str = '', retouch_on=None):
    """Move a prospect to a stage the founder confirms from outside the app.

    Acceptance, replies, and bookings are external events, so they are recorded
    rather than inferred.
    """
    if stage not in AdminProspect.STAGES:
        raise ValueError('unknown stage: {}'.format(stage))

    prev = p.stage
    p.stage = stage
    if stage == AdminProspect.STAGE_PASSED:
        p.passed_reason = (reason or '').strip()[:200] or None
        p.retouch_on = _parse_date(retouch_on)
        p.next_followup = None
    elif stage == AdminProspect.STAGE_ONBOARDED:
        p.next_followup = None
    else:
        p.next_followup = _followup_from(stage)

    if stage != prev:
        p.draft_text = None
        p.draft_for_stage = None
    db.session.add(AdminProspectTouch(
        prospect_id=p.id,
        channel=AdminProspectTouch.CHANNEL_NOTE,
        stage_at=prev,
        summary='Stage: {} → {}{}'.format(
            AdminProspect.STAGE_LABELS.get(prev, prev),
            AdminProspect.STAGE_LABELS.get(stage, stage),
            ' ({})'.format(reason.strip()) if reason else '',
        ),
    ))
    db.session.commit()
    return p


def delete_prospect(p) -> None:
    db.session.delete(p)
    db.session.commit()


# ── CSV import ────────────────────────────────────────────────────────────────

_CSV_FIELDS = ('firm_name', 'lead_name', 'lead_linkedin', 'website',
               'contact_path', 'fit', 'category', 'notes')


def _norm_site(v: str) -> str:
    v = (v or '').strip().lower()
    v = re.sub(r'^https?://', '', v)
    v = re.sub(r'^www\.', '', v)
    return v.rstrip('/')


def import_csv(text: str) -> dict:
    """Seed the pipeline from CSV, de-duplicated by firm name + website (FR-CRM-02).

    De-dupes against rows already in the table and within the file itself, so
    re-uploading the same list is a no-op rather than a pile of duplicates.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return {'created': 0, 'skipped': 0, 'errors': ['CSV has no header row']}

    headers = {(h or '').strip().lower().replace(' ', '_'): h for h in reader.fieldnames}
    if 'firm_name' not in headers and 'firm' not in headers:
        return {'created': 0, 'skipped': 0,
                'errors': ['CSV needs a firm_name column (got: {})'.format(
                    ', '.join(reader.fieldnames))]}

    seen = {
        (p.firm_name.strip().lower(), _norm_site(p.website))
        for p in AdminProspect.query.all()
    }
    created, skipped, errors = 0, 0, []

    for i, row in enumerate(reader, start=2):
        def get(field):
            src = headers.get(field) or headers.get(field.replace('firm_name', 'firm'))
            return (row.get(src) or '').strip() if src else ''

        firm = get('firm_name')
        if not firm:
            skipped += 1
            continue
        key = (firm.lower(), _norm_site(get('website')))
        if key in seen:
            skipped += 1
            continue

        try:
            create_prospect({f: get(f) for f in _CSV_FIELDS})
            seen.add(key)
            created += 1
        except Exception as exc:
            db.session.rollback()
            errors.append('row {}: {}'.format(i, exc))

    return {'created': created, 'skipped': skipped, 'errors': errors}


# ── Won → ORG-001 provisioning ────────────────────────────────────────────────

def onboard_to_org(p, data: dict):
    """One-click hand-off from a won deal to an organization (FR-CRM-07).

    Creates the CorporateAccount so the firm does not have to be re-entered, links
    it back via won_org_id, and moves the prospect to Onboarded.
    """
    from app.models.corporate import CorporateAccount
    from app.models.audit_log import AuditLog
    from utils.id_gen import generate_id
    from flask import current_app

    if p.won_org_id:
        existing = CorporateAccount.query.get(p.won_org_id)
        if existing:
            return existing

    contact_email = (data.get('contact_email') or '').strip().lower()
    if not contact_email:
        raise ValueError('contact_email is required to provision an organization')

    org_type = data.get('org_type') or CorporateAccount.ORG_PILOT
    if org_type not in CorporateAccount.ORG_TYPES:
        org_type = CorporateAccount.ORG_PILOT

    credits = int(data.get('credits') or 25)
    try:
        from app.services.pricing_service import get_current_price
        credit_value_cents = get_current_price()['base_price_cents']
    except Exception:
        credit_value_cents = current_app.config.get('SIMULATION_PRICE_CENTS', 69500)

    start = date.today()
    end = start + timedelta(days=CorporateAccount.DEFAULT_EXPIRY_DAYS.get(org_type, 365))

    org = CorporateAccount(
        id=generate_id(),
        org_name=p.firm_name,
        contact_name=(p.lead_name or p.firm_name),
        contact_email=contact_email,
        org_type=org_type,
        seat_count=credits or 25,
        credits_purchased=credits,
        credits_remaining=credits,
        credit_value_cents=int(credit_value_cents),
        contract_start=start,
        contract_end=end,
        notes='Provisioned from admin outreach prospect {} ({}).'.format(p.id, p.firm_name),
        status=CorporateAccount.STATUS_PENDING,
    )
    db.session.add(org)
    db.session.flush()

    p.won_org_id = org.id
    p.stage = AdminProspect.STAGE_ONBOARDED
    p.next_followup = None
    p.draft_text = None
    p.draft_for_stage = None
    db.session.add(AdminProspectTouch(
        prospect_id=p.id,
        channel=AdminProspectTouch.CHANNEL_NOTE,
        stage_at=AdminProspect.STAGE_ONBOARDED,
        summary='Won — provisioned organization {} ({} credits, {}).'.format(
            org.id, credits, org_type),
    ))

    try:
        from flask_login import current_user
        actor = current_user.id if getattr(current_user, 'is_authenticated', False) else None
    except Exception:
        actor = None
    AuditLog.log('admin_prospect_onboarded', user_id=actor, resource_id=p.id,
                 metadata={'org_id': org.id, 'firm': p.firm_name, 'credits': credits})
    db.session.commit()
    return org


# ── Morning briefing ──────────────────────────────────────────────────────────

def run_morning_briefing(today=None) -> dict:
    """Draft the day's touches and email the founder the queue (FR-CRM-03, FR-CRM-05).

    Replaces the drafting-and-remembering burden only. Nothing is sent to a
    prospect here — the email goes to the founder.
    """
    today = today or date.today()
    queue = due_queue(today, draft=True)
    if not queue:
        logger.info('admin_crm briefing: nothing due on %s', today)
        return {'due': 0, 'emailed': False}

    overdue = [q for q in queue if q['overdue']]
    sent = _email_briefing(queue, overdue, today)
    return {'due': len(queue), 'overdue': len(overdue), 'emailed': sent}


def _email_briefing(queue: list, overdue: list, today) -> bool:
    from flask import current_app
    from app.services.email_service import _html_wrap, _h1, _p, _btn, _divider, _send

    # FOUNDER_EMAIL only — MAIL_DEFAULT_SENDER is the From address for outbound
    # user mail and is set to a noreply box, which would silently swallow this.
    to = current_app.config.get('FOUNDER_EMAIL') or 'simi@simulacrumai.io'
    base = (current_app.config.get('BASE_URL') or 'https://simulacrumai.io').rstrip('/')

    plain = ['Outreach queue for {}'.format(today.strftime('%A, %B %d, %Y')), '']
    if overdue:
        plain.append('{} overdue.'.format(len(overdue)))
        plain.append('')

    rows_html = []
    for item in queue:
        p = item['prospect']
        flag = ' (OVERDUE)' if item['overdue'] else ''
        kind = item['draft_kind'] or 'reminder'
        plain.append('- {} [{}] {}{}'.format(p['firm_name'], p['stage_label'], kind, flag))
        if item['draft']:
            plain.append('  {}'.format(item['draft'].replace('\n', ' ')))
        plain.append('')

        rows_html.append(
            '<div style="border:1px solid {border};border-radius:8px;padding:12px 14px;'
            'margin-bottom:10px;background:{bg};">'
            '<div style="font-size:14px;font-weight:700;color:#0f172a;">{firm}'
            '{flag}</div>'
            '<div style="font-size:12px;color:#6b7280;margin:2px 0 8px;">{stage}'
            '{lead} · {kind}</div>'
            '{draft}'
            '</div>'.format(
                border='#fecaca' if item['overdue'] else '#e5e7eb',
                bg='#fef2f2' if item['overdue'] else '#ffffff',
                firm=_esc(p['firm_name']),
                flag=' <span style="color:#b91c1c;font-size:11px;font-weight:700;">'
                     'OVERDUE</span>' if item['overdue'] else '',
                stage=_esc(p['stage_label']),
                lead=' · {}'.format(_esc(p['lead_name'])) if p.get('lead_name') else '',
                kind=_esc(item['draft_kind'] or 'reminder — no draft'),
                draft=('<div style="font-size:13px;line-height:1.6;color:#111827;'
                       'white-space:pre-wrap;background:#f9fafb;border-radius:6px;'
                       'padding:10px;">{}</div>'.format(_esc(item['draft'])))
                      if item['draft'] else '',
            )
        )

    html = _html_wrap(
        _h1('Outreach queue — {}'.format(today.strftime('%b %d'))) +
        _p('{} prospect(s) due{}. Drafts are ready to personalise and send; '
           'click Logged in the pipeline once each one is away.'.format(
               len(queue), ', {} overdue'.format(len(overdue)) if overdue else '')) +
        ''.join(rows_html) +
        _divider() +
        _btn('{}/admin/contacts'.format(base), 'Open the pipeline'),
        preheader='{} prospect(s) due today.'.format(len(queue)),
    )

    try:
        _send(subject='Outreach queue — {} due{}'.format(
                  len(queue), ' ({} overdue)'.format(len(overdue)) if overdue else ''),
              recipients=[to], body='\n'.join(plain), html=html)
        return True
    except Exception as exc:
        logger.warning('admin_crm briefing: email failed: %s', exc)
        return False


def _esc(v) -> str:
    return (str(v or '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _parse_date(v):
    if not v:
        return None
    if isinstance(v, date):
        return v
    for fmt in ('%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except ValueError:
            continue
    return None
