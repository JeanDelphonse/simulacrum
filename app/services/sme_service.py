"""SIM-PRD-SME-001 — Simi SME Assignment service.

Pure business logic for:
  * canonical expertise-zone classification (FR-SME-04/05)
  * user-to-SME auto-matching, unassigned queue, rebalance (FR-SME-06/07/08)
  * coverage map + SME deactivation flagging (FR-SME-02/09)

Zone-matching is done in Python (candidate SMEs loaded by status, then filtered on their
JSON `zones` list) rather than with DB JSON operators, so it works on both SQLite (dev) and
MySQL (prod).

Manual-lock convention (no extra column needed):
  * AI classification  -> canonical_zones set, zones_computed_at = now
  * admin manual edit  -> canonical_zones set, zones_computed_at = None  (locked)
  A re-run with force=False skips a locked profile; "reset to AI" passes force=True.
"""
from __future__ import annotations  # PEP 604 unions (set | None) on Python < 3.10
import logging
from datetime import datetime

from app.extensions import db
from app.models.profile import UserProfile
from app.models.resume import Resume
from app.models.sme import SimiSME, ExpertiseCategory
from app.models.platform_settings import PlatformSetting

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.50


# ── Taxonomy ────────────────────────────────────────────────────────────────

def active_categories():
    """All active canonical categories, ordered for display."""
    return ExpertiseCategory.query.filter_by(is_active=True).order_by(
        ExpertiseCategory.sort_order, ExpertiseCategory.name,
    ).all()


def active_category_slugs():
    return [c.slug for c in active_categories()]


def zone_threshold():
    try:
        return float(PlatformSetting.get('sme_zone_threshold') or DEFAULT_THRESHOLD)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


# ── Classification (FR-SME-04/05) ─────────────────────────────────────────────

def _gather_expertise_text(profile: UserProfile):
    """Assemble the user's free-form expertise + a best-effort professional title."""
    title = (profile.tagline or profile.display_name or '').strip()
    parts = []
    resume = Resume.query.filter_by(user_id=profile.user_id).order_by(
        Resume.created_at.desc(),
    ).first()
    if resume and resume.expertise_zones:
        for z in resume.expertise_zones:
            if isinstance(z, dict):
                name = z.get('zone_name') or ''
                summ = z.get('summary') or ''
                parts.append(f'{name}. {summ}'.strip())
            elif isinstance(z, str):
                parts.append(z)
    if profile.bio:
        parts.append(profile.bio[:600])
    return title, '\n'.join(p for p in parts if p)[:4000]


def classify_user_zones(profile: UserProfile, force: bool = False, commit: bool = True):
    """Classify a user's expertise into canonical zones. Returns the assigned list.

    Respects manual locks unless force=True (reset-to-AI). Categories scoring >= threshold
    are assigned; the highest becomes primary; others are secondary.
    """
    from app.services.claude import classify_canonical_zones

    # Manual lock: canonical zones exist but were not AI-computed -> don't clobber.
    if not force and profile.canonical_zones and profile.zones_computed_at is None:
        return profile.canonical_zones

    title, expertise_text = _gather_expertise_text(profile)
    slugs = active_category_slugs()
    if not slugs:
        return []

    scored = classify_canonical_zones(title, expertise_text, slugs, profile.user_id)
    threshold = zone_threshold()
    assigned = [z for z in scored if z['confidence'] >= threshold]
    for i, z in enumerate(assigned):
        z['is_primary'] = (i == 0)

    profile.canonical_zones = assigned
    profile.zones_computed_at = datetime.utcnow()
    if commit:
        db.session.commit()
    return assigned


# ── SME matching (FR-SME-06/07/08) ────────────────────────────────────────────

def recount_sme(sme: SimiSME):
    """Recompute assigned_count from the source of truth (profiles pointing at this SME)."""
    sme.assigned_count = UserProfile.query.filter_by(sme_id=sme.id).count()


def _candidates_for(primary_slug: str, allowed_ids: set | None = None):
    active = SimiSME.query.filter_by(status=SimiSME.STATUS_ACTIVE).all()
    cands = [s for s in active if primary_slug in s.zones]
    if allowed_ids is not None:
        cands = [s for s in cands if s.id in allowed_ids]
    return cands


def _org_pod_ids(profile: UserProfile) -> set | None:
    """SIM-PRD-ORG-001: the SME pod covering this member's org, or None.

    Returns None when the member has no org or the org has no pod (so matching
    falls back to the global pool).
    """
    org_id = getattr(profile, 'org_id', None)
    if not org_id:
        return None
    try:
        from app.models.corporate import OrgSmePod
        ids = {r[0] for r in OrgSmePod.query
               .filter_by(org_id=org_id)
               .with_entities(OrgSmePod.sme_id).all()}
        return ids or None
    except Exception:
        return None


def auto_assign_sme(profile: UserProfile, force_over_capacity: bool = False, commit: bool = True):
    """Auto-match a user to the best-fit Active SME covering their primary zone.

    Selection: most secondary-zone overlap, then most available capacity (headroom),
    then stable round-robin by id. Never overwrites a manual assignment. On no match the
    user lands in the Unassigned queue (sme_id = None). Returns the assigned SimiSME or None.
    """
    # SIM-PRD-SME-002 FR-SMV-08: a user who opted out is never auto-re-matched.
    if getattr(profile, 'sme_opted_out', False):
        return None

    # Manual pins are never overwritten by auto-matching (FR-SME-07).
    if profile.sme_assignment_type == 'manual' and profile.sme_id:
        return SimiSME.query.get(profile.sme_id)

    primary = profile.primary_zone
    prev_sme_id = profile.sme_id
    if not primary:
        profile.sme_id = None
        profile.sme_assignment_type = None
        profile.needs_reassignment = False
        _resync_counts(prev_sme_id, None, commit=False)
        if commit:
            db.session.commit()
        return None

    # SIM-PRD-ORG-001: constrain to the org's SME pod when one exists; fall back
    # to the global pool if no pod SME covers the member's primary zone.
    pod_ids = _org_pod_ids(profile)
    candidates = _candidates_for(primary, allowed_ids=pod_ids)
    if pod_ids and not candidates:
        candidates = _candidates_for(primary)
    if not force_over_capacity:
        with_room = [c for c in candidates if c.headroom > 0]
        # Only drop over-capacity SMEs if at least one has room; otherwise keep all
        # so a soft-capped zone still assigns rather than dumping to Unassigned.
        candidates = with_room or candidates

    if not candidates:
        profile.sme_id = None
        profile.sme_assignment_type = None
        profile.needs_reassignment = False
        _resync_counts(prev_sme_id, None, commit=False)
        if commit:
            db.session.commit()
        return None

    secondary = set(profile.secondary_zones)

    def score(sme):
        overlap = len(set(sme.zones) & secondary)
        return (overlap, sme.headroom, sme.id)

    best = max(candidates, key=score)
    profile.sme_id = best.id
    profile.sme_assignment_type = 'auto'
    profile.needs_reassignment = False
    _resync_counts(prev_sme_id, best.id, commit=False)
    if commit:
        db.session.commit()
    return best


def manual_assign_sme(profile: UserProfile, sme_id, commit: bool = True):
    """Admin override — pin a user to a specific SME (or clear with sme_id=None)."""
    prev = profile.sme_id
    if sme_id:
        sme = SimiSME.query.get(sme_id)
        if not sme:
            raise ValueError('SME not found')
        profile.sme_id = sme.id
        profile.sme_assignment_type = 'manual'
        profile.needs_reassignment = False
    else:
        profile.sme_id = None
        profile.sme_assignment_type = None
    _resync_counts(prev, profile.sme_id, commit=False)
    if commit:
        db.session.commit()
    return profile


def _resync_counts(*sme_ids, commit=True):
    for sid in {s for s in sme_ids if s}:
        sme = SimiSME.query.get(sid)
        if sme:
            recount_sme(sme)
    if commit:
        db.session.commit()


# ── Bulk operations ────────────────────────────────────────────────────────

def assign_unassigned():
    """FR-SME-08 — auto-match every user who has canonical zones but no SME. Returns count."""
    profiles = UserProfile.query.filter(
        UserProfile.sme_id.is_(None),
        UserProfile._canonical_zones.isnot(None),
        UserProfile.sme_opted_out.is_(False),  # FR-SMV-08: don't re-match opted-out users
    ).all()
    assigned = 0
    for p in profiles:
        if p.primary_zone and auto_assign_sme(p, commit=False):
            assigned += 1
    db.session.commit()
    return assigned


def rebalance_zone(slug: str):
    """FR-SME-08 — even out auto-assigned users across Active SMEs covering `slug`.

    Unlike auto-match (which fills by absolute headroom), rebalance distributes by
    *utilization ratio* so load is spread evenly relative to each SME's capacity.
    Only auto-assigned users whose primary zone is `slug` are moved; manual pins and
    other-zone users are left untouched. Returns the number of users actually moved.
    """
    smes = _candidates_for(slug)
    if len(smes) < 2:
        return 0

    movable = [
        p for p in UserProfile.query.filter(
            UserProfile.sme_assignment_type == 'auto',
            UserProfile._canonical_zones.isnot(None),
        ).all()
        if p.primary_zone == slug
    ]
    movable_ids = {p.user_id for p in movable}

    # Base load = users each SME holds that this pass will NOT redistribute.
    base = {}
    for s in smes:
        held = UserProfile.query.filter_by(sme_id=s.id).count()
        base[s.id] = held - sum(1 for p in movable if p.sme_id == s.id)
    working = dict(base)

    # Place the most-constrained users (most secondary overlap available) first.
    movable.sort(key=lambda p: -len(set(p.secondary_zones)))

    moved = 0
    for p in movable:
        secondary = set(p.secondary_zones)

        def score(s):
            cap = s.capacity or 1
            ratio_after = (working[s.id] + 1) / cap
            overlap = len(set(s.zones) & secondary)
            # lowest projected utilization first, then more overlap, then stable id
            return (ratio_after, -overlap, s.id)

        best = min(smes, key=score)
        working[best.id] += 1
        if best.id != p.sme_id:
            p.sme_id = best.id
            p.sme_assignment_type = 'auto'
            moved += 1

    for s in smes:
        recount_sme(s)
    db.session.commit()
    return moved


def flag_sme_users_for_reassignment(sme: SimiSME, commit: bool = True):
    """Mark every user assigned to `sme` as needing reassignment (deactivation / zone change)."""
    profiles = UserProfile.query.filter_by(sme_id=sme.id).all()
    for p in profiles:
        p.needs_reassignment = True
    if commit:
        db.session.commit()
    return len(profiles)


def reassign_sme_users(sme: SimiSME, commit: bool = True):
    """Bulk auto-rematch all users currently assigned to `sme` (e.g. after deactivation).

    Manual pins are converted to auto and rematched too, since the SME can no longer serve.
    Returns (rematched_count, unassigned_count).
    """
    profiles = UserProfile.query.filter_by(sme_id=sme.id).all()
    rematched = unassigned = 0
    for p in profiles:
        # clear the pin so auto-match is free to move them off the inactive SME
        p.sme_assignment_type = 'auto'
        p.sme_id = None
        new_sme = auto_assign_sme(p, commit=False)
        if new_sme:
            rematched += 1
        else:
            unassigned += 1
    recount_sme(sme)
    if commit:
        db.session.commit()
    return rematched, unassigned


# ── Coverage map (FR-SME-09) ──────────────────────────────────────────────────

def coverage_map():
    """Grid: each active category -> #active SMEs covering it, #users in that zone, ratio.

    A gap is a category with users but zero active SMEs.
    """
    cats = active_categories()
    active_smes = SimiSME.query.filter_by(status=SimiSME.STATUS_ACTIVE).all()

    # Count users per primary zone in one pass.
    users_per_zone = {}
    profiles = UserProfile.query.filter(UserProfile._canonical_zones.isnot(None)).all()
    for p in profiles:
        pz = p.primary_zone
        if pz:
            users_per_zone[pz] = users_per_zone.get(pz, 0) + 1

    rows = []
    for c in cats:
        sme_count = sum(1 for s in active_smes if c.slug in s.zones)
        user_count = users_per_zone.get(c.slug, 0)
        rows.append({
            'category': c.name,
            'slug': c.slug,
            'sme_count': sme_count,
            'user_count': user_count,
            'capacity': sum(s.capacity for s in active_smes if c.slug in s.zones),
            'is_gap': user_count > 0 and sme_count == 0,
        })
    return rows


def run_classification_and_assignment(profile: UserProfile, force: bool = False):
    """Convenience hook: classify a user's zones then auto-assign an SME. Commits once."""
    classify_user_zones(profile, force=force, commit=False)
    auto_assign_sme(profile, commit=False)
    db.session.commit()
    return profile
