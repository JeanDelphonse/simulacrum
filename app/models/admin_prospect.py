"""SIM-PRD-CRM-001 — Admin Outreach Pipeline models.

The founder's own sales pipeline: firms Simulacrum is selling TO. Deliberately
separate from the per-user lead CRM (app/models/contact.py) and the per-org
shared CRM — this is admin-only and never surfaced to users, SMEs or org admins.
"""
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


class AdminProspect(db.Model):
    """A firm the founder is pursuing (FR-CRM-01, FR-CRM-02)."""

    __tablename__ = 'admin_prospects'

    # Pipeline stages, in order. The sequence mirrors the 3-touch LinkedIn motion
    # rather than a generic CRM funnel, so the tool matches what the founder runs.
    STAGE_NOT_STARTED = 'not_started'
    STAGE_RESEARCHED = 'researched'
    STAGE_TOUCH_1_SENT = 'touch_1_sent'
    STAGE_CONNECTED = 'connected'
    STAGE_TOUCH_2_SENT = 'touch_2_sent'
    STAGE_REPLIED = 'replied'
    STAGE_MEETING_BOOKED = 'meeting_booked'
    STAGE_ONBOARDED = 'onboarded'
    STAGE_PASSED = 'passed'

    STAGES = (
        STAGE_NOT_STARTED, STAGE_RESEARCHED, STAGE_TOUCH_1_SENT, STAGE_CONNECTED,
        STAGE_TOUCH_2_SENT, STAGE_REPLIED, STAGE_MEETING_BOOKED, STAGE_ONBOARDED,
        STAGE_PASSED,
    )

    STAGE_LABELS = {
        STAGE_NOT_STARTED: 'Not started',
        STAGE_RESEARCHED: 'Researched',
        STAGE_TOUCH_1_SENT: 'Touch 1 sent',
        STAGE_CONNECTED: 'Connected',
        STAGE_TOUCH_2_SENT: 'Touch 2 sent',
        STAGE_REPLIED: 'Replied',
        STAGE_MEETING_BOOKED: 'Meeting booked',
        STAGE_ONBOARDED: 'Onboarded',
        STAGE_PASSED: 'Passed',
    }

    # Stages that are no longer in flight — excluded from the due queue and the
    # 'active' counter, and never chased by the morning briefing.
    TERMINAL_STAGES = (STAGE_ONBOARDED, STAGE_PASSED)

    FIT_HIGH = 'high'
    FIT_MEDIUM = 'medium'
    FIT_LOW = 'low'
    FITS = (FIT_HIGH, FIT_MEDIUM, FIT_LOW)

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    firm_name = db.Column(db.String(200), nullable=False, index=True)
    lead_name = db.Column(db.String(160), nullable=True)
    lead_linkedin = db.Column(db.String(300), nullable=True)
    website = db.Column(db.String(300), nullable=True)
    contact_path = db.Column(db.String(200), nullable=True)   # linkedin | form | email
    fit = db.Column(db.String(10), nullable=False, default=FIT_MEDIUM)
    category = db.Column(db.String(80), nullable=True, index=True)
    stage = db.Column(db.String(30), nullable=False, default=STAGE_NOT_STARTED, index=True)
    last_contact = db.Column(db.Date, nullable=True)
    next_followup = db.Column(db.Date, nullable=True, index=True)
    notes = db.Column(db.Text, nullable=True)
    # Set when the deal is won and handed to ORG-001 provisioning (FR-CRM-07).
    won_org_id = db.Column(
        db.String(9), db.ForeignKey('corporate_accounts.id', ondelete='SET NULL'),
        nullable=True, index=True,
    )
    passed_reason = db.Column(db.String(200), nullable=True)
    retouch_on = db.Column(db.Date, nullable=True)
    # SIM-PRD-CRM-002 FR-DSC-03 — why discovery surfaced this firm. Kept separate
    # from notes so it can be shown on the queue cards without being tangled up
    # with the founder's own notes. discovery_fit records the fit at discovery
    # time, so it survives the founder later editing `fit` by hand.
    discovery_rationale = db.Column(db.Text, nullable=True)
    discovery_fit = db.Column(db.String(10), nullable=True)
    # Cached draft from the last briefing, so opening the tab does not re-bill a
    # Claude call for every prospect on every page load.
    draft_text = db.Column(db.Text, nullable=True)
    draft_for_stage = db.Column(db.String(30), nullable=True)
    draft_generated_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    touches = db.relationship(
        'AdminProspectTouch', backref='prospect', lazy='dynamic',
        cascade='all, delete-orphan',
    )

    @property
    def stage_label(self) -> str:
        return self.STAGE_LABELS.get(self.stage, self.stage)

    @property
    def is_active(self) -> bool:
        return self.stage not in self.TERMINAL_STAGES

    def is_overdue(self, today=None) -> bool:
        """Follow-up date has passed and the prospect is still in flight (FR-CRM-05)."""
        if not self.next_followup or not self.is_active:
            return False
        from datetime import date as _date
        return self.next_followup < (today or _date.today())

    def is_due(self, today=None) -> bool:
        if not self.next_followup or not self.is_active:
            return False
        from datetime import date as _date
        return self.next_followup <= (today or _date.today())

    def to_dict(self, include_touches: bool = False) -> dict:
        data = {
            'id': self.id,
            'firm_name': self.firm_name,
            'lead_name': self.lead_name,
            'lead_linkedin': self.lead_linkedin,
            'website': self.website,
            'contact_path': self.contact_path,
            'fit': self.fit,
            'category': self.category,
            'stage': self.stage,
            'stage_label': self.stage_label,
            'last_contact': self.last_contact.isoformat() if self.last_contact else None,
            'next_followup': self.next_followup.isoformat() if self.next_followup else None,
            'notes': self.notes,
            'won_org_id': self.won_org_id,
            'passed_reason': self.passed_reason,
            'retouch_on': self.retouch_on.isoformat() if self.retouch_on else None,
            'discovery_rationale': self.discovery_rationale,
            'discovery_fit': self.discovery_fit,
            'draft_text': self.draft_text,
            'draft_for_stage': self.draft_for_stage,
            'is_active': self.is_active,
            'is_due': self.is_due(),
            'is_overdue': self.is_overdue(),
            'touch_count': self.touches.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if include_touches:
            data['touch_log'] = [
                t.to_dict() for t in
                self.touches.order_by(AdminProspectTouch.touched_at.desc()).all()
            ]
        return data


class AdminProspectTouch(db.Model):
    """Append-only log of every interaction with a prospect (FR-CRM-02).

    Integer surrogate key rather than the usual 9-char id: this is the highest
    volume table in the feature and rows are never addressed by URL.
    """

    __tablename__ = 'admin_prospect_touches'

    CHANNEL_LINKEDIN = 'linkedin'
    CHANNEL_EMAIL = 'email'
    CHANNEL_CALL = 'call'
    CHANNEL_NOTE = 'note'
    CHANNELS = (CHANNEL_LINKEDIN, CHANNEL_EMAIL, CHANNEL_CALL, CHANNEL_NOTE)

    BY_MANUAL = 'manual'
    BY_FOUNDER_OPS = 'founder_ops'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    prospect_id = db.Column(
        db.String(9), db.ForeignKey('admin_prospects.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    touched_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    channel = db.Column(db.String(30), nullable=False, default=CHANNEL_LINKEDIN)
    stage_at = db.Column(db.String(30), nullable=False)
    summary = db.Column(db.Text, nullable=True)
    drafted_by = db.Column(db.String(20), nullable=False, default=BY_MANUAL)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'prospect_id': self.prospect_id,
            'touched_at': self.touched_at.isoformat() if self.touched_at else None,
            'channel': self.channel,
            'stage_at': self.stage_at,
            'stage_at_label': AdminProspect.STAGE_LABELS.get(self.stage_at, self.stage_at),
            'summary': self.summary,
            'drafted_by': self.drafted_by,
        }


class AdminStageRule(db.Model):
    """Stage → follow-up interval and which touch to draft. Configurable per PRD §5.

    Seeded by sql/043_admin_crm.sql. admin_crm_service falls back to its built-in
    defaults for any stage missing a row, so a partially seeded table cannot break
    the pipeline.
    """

    __tablename__ = 'admin_stage_rules'

    stage = db.Column(db.String(30), primary_key=True)
    followup_days = db.Column(db.Integer, nullable=False, default=3)
    drafts_touch = db.Column(db.String(30), nullable=True)   # touch1 | touch2 | touch3 | research

    def to_dict(self) -> dict:
        return {
            'stage': self.stage,
            'followup_days': self.followup_days,
            'drafts_touch': self.drafts_touch,
        }
