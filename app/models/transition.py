"""Employer Transition Package (PRD-SIM-TRANS-001, phase 1).

The employer is an existing organization (corporate_accounts, org_type
'transition'). These tables hold its layoff cohorts, the seats it bought, the
coaching attached to a seat and the outcomes employees choose to report.

Privacy boundary: nothing here references an employee's simulations, contacts
or artifacts, and the employer report reads only counts. Coaching notes are
encrypted and readable only by the employee and their coach.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


class TransitionCohort(db.Model):
    """One layoff event for one employer."""
    __tablename__ = 'transition_cohorts'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    org_id = db.Column(db.String(9), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    last_working_day = db.Column(db.Date, nullable=True)
    invite_date = db.Column(db.Date, nullable=True)            # invites go out on/after this day
    _employer_domains = db.Column('employer_domains', db.Text, nullable=True)   # JSON list
    min_report_size = db.Column(db.Integer, nullable=True)     # NULL = platform default
    created_by = db.Column(db.String(9), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def employer_domains(self) -> list:
        return json.loads(self._employer_domains) if self._employer_domains else []

    @employer_domains.setter
    def employer_domains(self, value):
        clean = sorted({(d or '').strip().lower().lstrip('@') for d in (value or []) if (d or '').strip()})
        self._employer_domains = json.dumps(clean) if clean else None

    def to_dict(self):
        return {'id': self.id, 'org_id': self.org_id, 'name': self.name,
                'last_working_day': self.last_working_day.isoformat() if self.last_working_day else None,
                'invite_date': self.invite_date.isoformat() if self.invite_date else None,
                'employer_domains': self.employer_domains, 'min_report_size': self.min_report_size,
                'created_at': self.created_at.isoformat()}


class TransitionSeat(db.Model):
    """One seat: a tier, a lifecycle, and (once activated) one employee.

    unassigned -> invited -> activated -> expired; unassigned/invited -> revoked.
    An activated seat can't be revoked: the employee keeps the full access period.
    """
    __tablename__ = 'transition_seats'
    __table_args__ = (db.Index('ix_seats_cohort', 'cohort_id', 'status'),
                      db.Index('ix_seats_user', 'user_id', 'status'))

    UNASSIGNED = 'unassigned'
    INVITED = 'invited'
    ACTIVATED = 'activated'
    EXPIRED = 'expired'
    REVOKED = 'revoked'
    STATUSES = (UNASSIGNED, INVITED, ACTIVATED, EXPIRED, REVOKED)

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    cohort_id = db.Column(db.String(9), nullable=False)
    org_id = db.Column(db.String(9), nullable=False, index=True)
    tier = db.Column(db.String(12), nullable=False)
    status = db.Column(db.String(12), nullable=False, default=UNASSIGNED)
    full_name = db.Column(db.String(200), nullable=True)       # from HR's roster
    invite_email_enc = db.Column(db.Text, nullable=True)       # personal email, encrypted
    invite_email_hash = db.Column(db.String(64), nullable=True, index=True)
    invite_token = db.Column(db.String(64), nullable=True, unique=True)
    invite_sent_at = db.Column(db.DateTime, nullable=True)
    reminder_count = db.Column(db.Integer, nullable=False, default=0)
    last_reminded_at = db.Column(db.DateTime, nullable=True)
    user_id = db.Column(db.String(9), nullable=True)
    invited_at = db.Column(db.DateTime, nullable=True)
    activated_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    # Entitlements, copied from the tier at activation so a later tier edit
    # never changes what an employee was given.
    access_months = db.Column(db.Integer, nullable=True)
    sims_allowed = db.Column(db.Integer, nullable=False, default=0)
    sims_used = db.Column(db.Integer, nullable=False, default=0)
    sessions_total = db.Column(db.Integer, nullable=False, default=0)
    sessions_used = db.Column(db.Integer, nullable=False, default=0)
    no_shows = db.Column(db.Integer, nullable=False, default=0)
    # Employee choices
    consent_at = db.Column(db.DateTime, nullable=True)         # saw what the employer sees
    share_outcomes = db.Column(db.Boolean, nullable=False, default=False)
    confidentiality_ack_at = db.Column(db.DateTime, nullable=True)
    coach_id = db.Column(db.String(9), nullable=True)          # simi_smes.id
    coach_changes = db.Column(db.Integer, nullable=False, default=0)
    coach_access = db.Column(db.Boolean, nullable=False, default=False)   # coach may see sim summary + bio
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def is_live(self) -> bool:
        return (self.status == self.ACTIVATED and self.expires_at is not None
                and self.expires_at > datetime.utcnow())


class CoachingSession(db.Model):
    __tablename__ = 'coaching_sessions'

    BOOKED, HELD, NO_SHOW, CANCELLED = 'booked', 'held', 'no_show', 'cancelled'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    seat_id = db.Column(db.String(9), nullable=False, index=True)
    coach_id = db.Column(db.String(9), nullable=False, index=True)
    scheduled_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(10), nullable=False, default=BOOKED)
    used_credit = db.Column(db.Boolean, nullable=False, default=False)
    rating = db.Column(db.Integer, nullable=True)              # 1-5, by the employee
    pay_cents = db.Column(db.Integer, nullable=True)           # rate at completion (Admin > Pricing)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'seat_id': self.seat_id, 'coach_id': self.coach_id,
                'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
                'status': self.status, 'used_credit': self.used_credit, 'rating': self.rating}


class CoachingNote(db.Model):
    """Private to the employee and the coach. Encrypted at rest; no admin or
    employer route reads it."""
    __tablename__ = 'coaching_notes'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    session_id = db.Column(db.String(9), nullable=False, index=True)
    author = db.Column(db.String(10), nullable=False)          # employee | coach
    body_enc = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class TransitionOutcome(db.Model):
    """What the employee says happened. Counted for the employer only if
    share_with_employer, and only in aggregate."""
    __tablename__ = 'transition_outcomes'

    OUTCOMES = ('new_role', 'consulting', 'venture', 'education', 'searching', 'other')

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    seat_id = db.Column(db.String(9), nullable=False, index=True)
    outcome = db.Column(db.String(12), nullable=False)
    share_with_employer = db.Column(db.Boolean, nullable=False, default=False)
    reported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
