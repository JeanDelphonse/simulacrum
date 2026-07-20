"""
Organization service — SIM-PRD-ORG-001 (Organizations & Bulk Provisioning).

Central logic for the credit pool, membership resolution, the partner
dashboard metrics, bulk-invite links, activation reminders, and co-branding
context. Built on the existing corporate_accounts / corporate_employees tables
(the org entity) plus credit_redemptions / org_invoices / org_sme_pod.

Data boundary (FR-ORG-02): nothing here ever exposes a member's simulation
contents, artifacts, income figures, contacts, or agent configuration. The
dashboard sees WHETHER a member activated / published / started a simulation
and AGGREGATE metrics only — never per-member private work.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta

from app.extensions import db

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Membership resolution
# ---------------------------------------------------------------------------

def member_org(user_id: str):
    """Return the CorporateAccount sponsoring this user, or None.

    Resolves via UserProfile.org_id first (canonical link), then falls back to
    an active/complete CorporateEmployee row.
    """
    from app.models.profile import UserProfile
    from app.models.corporate import CorporateAccount, CorporateEmployee

    prof = UserProfile.query.filter_by(user_id=user_id).first()
    if prof and prof.org_id:
        org = CorporateAccount.query.get(prof.org_id)
        if org:
            return org
    emp = (CorporateEmployee.query
           .filter(CorporateEmployee.user_id == user_id)
           .filter(CorporateEmployee.status.in_([
               CorporateEmployee.STATUS_ACTIVE, CorporateEmployee.STATUS_COMPLETE]))
           .first())
    if emp:
        return CorporateAccount.query.get(emp.org_id)
    return None


def member_user_ids(org) -> list[str]:
    """User ids of the org's signed-up members (excludes removed/declined)."""
    from app.models.corporate import CorporateEmployee
    rows = (CorporateEmployee.query
            .filter(CorporateEmployee.org_id == org.id)
            .filter(CorporateEmployee.user_id.isnot(None))
            .filter(CorporateEmployee.status.notin_([
                CorporateEmployee.STATUS_REMOVED, CorporateEmployee.STATUS_DECLINED]))
            .with_entities(CorporateEmployee.user_id)
            .all())
    return [r[0] for r in rows if r[0]]


# ---------------------------------------------------------------------------
# Credit pool (FR-ORG-04 / FR-ORG-05)
# ---------------------------------------------------------------------------

def try_redeem_credit(user_id: str, sim) -> bool:
    """Redeem one org credit for `sim` if the member's pool has capacity.

    Atomic guarded decrement (avoids oversell under concurrency). Adds a
    CreditRedemption to the session; the caller's commit persists it. Returns
    True if a credit was redeemed (sim should run free), False otherwise
    (caller falls through to self-serve checkout — never a hard block).
    """
    try:
        from app.models.corporate import CorporateAccount, CorporateEmployee, CreditRedemption

        org = member_org(user_id)
        if not org or not org.can_redeem:
            return False

        updated = (db.session.query(CorporateAccount)
                   .filter(CorporateAccount.id == org.id,
                           CorporateAccount.status == CorporateAccount.STATUS_ACTIVE,
                           CorporateAccount.credits_remaining > 0)
                   .update({CorporateAccount.credits_remaining:
                            CorporateAccount.credits_remaining - 1},
                           synchronize_session=False))
        if not updated:
            return False

        db.session.add(CreditRedemption(
            org_id=org.id,
            user_id=user_id,
            simulation_id=getattr(sim, 'id', None),
            credit_value_cents=org.credit_value_cents or 0,
        ))

        # Reflect "simulation started" on the membership roster.
        emp = CorporateEmployee.query.filter_by(org_id=org.id, user_id=user_id).first()
        if emp:
            emp.simulation_id = getattr(sim, 'id', None)
            if emp.status == CorporateEmployee.STATUS_INVITED:
                emp.status = CorporateEmployee.STATUS_ACTIVE
                emp.activated_at = emp.activated_at or datetime.utcnow()
        db.session.flush()
        _log.info('org credit redeemed: org=%s user=%s sim=%s',
                  org.id, user_id, getattr(sim, 'id', None))
        return True
    except Exception as exc:  # pragma: no cover - never block sim generation
        _log.warning('try_redeem_credit failed for user %s: %s', user_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


def add_credits(org, quantity: int, actor_id: str | None = None) -> None:
    """Top up an active pool mid-contract (upsell). Logged against the org."""
    from app.models.audit_log import AuditLog
    org.credits_purchased = (org.credits_purchased or 0) + int(quantity)
    org.credits_remaining = (org.credits_remaining or 0) + int(quantity)
    AuditLog.log('org_credits_topup', user_id=actor_id, resource_id=org.id,
                 metadata={'quantity': int(quantity)})


def burn_rate(org) -> dict:
    """Redemption velocity + a plain-language runway projection."""
    from app.models.corporate import CreditRedemption
    try:
        since = datetime.utcnow() - timedelta(days=14)
        recent = (CreditRedemption.query
                  .filter(CreditRedemption.org_id == org.id,
                          CreditRedemption.redeemed_at >= since)
                  .count())
        per_week = recent / 2.0
        remaining = org.credits_remaining or 0
        weeks_left = round(remaining / per_week, 1) if per_week > 0 else None
        if weeks_left is None:
            projection = 'No recent redemptions — pool is not burning down yet.'
        elif remaining <= 0:
            projection = 'Pool exhausted.'
        else:
            wk = int(weeks_left) if weeks_left == int(weeks_left) else weeks_left
            projection = f'At this pace, your pool is exhausted in about {wk} week(s).'
        return {'per_week': round(per_week, 1), 'weeks_left': weeks_left,
                'projection': projection}
    except Exception as exc:
        _log.warning('burn_rate failed for org %s: %s', org.id, exc)
        return {'per_week': 0, 'weeks_left': None, 'projection': ''}


# ---------------------------------------------------------------------------
# Partner dashboard metrics (FR-ORG-10) — aggregate only, never per-member work
# ---------------------------------------------------------------------------

def dashboard_metrics(org) -> dict:
    """Build the partner dashboard: funnel, credit pool, engagement, outcomes.

    Every section degrades gracefully to zeros if its data is unavailable, so
    the buyer's dashboard never hard-fails.
    """
    from app.models.corporate import CorporateEmployee

    uids = member_user_ids(org)

    # ── Activation funnel ──
    provisioned = (CorporateEmployee.query
                   .filter(CorporateEmployee.org_id == org.id)
                   .filter(CorporateEmployee.status.notin_([
                       CorporateEmployee.STATUS_REMOVED,
                       CorporateEmployee.STATUS_DECLINED]))
                   .count())
    signed_up = len(uids)

    bio_published = 0
    try:
        from app.models.profile import UserProfile
        if uids:
            bio_published = (UserProfile.query
                             .filter(UserProfile.user_id.in_(uids),
                                     UserProfile.is_published.is_(True))
                             .count())
    except Exception as exc:
        _log.warning('funnel bio_published failed: %s', exc)

    sim_started = 0
    try:
        from app.models.simulation import Simulation
        if uids:
            sim_started = (db.session.query(Simulation.user_id)
                           .filter(Simulation.user_id.in_(uids))
                           .distinct().count())
    except Exception as exc:
        _log.warning('funnel sim_started failed: %s', exc)

    def _rate(n):
        return round(n / provisioned * 100) if provisioned else 0

    funnel = {
        'invited': provisioned,
        'signed_up': signed_up,
        'bio_published': bio_published,
        'sim_started': sim_started,
        'rates': {
            'signed_up': _rate(signed_up),
            'bio_published': _rate(bio_published),
            'sim_started': _rate(sim_started),
        },
    }

    # ── Credit pool ──
    pool = {
        'purchased': org.credits_purchased or 0,
        'redeemed': org.credits_used,
        'remaining': org.credits_remaining or 0,
        'value_cents': org.credit_value_cents or 0,
        'expiry': org.contract_end.isoformat() if org.contract_end else None,
        'burn': burn_rate(org),
    }

    # ── Engagement (aggregate) ──
    engagement = {'bio_views': 0, 'leads': 0, 'chats': 0, 'likes': 0}
    try:
        from app.models.bio_page import BioPage
        if uids:
            rows = (BioPage.query
                    .filter(BioPage.user_id.in_(uids))
                    .with_entities(BioPage.view_count, BioPage.contact_form_count,
                                   BioPage.like_count)
                    .all())
            engagement['bio_views'] = sum((r[0] or 0) for r in rows)
            engagement['leads'] = sum((r[1] or 0) for r in rows)
            engagement['likes'] = sum((r[2] or 0) for r in rows)
    except Exception as exc:
        _log.warning('engagement bio metrics failed: %s', exc)
    try:
        from app.models.bio_page import BioChatSession
        if uids:
            engagement['chats'] = (BioChatSession.query
                                   .filter(BioChatSession.user_id.in_(uids))
                                   .count())
    except Exception as exc:
        _log.warning('engagement chats failed: %s', exc)

    # ── Outcomes (aggregate) ──
    outcomes = {'by_status': {}, 'total_income_cents': 0, 'income_by_layer': {}}
    try:
        from app.models.simulation import Simulation
        if uids:
            for status, cnt in (db.session.query(Simulation.status, db.func.count())
                                .filter(Simulation.user_id.in_(uids))
                                .group_by(Simulation.status).all()):
                outcomes['by_status'][status] = cnt
    except Exception as exc:
        _log.warning('outcomes by_status failed: %s', exc)
    try:
        from app.models.simulation import Simulation
        from app.models.income import LayerIncomeRecord
        if uids:
            sim_ids = [r[0] for r in db.session.query(Simulation.id)
                       .filter(Simulation.user_id.in_(uids)).all()]
            if sim_ids:
                q = (db.session.query(LayerIncomeRecord.layer_number,
                                      db.func.sum(LayerIncomeRecord.amount))
                     .filter(LayerIncomeRecord.simulation_id.in_(sim_ids),
                             LayerIncomeRecord.is_void.is_(False))
                     .group_by(LayerIncomeRecord.layer_number).all())
                total = 0
                for layer, amt in q:
                    cents = int(round(float(amt or 0) * 100))
                    outcomes['income_by_layer'][int(layer)] = cents
                    total += cents
                outcomes['total_income_cents'] = total
    except Exception as exc:
        _log.warning('outcomes income failed: %s', exc)

    return {'funnel': funnel, 'pool': pool, 'engagement': engagement,
            'outcomes': outcomes}


def roster(org) -> list[dict]:
    """Per-member activation status ONLY (FR-ORG-02 data boundary).

    Explicitly NOT included: resume, artifacts, income, contacts, agent config.
    """
    from app.models.corporate import CorporateEmployee
    from app.models.profile import UserProfile
    from app.models.simulation import Simulation

    emps = (CorporateEmployee.query
            .filter_by(org_id=org.id)
            .order_by(CorporateEmployee.provisioned_at.desc())
            .all())
    uids = [e.user_id for e in emps if e.user_id]

    published = set()
    started = set()
    if uids:
        try:
            published = {r[0] for r in UserProfile.query
                         .filter(UserProfile.user_id.in_(uids),
                                 UserProfile.is_published.is_(True))
                         .with_entities(UserProfile.user_id).all()}
        except Exception:
            pass
        try:
            started = {r[0] for r in db.session.query(Simulation.user_id)
                       .filter(Simulation.user_id.in_(uids)).distinct().all()}
        except Exception:
            pass

    out = []
    for e in emps:
        out.append({
            'id': e.id,
            'name': e.full_name or '',
            'email': e.email,
            'status': e.status,
            'role': e.role,
            'bio_published': e.user_id in published,
            'sim_started': (e.user_id in started) or bool(e.simulation_id),
            'activated_at': e.activated_at.isoformat() if e.activated_at else None,
        })
    return out


# ---------------------------------------------------------------------------
# Bulk invite (FR-ORG-08 / FR-ORG-09)
# ---------------------------------------------------------------------------

def ensure_invite_link(org, cap: int | None = None, expires_at=None) -> str:
    """Create (or return) the org's shareable join token."""
    if not org.invite_token:
        org.invite_token = org.generate_invite_token()
    if cap is not None:
        org.invite_cap = cap
    if expires_at is not None:
        org.invite_expires_at = expires_at
    return org.invite_token


def invite_link_ok(org) -> bool:
    """True if the shareable link is currently usable."""
    if not org.invite_token or org.status != org.STATUS_ACTIVE:
        return False
    if org.invite_expires_at and org.invite_expires_at < datetime.utcnow():
        return False
    if org.invite_cap is not None and (org.invite_uses or 0) >= org.invite_cap:
        return False
    return True


def org_for_email_domain(email: str):
    """Return an active org whose auto_join_domains covers this email, or None."""
    from app.models.corporate import CorporateAccount
    if not email or '@' not in email:
        return None
    domain = email.rsplit('@', 1)[1].strip().lower()
    if not domain:
        return None
    try:
        candidates = (CorporateAccount.query
                      .filter(CorporateAccount.status == CorporateAccount.STATUS_ACTIVE)
                      .filter(CorporateAccount.auto_join_domains.isnot(None))
                      .all())
    except Exception as exc:
        _log.warning('org_for_email_domain query failed: %s', exc)
        return None
    for org in candidates:
        domains = [str(d).strip().lower() for d in (org.auto_join_domains or [])]
        if domain in domains:
            return org
    return None


def link_member(org, user_id: str, email: str, full_name: str | None,
                join_source: str) -> None:
    """Attach a user to an org: create/update the membership row + profile link.

    Idempotent. Does not consume credits (that happens at simulation start).
    """
    from app.models.corporate import CorporateEmployee
    from app.models.profile import UserProfile

    email = (email or '').strip().lower()
    emp = None
    if email:
        emp = CorporateEmployee.query.filter_by(org_id=org.id, email=email).first()
    if not emp:
        emp = CorporateEmployee.query.filter_by(org_id=org.id, user_id=user_id).first()
    if not emp:
        emp = CorporateEmployee(
            org_id=org.id, email=email or f'{user_id}@unknown',
            full_name=full_name, join_source=join_source,
        )
        db.session.add(emp)
        org.seats_used = (org.seats_used or 0) + 1
    emp.user_id = user_id
    emp.join_source = emp.join_source or join_source
    if emp.status in (CorporateEmployee.STATUS_INVITED, None):
        emp.status = CorporateEmployee.STATUS_ACTIVE
        emp.activated_at = emp.activated_at or datetime.utcnow()
    emp.invite_token = None

    prof = UserProfile.query.filter_by(user_id=user_id).first()
    if prof and not prof.org_id:
        prof.org_id = org.id


# ---------------------------------------------------------------------------
# Activation reminders (FR-ORG-09) — Day 3 / Day 10, then stop
# ---------------------------------------------------------------------------

def due_reminders(org=None):
    """Members invited-but-not-activated who are due a nudge (Day 3 / Day 10)."""
    from app.models.corporate import CorporateEmployee
    now = datetime.utcnow()
    q = CorporateEmployee.query.filter(
        CorporateEmployee.status == CorporateEmployee.STATUS_INVITED)
    if org is not None:
        q = q.filter(CorporateEmployee.org_id == org.id)
    due = []
    for e in q.all():
        if e.reminder_count >= 2:
            continue
        age = (now - e.provisioned_at).days if e.provisioned_at else 0
        threshold = 3 if e.reminder_count == 0 else 10
        if age >= threshold:
            due.append(e)
    return due
