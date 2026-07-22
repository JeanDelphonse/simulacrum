"""
SIM-PRD-PRIVACY-001 — Bio Page Private Mode service.

The retrieval boundary and state machine for the LinkedIn-verified access loop:

    teaser  →  request (LinkedIn OAuth)  →  rules (allow/block) or pending
            →  owner approve (dashboard or one-tap email)
            →  grant (persists until revoked) + verified warm-lead contact

Gating is enforced HERE, server-side: an un-approved session never has gated bio
content assembled for it (neither the page payload nor Simi's context), so there
is nothing to leak client-side.

Approved-viewer identity is remembered in the signed Flask session (keyed by page
owner) after LinkedIn OAuth; the BioAccessGrant row is the revocable source of
truth checked on every visit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from flask import current_app, request, session

from app.extensions import db
from app.models.bio_privacy import BioAccessRequest, BioAccessGrant, BioAccessRule
from app.models.profile import UserProfile
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

_SESSION_KEY = '_bio_viewers'          # {owner_user_id: requester_linkedin}
_TOKEN_SALT = 'bio-access-approve'
EXPIRY_DAYS = 30                       # default silent-expiry window (FR-PRV-05)
_REENTRY_THROTTLE = timedelta(hours=1)  # min gap before a repeat-view signal fires


# ── Session-side viewer identity ─────────────────────────────────────────────

def remember_viewer(owner_user_id: str, requester_linkedin: str) -> None:
    """Record (in the signed session cookie) that this browser authenticated as
    `requester_linkedin` for `owner_user_id`'s page."""
    viewers = dict(session.get(_SESSION_KEY) or {})
    viewers[owner_user_id] = requester_linkedin
    session[_SESSION_KEY] = viewers
    session.permanent = True


def viewer_linkedin_for(owner_user_id: str) -> str | None:
    return (session.get(_SESSION_KEY) or {}).get(owner_user_id)


# ── Grant / access checks ────────────────────────────────────────────────────

def active_grant(owner_user_id: str, requester_linkedin: str | None) -> BioAccessGrant | None:
    if not requester_linkedin:
        return None
    return BioAccessGrant.query.filter_by(
        owner_user_id=owner_user_id,
        requester_linkedin=requester_linkedin,
        revoked_at=None,
    ).first()


def session_has_access(owner_user_id: str) -> bool:
    """True when the current browser session holds an active grant for this owner."""
    li = viewer_linkedin_for(owner_user_id)
    return active_grant(owner_user_id, li) is not None


def can_view_full(profile: UserProfile, viewer_is_owner: bool) -> bool:
    """Whether the current viewer may see the full (gated) bio page."""
    if not profile or not profile.is_private:
        return True
    if viewer_is_owner:
        return True
    return session_has_access(profile.user_id)


def pending_request_for_session(owner_user_id: str) -> BioAccessRequest | None:
    li = viewer_linkedin_for(owner_user_id)
    if not li:
        return None
    return BioAccessRequest.query.filter_by(
        owner_user_id=owner_user_id, requester_linkedin=li,
    ).first()


# ── Teaser context (the ONLY content served to an un-approved session) ───────

def expertise_tags(profile: UserProfile, limit: int = 6) -> list[str]:
    """High-level expertise zones only — no detail (FR-PRV-02)."""
    tags: list[str] = []
    seen: set = set()

    def _add(v):
        if not v:
            return
        k = str(v).strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            tags.append(k)

    try:
        for z in (profile.canonical_zones or []):
            _add(z.get('category'))
    except Exception:
        pass

    if not tags:
        # Fallback: expertise_zone of the most recent complete simulation
        try:
            from app.models.simulation import Simulation
            sim = Simulation.query.filter_by(
                user_id=profile.user_id, status='complete',
            ).order_by(Simulation.created_at.desc()).first()
            if sim:
                _add(sim.expertise_zone)
        except Exception:
            pass

    return tags[:limit]


def teaser_context(profile: UserProfile, user, bp) -> dict:
    """Minimal, non-sensitive teaser payload. Deliberately excludes bio prose,
    timeline, services, rates, contact, testimonials — none of it is assembled."""
    headline = (profile.tagline or '')[:200]
    # A custom hero title (if set) is one line of positioning — safe to show.
    hero = (bp.sections.get('hero') if bp else {}) or {}
    if not headline and hero.get('is_custom_title') and hero.get('professional_title'):
        headline = hero['professional_title'][:200]
    return {
        'display_name': profile.display_name or (user.full_name if user else '') or profile.username,
        'headline': headline,
        'avatar_path': profile.avatar_path or '',
        'expertise_tags': expertise_tags(profile),
        'accepting_requests': bool(profile.accepting_requests),
    }


# ── LinkedIn identity normalization ──────────────────────────────────────────

def identity_from_userinfo(userinfo: dict) -> dict:
    """Map an OpenID Connect userinfo payload to our verified-identity shape.

    The 'openid profile email' scope reliably returns sub, name and (with member
    permission) email + picture. Company/industry are captured when the app's
    granted products expose them, else left null — the verification guarantee
    (a real LinkedIn login) holds regardless."""
    sub = (userinfo.get('sub') or '').strip()
    name = (userinfo.get('name')
            or f"{userinfo.get('given_name', '')} {userinfo.get('family_name', '')}".strip()
            or 'LinkedIn member')
    # Identity anchor: the opaque, verified member id. Not a guessable public URL.
    anchor = f'urn:li:person:{sub}' if sub else f'li:{name.lower()}'
    return {
        'anchor': anchor[:300],
        'name': name[:160],
        'email': (userinfo.get('email') or None),
        'avatar': (userinfo.get('picture') or None),
        'company': (userinfo.get('company') or userinfo.get('organization') or None),
        'industry': (userinfo.get('industry') or None),
    }


# ── Rules ────────────────────────────────────────────────────────────────────

def evaluate_rules(owner_user_id: str, identity: dict) -> str | None:
    """Return 'allow', 'block', or None (→ manual pending). Block wins over allow."""
    rules = BioAccessRule.query.filter_by(owner_user_id=owner_user_id).all()
    if not rules:
        return None

    email = (identity.get('email') or '').lower()
    domain = email.split('@', 1)[1] if '@' in email else ''
    company = (identity.get('company') or '').lower()
    anchor = (identity.get('anchor') or '').lower()

    def _matches(rule: BioAccessRule) -> bool:
        val = (rule.match_value or '').strip().lower()
        if not val:
            return False
        if rule.match_type == BioAccessRule.MATCH_DOMAIN:
            return bool(domain) and (domain == val or domain.endswith('.' + val))
        if rule.match_type == BioAccessRule.MATCH_COMPANY:
            return bool(company) and val in company
        if rule.match_type == BioAccessRule.MATCH_LINKEDIN:
            return bool(anchor) and val in anchor
        return False

    decision = None
    for r in rules:
        if _matches(r):
            if r.rule_type == BioAccessRule.RULE_BLOCK:
                return 'block'          # block short-circuits
            if r.rule_type == BioAccessRule.RULE_ALLOW:
                decision = 'allow'
    return decision


# ── Request lifecycle ────────────────────────────────────────────────────────

def record_request(owner_user_id: str, identity: dict, message: str | None) -> tuple:
    """Create/refresh an access request and apply allow/block rules.

    Returns (request, outcome) where outcome ∈
    {'auto_approved', 'blocked', 'pending', 'already_approved', 'already_pending'}.
    """
    anchor = identity['anchor']
    existing = BioAccessRequest.query.filter_by(
        owner_user_id=owner_user_id, requester_linkedin=anchor,
    ).first()

    # Already-approved identity re-authenticating → just refresh the session.
    if existing and existing.is_approved and active_grant(owner_user_id, anchor):
        remember_viewer(owner_user_id, anchor)
        return existing, 'already_approved'

    if existing:
        req = existing
        # Refresh verified fields (identity may have been re-pulled) but do not
        # reopen a resolved request unless it had expired.
        if req.status == BioAccessRequest.STATUS_EXPIRED:
            req.status = BioAccessRequest.STATUS_PENDING
            req.resolved_at = None
            req.created_at = datetime.utcnow()
    else:
        req = BioAccessRequest(id=generate_id(), owner_user_id=owner_user_id,
                               requester_linkedin=anchor)
        db.session.add(req)

    req.requester_name = identity['name']
    req.requester_email = identity.get('email')
    req.requester_avatar = identity.get('avatar')
    req.requester_company = identity.get('company')
    req.requester_industry = identity.get('industry')
    if message:
        req.message = message[:2000]

    remember_viewer(owner_user_id, anchor)

    # Rate limiting / dedup: an existing still-pending request just re-notifies softly.
    if existing and req.status == BioAccessRequest.STATUS_PENDING and not (
        existing.status == BioAccessRequest.STATUS_EXPIRED
    ):
        db.session.commit()
        return req, 'already_pending'

    decision = evaluate_rules(owner_user_id, identity)
    if decision == 'block':
        req.status = BioAccessRequest.STATUS_BLOCKED
        req.resolved_at = datetime.utcnow()
        db.session.commit()
        return req, 'blocked'

    if decision == 'allow':
        _grant(req, auto=True)
        db.session.commit()
        return req, 'auto_approved'

    # Manual approval needed.
    req.status = BioAccessRequest.STATUS_PENDING
    db.session.commit()
    _notify_owner(owner_user_id, req)
    return req, 'pending'


def _grant(req: BioAccessRequest, auto: bool) -> BioAccessGrant:
    """Approve a request: flip status, upsert the grant, create the warm lead, and
    email the requester. Caller commits."""
    req.status = (BioAccessRequest.STATUS_AUTO_APPROVED if auto
                  else BioAccessRequest.STATUS_APPROVED)
    req.resolved_at = datetime.utcnow()

    grant = BioAccessGrant.query.filter_by(
        owner_user_id=req.owner_user_id, requester_linkedin=req.requester_linkedin,
    ).first()
    if grant:
        grant.revoked_at = None
        grant.requester_name = req.requester_name
    else:
        grant = BioAccessGrant(
            id=generate_id(),
            owner_user_id=req.owner_user_id,
            requester_linkedin=req.requester_linkedin,
            requester_name=req.requester_name,
        )
        db.session.add(grant)

    try:
        _create_warm_lead(req)
    except Exception as exc:
        logger.warning('warm-lead contact creation failed: %s', exc)

    try:
        _email_requester_approved(req)
    except Exception as exc:
        logger.warning('requester approval email failed: %s', exc)

    return grant


def approve_request(request_id: str, owner_user_id: str | None = None) -> BioAccessRequest | None:
    """Owner (dashboard) or one-tap (email token) approval. If owner_user_id is
    given it is enforced."""
    req = BioAccessRequest.query.filter_by(id=request_id).first()
    if not req:
        return None
    if owner_user_id and req.owner_user_id != owner_user_id:
        return None
    if req.is_approved and active_grant(req.owner_user_id, req.requester_linkedin):
        return req  # idempotent
    _grant(req, auto=False)
    db.session.commit()
    return req


def revoke_grant(grant_id: str, owner_user_id: str) -> bool:
    grant = BioAccessGrant.query.filter_by(id=grant_id, owner_user_id=owner_user_id).first()
    if not grant or grant.revoked_at is not None:
        return False
    grant.revoked_at = datetime.utcnow()
    # Reflect on the originating request so the dashboard shows 'revoked'.
    req = BioAccessRequest.query.filter_by(
        owner_user_id=owner_user_id, requester_linkedin=grant.requester_linkedin,
    ).first()
    if req and req.is_approved:
        req.status = BioAccessRequest.STATUS_REVOKED
        req.resolved_at = datetime.utcnow()
    db.session.commit()
    return True


def expire_stale_requests(days: int = EXPIRY_DAYS) -> int:
    """Silently expire pending requests older than `days` (FR-PRV-05). No email is
    ever sent. Safe to call from a maintenance cron."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    stale = BioAccessRequest.query.filter(
        BioAccessRequest.status == BioAccessRequest.STATUS_PENDING,
        BioAccessRequest.created_at < cutoff,
    ).all()
    for r in stale:
        r.status = BioAccessRequest.STATUS_EXPIRED
        r.resolved_at = datetime.utcnow()
    if stale:
        db.session.commit()
    return len(stale)


# ── View recording + re-entry (buying-intent) signal ────────────────────────

def record_view(owner_user_id: str, requester_linkedin: str | None) -> None:
    grant = active_grant(owner_user_id, requester_linkedin)
    if not grant:
        return
    prev = grant.last_viewed_at
    grant.view_count = (grant.view_count or 0) + 1
    grant.last_viewed_at = datetime.utcnow()
    # Re-entry signal: an already-approved viewer returning is a buying-intent
    # signal (FR-PRV-09). In-app only, throttled to avoid noise.
    if prev is not None and (grant.last_viewed_at - prev) > _REENTRY_THROTTLE:
        try:
            from app.models.notification import Notification
            db.session.add(Notification(
                id=generate_id(),
                user_id=owner_user_id,
                notification_type='bio_access_reentry',
                title=f'{grant.requester_name or "An approved viewer"} viewed your profile again',
                body=f'They have now viewed your private profile {grant.view_count} times.',
                cta_url='/settings/bio-privacy',
                cta_label='View access dashboard',
                priority='low',
            ))
        except Exception:
            pass
    db.session.commit()


# ── Warm-lead CRM contact ────────────────────────────────────────────────────

def _create_warm_lead(req: BioAccessRequest) -> None:
    """Create/update a verified warm-lead contact (FR-PRV-09)."""
    from app.models.contact import Contact, ContactActivity

    parts = (req.requester_name or 'LinkedIn member').strip().split(' ', 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ''
    # A verified email is preferred; fall back to a synthetic anchor address so the
    # contact is still created and de-duplicated (email column is NOT NULL).
    email = (req.requester_email or '').lower().strip() or f'{req.requester_linkedin}@linkedin.invalid'
    note = 'Verified warm lead — approved access to your private bio page.'
    if req.message:
        note += f' Note: "{req.message[:300]}"'

    existing = Contact.query.filter_by(user_id=req.owner_user_id, email=email).first()
    if existing:
        existing.company_name = existing.company_name or req.requester_company
        existing.industry = existing.industry or req.requester_industry
        existing.source_notes = note[:500]
        activity_contact_id = existing.id
    else:
        contact = Contact(
            id=generate_id(),
            user_id=req.owner_user_id,
            first_name=first[:100],
            last_name=last[:100],
            email=email[:255],
            company_name=(req.requester_company or None),
            industry=(req.requester_industry or None),
            linkedin_url=req.requester_linkedin[:500],
            source='private_page_request',
            source_notes=note[:500],
            pipeline_stage='prospect',   # CRM enum has no 'warm_lead'; source marks it
        )
        db.session.add(contact)
        db.session.flush()
        activity_contact_id = contact.id

    db.session.add(ContactActivity(
        id=generate_id(),
        contact_id=activity_contact_id,
        activity_type='private_access_approved',
        created_by='system',
        notes=note[:1000],
    ))


# ── Owner notification (realtime vs digest) ──────────────────────────────────

def _notify_owner(owner_user_id: str, req: BioAccessRequest) -> None:
    """Notify the owner of a pending request. Realtime sends email + in-app with a
    one-tap approve link; digest creates in-app only (batched email out of band)."""
    profile = UserProfile.query.filter_by(user_id=owner_user_id).first()
    cadence = (profile.request_notify if profile else 'realtime') or 'realtime'

    ident = req.requester_name
    if req.requester_company:
        ident += f' · {req.requester_company}'
    body = (f'{ident} requested access to your private bio page.'
            + (f' They wrote: "{req.message[:200]}"' if req.message else ''))
    approve_url = approve_link(req)

    if cadence == 'digest':
        # In-app only; the per-request email is suppressed (FR-PRV-07 digest).
        try:
            from app.models.notification import Notification
            db.session.add(Notification(
                id=generate_id(),
                user_id=owner_user_id,
                notification_type='bio_access_request',
                title=f'{req.requester_name} requested access',
                body=body,
                cta_url='/settings/bio-privacy',
                cta_label='Review requests',
                priority='normal',
            ))
            db.session.commit()
        except Exception as exc:
            logger.warning('digest in-app notification failed: %s', exc)
        return

    # Realtime: in-app + email with a one-tap approve CTA.
    try:
        from app.services.notification_service import send_notification
        send_notification(
            user_id=owner_user_id,
            notification_type='bio_access_request',
            title=f'{req.requester_name} requested access to your bio page',
            body=body,
            cta_url=approve_url,
            cta_label='Approve access',
            priority='normal',
        )
    except Exception as exc:
        logger.warning('realtime access-request notification failed: %s', exc)


def _email_requester_approved(req: BioAccessRequest) -> None:
    """Email the requester that they've been approved (FR-PRV-05)."""
    if not req.requester_email:
        return
    profile = UserProfile.query.filter_by(user_id=req.owner_user_id).first()
    owner_name = (profile.display_name if profile else None) or 'The profile owner'
    slug = profile.username if profile else ''
    base = _base_url()
    view_url = f'{base}/u/{slug}' if slug else base
    first = (req.requester_name or 'there').split(' ', 1)[0]

    subject = f"You've been approved — view {owner_name}'s profile"
    body = (f'Hi {first},\n\n'
            f'{owner_name} approved your request to view their full profile.\n\n'
            f'View it here: {view_url}\n\n'
            f'(Sign in with the same LinkedIn account you used to request access.)\n\n'
            f'— Simulacrum')
    try:
        from app.services.email_service import _send, _html_wrap, _btn
        html = _html_wrap(
            f'<p style="font-size:16px;color:#111827;">Hi {first},</p>'
            f'<p style="font-size:15px;color:#374151;line-height:1.6;">'
            f'<strong>{owner_name}</strong> approved your request to view their full profile.</p>'
            f'{_btn(view_url, "View the profile")}'
            f'<p style="font-size:13px;color:#9ca3af;margin-top:20px;">'
            f'Sign in with the same LinkedIn account you used to request access.</p>',
            preheader=f"{owner_name} approved your access request",
        )
        _send(subject=subject, recipients=[req.requester_email], body=body, html=html)
    except Exception as exc:
        logger.warning('approval email send failed: %s', exc)


# ── Approval tokens (one-tap approve from email) ─────────────────────────────

def _serializer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=_TOKEN_SALT)


def make_approve_token(req: BioAccessRequest) -> str:
    return _serializer().dumps({'rid': req.id, 'owner': req.owner_user_id})


def verify_approve_token(token: str, max_age_days: int = EXPIRY_DAYS) -> dict | None:
    try:
        return _serializer().loads(token, max_age=max_age_days * 86400)
    except Exception:
        return None


def approve_link(req: BioAccessRequest) -> str:
    return f'{_base_url()}/bio/access/approve/{make_approve_token(req)}'


def _base_url() -> str:
    try:
        return request.url_root.rstrip('/')
    except Exception:
        return (current_app.config.get('APP_BASE_URL')
                or 'https://simulacrumai.io').rstrip('/')


# ── Analytics (distinct from public-page views, FR-PRV-09) ──────────────────

def analytics(owner_user_id: str) -> dict:
    reqs = BioAccessRequest.query.filter_by(owner_user_id=owner_user_id).all()
    total = len(reqs)
    approved = sum(1 for r in reqs if r.is_approved)
    pending = sum(1 for r in reqs if r.status == BioAccessRequest.STATUS_PENDING)
    grants = BioAccessGrant.query.filter_by(owner_user_id=owner_user_id).all()
    active = [g for g in grants if g.revoked_at is None]
    repeat_viewers = sum(1 for g in active if (g.view_count or 0) > 1)
    total_views = sum((g.view_count or 0) for g in grants)
    # Denominator excludes still-pending (undecided) requests.
    decided = sum(1 for r in reqs
                  if r.status not in (BioAccessRequest.STATUS_PENDING,))
    return {
        'requests_total': total,
        'requests_pending': pending,
        'approved': approved,
        'approval_rate': round(approved / decided, 3) if decided else 0.0,
        'active_grants': len(active),
        'repeat_viewers': repeat_viewers,
        'gated_views_total': total_views,
    }
