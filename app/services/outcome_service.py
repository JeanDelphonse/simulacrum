"""Outcome check-ins at day 30, 90 and 180 (COMPLIANCE_PLAN A6).

They exist so SimiSI can one day make honest, data-backed claims. Until counsel
approves how the data may be presented, nothing public reads it: only the
member's own dashboard prompt and aggregate counts in Admin.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.extensions import db
from app.models.compliance import OutcomeCheckin

INCOME_LABELS = {
    '0': 'Nothing yet', 'under_1k': 'Under $1,000', '1k_5k': '$1,000–$5,000',
    '5k_20k': '$5,000–$20,000', '20k_plus': 'Over $20,000', 'prefer_not': "I'd rather not say",
}


def due(user_id: str, now: datetime | None = None):
    """The earliest check-in the member hasn't answered or dismissed, or None.
    Counts from the day a completed simulation was created."""
    from app.models.simulation import Simulation
    now = now or datetime.utcnow()
    sims = Simulation.query.filter_by(user_id=user_id, status='complete').order_by(Simulation.created_at).all()
    if not sims:
        return None
    done = {(c.simulation_id, c.checkin_day) for c in OutcomeCheckin.query.filter_by(user_id=user_id).all()}
    for sim in sims:
        for day in OutcomeCheckin.DAYS:
            if sim.created_at and sim.created_at + timedelta(days=day) <= now and (sim.id, day) not in done:
                # A later day supersedes an earlier one the member missed.
                later = [d for d in OutcomeCheckin.DAYS if d > day
                         and sim.created_at + timedelta(days=d) <= now and (sim.id, d) not in done]
                return {'simulation_id': sim.id, 'simulation_name': sim.name, 'day': later[-1] if later else day}
    return None


def record(user_id: str, data: dict) -> OutcomeCheckin:
    from app.models.simulation import Simulation
    sim = Simulation.query.get(data.get('simulation_id') or '')
    if not sim or sim.user_id != user_id:
        raise ValueError('Unknown simulation.')
    day = int(data.get('day') or 0)
    if day not in OutcomeCheckin.DAYS:
        raise ValueError('Unknown check-in day.')
    band = data.get('income_gained_range') or None
    if band is not None and band not in OutcomeCheckin.INCOME_BANDS:
        raise ValueError('Unknown income range.')

    def yn(key):
        v = data.get(key)
        return None if v in (None, '') else bool(v)

    row = OutcomeCheckin.query.filter_by(simulation_id=sim.id, checkin_day=day).first()
    if not row:
        row = OutcomeCheckin(user_id=user_id, simulation_id=sim.id, checkin_day=day)
        db.session.add(row)
    if data.get('dismiss'):
        row.dismissed = True
    else:
        row.dismissed = False
        row.income_gained_range = band
        row.first_client, row.job_landed = yn('first_client'), yn('job_landed')
        row.notes = (data.get('notes') or '').strip()[:1000] or None
    # Earlier days the member skipped past are closed too, so they aren't asked again.
    for d in OutcomeCheckin.DAYS:
        if d < day and not OutcomeCheckin.query.filter_by(simulation_id=sim.id, checkin_day=d).first():
            db.session.add(OutcomeCheckin(user_id=user_id, simulation_id=sim.id, checkin_day=d, dismissed=True))
    db.session.commit()
    return row


def admin_summary() -> dict:
    """Aggregate counts only: no member, simulation or note is shown."""
    rows = OutcomeCheckin.query.filter_by(dismissed=False).all()
    out = {'answered': len(rows), 'dismissed': OutcomeCheckin.query.filter_by(dismissed=True).count(), 'by_day': {}}
    for d in OutcomeCheckin.DAYS:
        day_rows = [r for r in rows if r.checkin_day == d]
        out['by_day'][d] = {
            'answered': len(day_rows),
            'first_client': sum(1 for r in day_rows if r.first_client),
            'job_landed': sum(1 for r in day_rows if r.job_landed),
            'income': {k: sum(1 for r in day_rows if r.income_gained_range == k) for k in OutcomeCheckin.INCOME_BANDS},
        }
    return out
