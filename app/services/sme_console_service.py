"""SIM-PRD-SME-002 — SME Simulation Visibility & Advisory service.

The SME advises; the user acts. This service is the business logic behind the SME
console (caseload triage, read-only user drill-down with the Layer-5 gate) and the
recommendation loop (typed advice the USER applies with one click). It also carries
the transparency card, opt-out / request-different / re-opt-in, and the access audit.

Read-only guarantee: nothing here writes to a user's simulation, agents, artifacts, or
settings on behalf of an SME. The only SME-originated writes are to sme_recommendations
and sme_access_log. apply_recommendation() runs with the USER as the actor of record.
"""
import logging
from datetime import datetime, timedelta

from app.extensions import db
from app.models.profile import UserProfile
from app.models.user import User
from app.models.sme import SimiSME, SmeRecommendation, SmeAccessLog
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

# Triage thresholds (SIM-PRD-SME-002 §7 — reusing founder-ops stuck-customer logic)
STALLED_DAYS = 14          # no signals in this many days -> stalled pipeline
NOT_VIEWED_DAYS = 14       # SME hasn't opened this user recently
REC_EXPIRY_DAYS = 30       # untouched recommendations auto-expire (§4)

L5_PLACEHOLDER = 'Financial detail visible to Finance advisors only.'

# L5 income layer — artifact contents gated for non-Finance/Consulting SMEs (§3).
L5_LAYER = 5


# ── Role resolution & access control (FR-SMV-01/02) ───────────────────────────

def get_sme_for_login(user):
    """Return the Active SimiSME whose console login is this user, or None.

    This is what makes SME a distinct role: a normal login is treated as an SME
    session only when an active simi_smes row points at it via auth_user_id.
    """
    if not user or not getattr(user, 'id', None):
        return None
    return SimiSME.query.filter_by(
        auth_user_id=user.id, status=SimiSME.STATUS_ACTIVE,
    ).first()


def assigned_profile(sme, user_id):
    """Return the user's profile IFF it is currently assigned to this SME, else None.

    Assigned-only scope (FR-SMV-02): an SME sees only users whose profile.sme_id
    equals the SME's id. Callers treat None as 403 + logged security event.
    """
    if not sme or not user_id:
        return None
    profile = UserProfile.query.filter_by(user_id=user_id, sme_id=sme.id).first()
    return profile


def log_access(sme_id, action, user_id=None, detail=None, commit=True):
    """Append an SME access-log row (FR-SMV-10)."""
    try:
        row = SmeAccessLog(sme_id=sme_id, user_id=user_id, action=action)
        if detail:
            row.detail = detail
        db.session.add(row)
        if commit:
            db.session.commit()
    except Exception as exc:
        logger.warning('sme access log failed sme=%s action=%s: %s', sme_id, action, exc)
        try:
            db.session.rollback()
        except Exception:
            pass


def log_denial(sme, user_id, reason='not_assigned'):
    """Record a denied access attempt as a security event for admin review (FR-SMV-02)."""
    log_access(sme.id, 'denied', user_id=user_id, detail={'reason': reason})


# ── Caseload dashboard + triage (FR-SMV-09) ───────────────────────────────────

def _last_signal_map(user_ids):
    from sqlalchemy import func
    from app.models.integration_signal import IntegrationSignal
    if not user_ids:
        return {}
    rows = (
        db.session.query(IntegrationSignal.user_id, func.max(IntegrationSignal.created_at))
        .filter(IntegrationSignal.user_id.in_(user_ids))
        .group_by(IntegrationSignal.user_id).all()
    )
    return {r[0]: r[1] for r in rows}


def _last_viewed_map(sme_id, user_ids):
    from sqlalchemy import func
    if not user_ids:
        return {}
    rows = (
        db.session.query(SmeAccessLog.user_id, func.max(SmeAccessLog.created_at))
        .filter(SmeAccessLog.sme_id == sme_id,
                SmeAccessLog.user_id.in_(user_ids),
                SmeAccessLog.action == 'view_user')
        .group_by(SmeAccessLog.user_id).all()
    )
    return {r[0]: r[1] for r in rows}


def _unread_rec_counts(sme_id, user_ids):
    from sqlalchemy import func
    if not user_ids:
        return {}
    rows = (
        db.session.query(SmeRecommendation.user_id, func.count(SmeRecommendation.id))
        .filter(SmeRecommendation.sme_id == sme_id,
                SmeRecommendation.user_id.in_(user_ids),
                SmeRecommendation.status == SmeRecommendation.STATUS_PENDING,
                SmeRecommendation.seen_at.is_(None))
        .group_by(SmeRecommendation.user_id).all()
    )
    return {r[0]: r[1] for r in rows}


def caseload(sme):
    """Return the SME's assigned users with at-a-glance status + triage flags (§7).

    Triage surfaces: stalled pipelines (no signals in STALLED_DAYS), unread
    recommendations, and users not viewed recently.
    """
    from app.models.simulation import Simulation
    from app.models.layer6 import Layer6Outcome, Layer6Cycle
    from sqlalchemy import func

    profiles = UserProfile.query.filter_by(sme_id=sme.id).all()
    user_ids = [p.user_id for p in profiles]
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}

    # Bulk aggregates
    sim_counts, income_totals, last_cycle = {}, {}, {}
    if user_ids:
        for uid, cnt in (
            db.session.query(Simulation.user_id, func.count(Simulation.id))
            .filter(Simulation.user_id.in_(user_ids)).group_by(Simulation.user_id).all()
        ):
            sim_counts[uid] = cnt
        # income + last activity keyed via simulations owned by these users
        sim_owner = {
            s.id: s.user_id for s in
            Simulation.query.filter(Simulation.user_id.in_(user_ids)).all()
        }
        sim_ids = list(sim_owner.keys())
        if sim_ids:
            for sid, total in (
                db.session.query(Layer6Outcome.simulation_id, func.sum(Layer6Outcome.actual_income))
                .filter(Layer6Outcome.simulation_id.in_(sim_ids))
                .group_by(Layer6Outcome.simulation_id).all()
            ):
                income_totals[sim_owner[sid]] = income_totals.get(sim_owner[sid], 0) + float(total or 0)
            for sid, ts in (
                db.session.query(Layer6Cycle.simulation_id, func.max(Layer6Cycle.cycle_completed_at))
                .filter(Layer6Cycle.simulation_id.in_(sim_ids))
                .group_by(Layer6Cycle.simulation_id).all()
            ):
                prev = last_cycle.get(sim_owner[sid])
                if ts and (prev is None or ts > prev):
                    last_cycle[sim_owner[sid]] = ts

    last_signal = _last_signal_map(user_ids)
    last_viewed = _last_viewed_map(sme.id, user_ids)
    unread = _unread_rec_counts(sme.id, user_ids)
    now = datetime.utcnow()

    rows = []
    for p in profiles:
        u = users.get(p.user_id)
        if not u:
            continue
        sig_ts = last_signal.get(p.user_id)
        stalled = (sig_ts is None) or ((now - sig_ts) > timedelta(days=STALLED_DAYS))
        viewed_ts = last_viewed.get(p.user_id)
        not_viewed = (viewed_ts is None) or ((now - viewed_ts) > timedelta(days=NOT_VIEWED_DAYS))
        unread_count = unread.get(p.user_id, 0)
        last_activity = last_cycle.get(p.user_id) or sig_ts
        rows.append({
            'user_id': p.user_id,
            'display_name': (p.display_name if p and p.display_name else u.full_name) or u.email,
            'username': p.username,
            'primary_zone': p.primary_zone,
            'sim_count': sim_counts.get(p.user_id, 0),
            'headline_income': round(income_totals.get(p.user_id, 0), 2),
            'last_activity': last_activity.isoformat() if last_activity else None,
            'last_signal_at': sig_ts.isoformat() if sig_ts else None,
            'last_viewed_at': viewed_ts.isoformat() if viewed_ts else None,
            'unread_recommendations': unread_count,
            'needs_reassignment': bool(p.needs_reassignment),
            'flags': {
                'stalled': stalled,
                'unread': unread_count > 0,
                'not_viewed': not_viewed,
            },
        })

    # Attention first: stalled, then unread, then not-viewed, then name.
    rows.sort(key=lambda r: (
        not r['flags']['stalled'],
        not r['flags']['unread'],
        not r['flags']['not_viewed'],
        (r['display_name'] or '').lower(),
    ))
    return rows


# ── User drill-down (FR-SMV-03/04) ────────────────────────────────────────────

def _bayesian_scores_for_sim(sim_id):
    """Map action_type -> representative Bayesian posterior mean (mean of matching keys)."""
    from app.models.bayesian import BayesianPosterior
    rows = BayesianPosterior.query.filter_by(simulation_id=sim_id).all()
    buckets = {}
    for r in rows:
        # posterior_key like 'reply_rate:cold_email_campaign'
        at = r.posterior_key.split(':', 1)[1] if ':' in r.posterior_key else r.posterior_key
        buckets.setdefault(at, []).append(float(r.value))
    return {at: round(sum(v) / len(v), 3) for at, v in buckets.items() if v}


def _last_outcome_for_sim(sim_id):
    """Map action_type -> {status, completed_at} of its most recent AgentAction."""
    from app.models.agent_action import AgentAction
    rows = (
        AgentAction.query.filter_by(simulation_id=sim_id)
        .order_by(AgentAction.created_at.desc()).all()
    )
    out = {}
    for r in rows:
        if r.action_type not in out:
            out[r.action_type] = {
                'status': r.status,
                'completed_at': r.completed_at.isoformat() if r.completed_at else None,
            }
    return out


def agents_by_layer(sim):
    """Read-only L1–L5 agent map: selection state, Bayesian score, last outcome (FR-SMV-03).

    Built directly from the registry + sim.selected_agents + posteriors so no Claude
    call or cache write is triggered by an SME view (keeps the session truly read-only).
    Structure and scores are returned for ALL layers, including L5 — the gate in §3
    applies only to artifact CONTENTS, handled separately in artifacts_for_sim().
    """
    from app.services.agent_registry import get_all_agents
    from app.services.agent_selector import (
        LAYER_NAMES, LAYER_DESCRIPTIONS, ROOT_AGENT_ID, TRIGGERED_AGENT_IDS,
    )

    selected = set(sim.selected_agents or [])
    scores = _bayesian_scores_for_sim(sim.id)
    outcomes = _last_outcome_for_sim(sim.id)
    agents = get_all_agents()

    layers = []
    for n in range(1, 6):
        tiles = []
        for a in [x for x in agents if x['layer'] == n]:
            at = a['action_type']
            tiles.append({
                'action_type': at,
                'label': a['label'],
                'selected': at in selected,
                'is_root': at == ROOT_AGENT_ID,
                'is_triggered': at in TRIGGERED_AGENT_IDS,
                'bayesian_score': scores.get(at),
                'last_outcome': outcomes.get(at),
            })
        layers.append({
            'layer_number': n,
            'name': LAYER_NAMES[n],
            'description': LAYER_DESCRIPTIONS[n],
            'selected_count': sum(1 for t in tiles if t['selected']),
            'total_count': len(tiles),
            'agents': tiles,
        })
    return layers


def signal_pipeline(user_id, limit=40):
    """The user's live signals: replies, bookings, payments, documents (FR-SMV-03)."""
    from app.models.integration_signal import IntegrationSignal
    from sqlalchemy import func
    rows = (
        IntegrationSignal.query.filter_by(user_id=user_id)
        .order_by(IntegrationSignal.created_at.desc()).limit(limit).all()
    )
    counts = dict(
        db.session.query(IntegrationSignal.signal_type, func.count(IntegrationSignal.id))
        .filter_by(user_id=user_id).group_by(IntegrationSignal.signal_type).all()
    )
    return {
        'counts': counts,
        'recent': [s.to_dict() for s in rows],
    }


def artifacts_for_sim(sim, sme):
    """Agent-produced deliverables for quality review, with the Layer-5 gate (FR-SMV-04).

    The gate is enforced HERE (server-side): for a non-qualifying SME, L5 artifact
    contents are replaced with the placeholder and never leave the server. Structure
    (which agents ran, when) is still returned so the SME understands the full system.
    """
    from app.models.agent_action import AgentAction
    can_l5 = sme.can_view_l5
    rows = (
        AgentAction.query.filter_by(simulation_id=sim.id, status=AgentAction.STATUS_COMPLETE)
        .order_by(AgentAction.completed_at.desc()).all()
    )
    out = []
    for r in rows:
        gated = (r.layer_number == L5_LAYER) and not can_l5
        out.append({
            'id': r.id,
            'action_type': r.action_type,
            'layer_number': r.layer_number,
            'completed_at': r.completed_at.isoformat() if r.completed_at else None,
            'artifact': L5_PLACEHOLDER if gated else r.artifact,
            'gated': gated,
        })
    return out


def simulation_roster(user_id):
    """All of the user's simulations with phase, cycle count, headline income, last activity."""
    from app.models.simulation import Simulation
    from app.models.layer6 import Layer6Outcome, Layer6Cycle
    from sqlalchemy import func

    sims = Simulation.query.filter_by(user_id=user_id).order_by(Simulation.created_at.desc()).all()
    roster = []
    for s in sims:
        income = float(
            db.session.query(func.sum(Layer6Outcome.actual_income))
            .filter_by(simulation_id=s.id).scalar() or 0
        )
        cycles = db.session.query(func.max(Layer6Cycle.cycle_number)).filter_by(
            simulation_id=s.id).scalar() or 0
        last_ts = db.session.query(func.max(Layer6Cycle.cycle_completed_at)).filter_by(
            simulation_id=s.id).scalar()
        roster.append({
            'id': s.id,
            'name': s.name,
            'expertise_zone': s.expertise_zone,
            'lifecycle_phase': s.lifecycle_phase,
            'status': s.status,
            'cycle_count': int(cycles),
            'headline_income': round(income, 2),
            'last_activity': last_ts.isoformat() if last_ts else None,
        })
    return roster


def user_overview(sme, profile):
    """Assemble everything the SME sees for one assigned user (FR-SMV-03/04)."""
    u = User.query.get(profile.user_id)
    recs = (
        SmeRecommendation.query.filter_by(sme_id=sme.id, user_id=profile.user_id)
        .order_by(SmeRecommendation.created_at.desc()).all()
    )
    return {
        'user': {
            'user_id': profile.user_id,
            'display_name': (profile.display_name or (u.full_name if u else None) or (u.email if u else '')),
            'username': profile.username,
            'primary_zone': profile.primary_zone,
            'canonical_zones': profile.canonical_zones,
        },
        'roster': simulation_roster(profile.user_id),
        'recommendation_history': [r.to_dict() for r in recs],
        'can_view_l5': sme.can_view_l5,
    }


# ── Recommendation loop (FR-SMV-05/06) ────────────────────────────────────────

def create_recommendation(sme, profile, rec_type, rationale, payload=None,
                          simulation_id=None, notify=True):
    """Issue a typed recommendation to an assigned user. Returns the SmeRecommendation."""
    if rec_type not in SmeRecommendation.ALL_TYPES:
        raise ValueError(f'Unknown recommendation type: {rec_type}')
    rationale = (rationale or '').strip()
    if not rationale:
        raise ValueError('A rationale is required')

    rec = SmeRecommendation(
        id=generate_id(),
        sme_id=sme.id,
        user_id=profile.user_id,
        simulation_id=simulation_id,
        type=rec_type,
        rationale=rationale,
        status=SmeRecommendation.STATUS_PENDING,
        expires_at=datetime.utcnow() + timedelta(days=REC_EXPIRY_DAYS),
    )
    rec.payload = payload or {}
    db.session.add(rec)
    log_access(sme.id, 'issue_recommendation', user_id=profile.user_id,
               detail={'type': rec_type, 'rec_id': rec.id}, commit=False)
    db.session.commit()

    if notify:
        _notify_recommendation(rec, sme)
    return rec


def pending_recommendations(user_id, simulation_id=None):
    """Pending recommendations for a user's GCC (optionally scoped to one simulation)."""
    q = SmeRecommendation.query.filter_by(
        user_id=user_id, status=SmeRecommendation.STATUS_PENDING,
    )
    if simulation_id:
        # Include sim-scoped recs and un-scoped (account-level) ones.
        q = q.filter(
            (SmeRecommendation.simulation_id == simulation_id)
            | (SmeRecommendation.simulation_id.is_(None))
        )
    return q.order_by(SmeRecommendation.created_at.desc()).all()


def mark_seen(user_id, rec_ids=None):
    """Mark pending recommendations as seen (unread triage closes for the SME)."""
    q = SmeRecommendation.query.filter_by(user_id=user_id, seen_at=None)
    if rec_ids:
        q = q.filter(SmeRecommendation.id.in_(rec_ids))
    now = datetime.utcnow()
    n = q.update({'seen_at': now}, synchronize_session=False)
    db.session.commit()
    return n


def apply_recommendation(rec, user):
    """Apply a one-click recommendation — the USER is the actor (§4, FR-SMV-06).

    Only the user the recommendation is for may apply it. The change is performed under
    the user's own action; the SME is credited as recommender in the audit trail.
    Returns (ok, message).
    """
    if rec.user_id != user.id:
        return False, 'This recommendation is not yours to apply.'
    if rec.status != SmeRecommendation.STATUS_PENDING:
        return False, f'Recommendation already {rec.status}.'
    if rec.type not in SmeRecommendation.ONE_CLICK_TYPES:
        return False, 'This recommendation type is advisory only.'

    from app.models.simulation import Simulation
    payload = rec.payload or {}
    sim = None
    if rec.simulation_id:
        sim = Simulation.query.filter_by(id=rec.simulation_id, user_id=user.id).first()

    if rec.type in (SmeRecommendation.TYPE_SWAP_AGENT,
                    SmeRecommendation.TYPE_ADD_AGENT,
                    SmeRecommendation.TYPE_REMOVE_AGENT):
        if not sim:
            return False, 'The related simulation could not be found.'
        ok, msg = _apply_agent_change(sim, rec.type, payload)
        if not ok:
            return False, msg
    elif rec.type == SmeRecommendation.TYPE_ADJUST_RATE:
        # A rate is a value the user confirms; we record acceptance. The user is
        # directed to the rate editor with the value pre-filled (payload carries it).
        # No silent write to income figures — acceptance is the audit record.
        pass

    rec.status = SmeRecommendation.STATUS_APPLIED
    rec.resolved_at = datetime.utcnow()
    rec.resolved_by = user.id
    if rec.seen_at is None:
        rec.seen_at = rec.resolved_at

    from app.models.audit_log import AuditLog
    # actor = user; recommender = SME (separated per FR-SMV-10)
    AuditLog.log('recommendation_applied', user_id=user.id, resource_id=rec.id,
                 metadata={'recommender_sme_id': rec.sme_id, 'type': rec.type,
                           'simulation_id': rec.simulation_id})
    log_access(rec.sme_id, 'recommendation_applied', user_id=user.id,
               detail={'rec_id': rec.id, 'type': rec.type}, commit=False)
    db.session.commit()
    return True, 'Applied.'


def _apply_agent_change(sim, rec_type, payload):
    """Mutate sim.selected_agents for swap/add/remove. Enforces selector invariants."""
    from app.services.agent_selector import ROOT_AGENT_ID, TRIGGERED_AGENT_IDS
    from app.services.agent_registry import get_all_agents

    valid = {a['action_type'] for a in get_all_agents()}
    selected = list(sim.selected_agents or [])

    frm = payload.get('from')
    to = payload.get('to') or payload.get('action_type')

    if rec_type == SmeRecommendation.TYPE_REMOVE_AGENT:
        target = frm or to
        if not target:
            return False, 'No agent specified to remove.'
        if target == ROOT_AGENT_ID:
            return False, 'The rate card cannot be removed.'
        selected = [a for a in selected if a != target]
    else:
        if not to or to not in valid:
            return False, 'The recommended agent is not recognised.'
        if rec_type == SmeRecommendation.TYPE_SWAP_AGENT and frm:
            selected = [a for a in selected if a != frm]
        if to not in selected:
            selected.append(to)

    # Re-assert invariants: root always on, triggered always included.
    if ROOT_AGENT_ID not in selected:
        selected.insert(0, ROOT_AGENT_ID)
    for t in TRIGGERED_AGENT_IDS:
        if t not in selected:
            selected.append(t)

    sim.selected_agents = selected
    return True, 'ok'


def dismiss_recommendation(rec, user, reason=None):
    if rec.user_id != user.id:
        return False, 'Not yours to dismiss.'
    if rec.status != SmeRecommendation.STATUS_PENDING:
        return False, f'Recommendation already {rec.status}.'
    rec.status = SmeRecommendation.STATUS_DISMISSED
    rec.dismiss_reason = (reason or '').strip() or None
    rec.resolved_at = datetime.utcnow()
    rec.resolved_by = user.id
    if rec.seen_at is None:
        rec.seen_at = rec.resolved_at
    log_access(rec.sme_id, 'recommendation_dismissed', user_id=user.id,
               detail={'rec_id': rec.id}, commit=False)
    db.session.commit()
    return True, 'Dismissed.'


def expire_stale_recommendations():
    """Auto-expire pending recommendations past their window (§4). Returns count."""
    now = datetime.utcnow()
    stale = SmeRecommendation.query.filter(
        SmeRecommendation.status == SmeRecommendation.STATUS_PENDING,
        SmeRecommendation.expires_at.isnot(None),
        SmeRecommendation.expires_at < now,
    ).all()
    for rec in stale:
        rec.status = SmeRecommendation.STATUS_EXPIRED
        rec.resolved_at = now
    if stale:
        db.session.commit()
    return len(stale)


# ── Transparency card (FR-SMV-07) ─────────────────────────────────────────────

def expert_card(profile):
    """The 'Your Simi Expert' card data, sourced entirely from the simi_smes record.

    Returns None when the user has no assigned SME — visibility and the card are
    coupled (§5 'No silent observation').
    """
    if not profile or not profile.sme_id:
        return None
    sme = SimiSME.query.get(profile.sme_id)
    if not sme:
        return None
    zones = sme.zones
    return {
        'sme_id': sme.id,
        'name': sme.full_name,
        'email': sme.email,
        'phone': sme.phone,
        'bio_url': sme.bio_url,
        'zone': zones[0] if zones else None,
        'zones': zones,
    }


# ── Opt-out / request-different / re-opt-in (FR-SMV-08) ───────────────────────

def opt_out(profile):
    """Sever the assignment immediately; block auto re-match; cancel pending recs (§6).

    opt_out = 'no SME at all, do not re-match.'
    """
    from app.services import sme_service
    prev_sme_id = profile.sme_id

    # Cancel pending recommendations from the current SME.
    if prev_sme_id:
        pending = SmeRecommendation.query.filter_by(
            sme_id=prev_sme_id, user_id=profile.user_id,
            status=SmeRecommendation.STATUS_PENDING,
        ).all()
        for r in pending:
            r.status = SmeRecommendation.STATUS_EXPIRED
            r.resolved_at = datetime.utcnow()

    profile.sme_id = None
    profile.sme_assignment_type = None
    profile.needs_reassignment = False
    profile.sme_opted_out = True

    if prev_sme_id:
        sme_service._resync_counts(prev_sme_id, commit=False)
        log_access(prev_sme_id, 'user_opted_out', user_id=profile.user_id, commit=False)
    db.session.commit()
    return True


def request_different(profile):
    """Softer path: keep current SME, flag for admin reassignment (§6).

    request-different = 'I want an SME, just not this one.'
    """
    if not profile.sme_id:
        return False
    profile.needs_reassignment = True
    log_access(profile.sme_id, 'user_requested_different', user_id=profile.user_id, commit=False)
    db.session.commit()
    return True


def opt_in(profile):
    """Re-opt-in: clear the declined flag and run auto-match (§6)."""
    from app.services import sme_service
    profile.sme_opted_out = False
    sme = sme_service.auto_assign_sme(profile, commit=False)
    db.session.commit()
    if sme:
        notify_assignment(profile, sme)
    return sme


# ── Notifications (FR-SMV-07) ─────────────────────────────────────────────────

def notify_assignment(profile, sme):
    """Notify the user they've been matched with an SME (§5)."""
    try:
        from app.services.notification_service import send_notification
        zone = (sme.zones[0] if sme.zones else 'subject-matter')
        send_notification(
            user_id=profile.user_id,
            notification_type='sme_assignment',
            title=f"You've been matched with {sme.full_name}",
            body=(f"You've been matched with {sme.full_name}, a {zone} expert who can "
                  f"review your simulation and suggest improvements. You can view their "
                  f"details, or opt out, any time in Settings."),
            cta_url='/settings/profile#simi-expert',
            cta_label='View your expert →',
            priority='normal',
        )
    except Exception as exc:
        logger.warning('notify_assignment failed user=%s: %s', profile.user_id, exc)


def _notify_recommendation(rec, sme):
    try:
        from app.services.notification_service import send_notification
        labels = {
            SmeRecommendation.TYPE_SWAP_AGENT: 'suggested swapping an agent',
            SmeRecommendation.TYPE_ADD_AGENT: 'suggested adding an agent',
            SmeRecommendation.TYPE_REMOVE_AGENT: 'suggested removing an agent',
            SmeRecommendation.TYPE_ADJUST_RATE: 'suggested a rate change',
            SmeRecommendation.TYPE_REVISE_ARTIFACT: 'suggested an edit to one of your artifacts',
            SmeRecommendation.TYPE_NOTE: 'left you a note',
        }
        what = labels.get(rec.type, 'sent you a recommendation')
        cta = f'/simulations/{rec.simulation_id}/gcc' if rec.simulation_id else '/dashboard'
        send_notification(
            user_id=rec.user_id,
            notification_type='sme_recommendation',
            title=f'{sme.full_name} {what}',
            body=rec.rationale[:400],
            cta_url=cta,
            cta_label='Review it →',
            simulation_id=rec.simulation_id,
            priority='normal',
        )
    except Exception as exc:
        logger.warning('notify_recommendation failed rec=%s: %s', rec.id, exc)
