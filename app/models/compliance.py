"""Compliance risk reduction (COMPLIANCE_PLAN.md, 2026-11).

LegalText        the one source for disclaimer and disclosure wording. Every
                 row starts as a counsel DRAFT; only an admin can mark it
                 counsel-approved (never code).
OutcomeCheckin   day-30/90/180 self-reported outcomes. Internal only: nothing
                 public reads this table until counsel approves how it is shown.
ComplianceEvent  a Layer 5 output that broke the education-only rules.

Primary keys use utils.id_gen.generate_pk() (9-char uppercase).
"""
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_pk


class LegalText(db.Model):
    __tablename__ = 'legal_texts'

    STATUS_DRAFT = 'draft'
    STATUS_APPROVED = 'counsel_approved'

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    key = db.Column(db.String(60), nullable=False, unique=True)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=STATUS_DRAFT)
    version = db.Column(db.Integer, nullable=False, default=1)
    updated_by = db.Column(db.String(9), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class OutcomeCheckin(db.Model):
    __tablename__ = 'outcome_checkins'
    __table_args__ = (db.UniqueConstraint('simulation_id', 'checkin_day', name='uq_outcome_checkin_day'),)

    DAYS = (30, 90, 180)
    INCOME_BANDS = ('0', 'under_1k', '1k_5k', '5k_20k', '20k_plus', 'prefer_not')

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    user_id = db.Column(db.String(9), nullable=False, index=True)
    simulation_id = db.Column(db.String(9), nullable=False)
    checkin_day = db.Column(db.Integer, nullable=False)
    income_gained_range = db.Column(db.String(20), nullable=True)
    first_client = db.Column(db.Boolean, nullable=True)
    job_landed = db.Column(db.Boolean, nullable=True)
    notes = db.Column(db.String(1000), nullable=True)
    dismissed = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ComplianceEvent(db.Model):
    __tablename__ = 'compliance_events'

    id = db.Column(db.String(9), primary_key=True, default=generate_pk)
    user_id = db.Column(db.String(9), nullable=True, index=True)
    agent = db.Column(db.String(60), nullable=False)
    rule = db.Column(db.String(60), nullable=False)
    excerpt = db.Column(db.String(300), nullable=True)
    outcome = db.Column(db.String(20), nullable=True)   # regenerated | fallback
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
