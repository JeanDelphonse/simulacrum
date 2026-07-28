"""SIM-PRD-CRM-002 — Prospect Discovery Agent models.

Feeds the CRM-001 admin pipeline. A DiscoveryProfile is a reusable set of Apollo
firmographic filters; a DiscoveryCandidate is one company Apollo returned, scored
by Claude and routed either straight into admin_prospects or into a review queue.
"""
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


class DiscoveryProfile(db.Model):
    """A saved set of firmographic filters passed to Apollo (FR-DSC-01)."""

    __tablename__ = 'discovery_profiles'

    THRESHOLD_HIGH = 'high'   # default — only high-fit unflagged firms auto-save
    THRESHOLD_NONE = 'none'   # nothing auto-saves; review everything
    THRESHOLDS = (THRESHOLD_HIGH, THRESHOLD_NONE)

    SCHEDULE_WEEKLY = 'weekly'
    SCHEDULE_MONTHLY = 'monthly'
    SCHEDULES = (SCHEDULE_WEEKLY, SCHEDULE_MONTHLY)

    DEFAULT_BATCH_CAP = 50

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    name = db.Column(db.String(120), nullable=False)
    categories = db.Column(db.JSON, nullable=True)
    headcount_min = db.Column(db.Integer, nullable=False, default=5)
    headcount_max = db.Column(db.Integer, nullable=False, default=25)
    geography = db.Column(db.String(160), nullable=True)
    keywords_pos = db.Column(db.JSON, nullable=True)
    keywords_neg = db.Column(db.JSON, nullable=True)
    auto_save_threshold = db.Column(db.String(10), nullable=False, default=THRESHOLD_HIGH)
    batch_cap = db.Column(db.Integer, nullable=False, default=DEFAULT_BATCH_CAP)
    schedule = db.Column(db.String(20), nullable=True, index=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def employee_range(self) -> str:
        """Apollo expresses headcount filters as 'min,max' range strings."""
        return '{},{}'.format(self.headcount_min or 1, self.headcount_max or 1000)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'categories': self.categories or [],
            'headcount_min': self.headcount_min,
            'headcount_max': self.headcount_max,
            'geography': self.geography,
            'keywords_pos': self.keywords_pos or [],
            'keywords_neg': self.keywords_neg or [],
            'auto_save_threshold': self.auto_save_threshold,
            'batch_cap': self.batch_cap,
            'schedule': self.schedule,
            'last_run_at': self.last_run_at.isoformat() if self.last_run_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class DiscoveryCandidate(db.Model):
    """One company Apollo surfaced, with its fit score and routing decision.

    Rows are kept forever, including dismissals — the domain uniqueness
    constraint is the dedup memory that stops a firm being surfaced twice
    (FR-DSC-02, section 5 'Dedup + memory').
    """

    __tablename__ = 'discovery_candidates'

    FIT_HIGH = 'high'
    FIT_MEDIUM = 'medium'
    FIT_LOW = 'low'
    FITS = (FIT_HIGH, FIT_MEDIUM, FIT_LOW)

    ROUTE_AUTO_SAVE = 'auto_save'
    ROUTE_REVIEW = 'review_queue'

    STATUS_QUEUED = 'queued'
    STATUS_SAVED = 'saved'
    STATUS_DISMISSED = 'dismissed'

    # Guardrail flags. Any flag blocks auto-save regardless of apparent fit
    # (FR-DSC-04) — this is what catches an acquired firm before it costs a touch.
    FLAG_ACQUIRED = 'possibly_acquired'
    FLAG_TOO_LARGE = 'too_large'
    FLAG_TOO_SMALL = 'too_small'
    FLAG_OFF_CATEGORY = 'off_category'

    FLAG_LABELS = {
        FLAG_ACQUIRED: 'possibly acquired — verify',
        FLAG_TOO_LARGE: 'headcount above the boutique band',
        FLAG_TOO_SMALL: 'likely a solo shop',
        FLAG_OFF_CATEGORY: 'off-category',
    }

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    profile_id = db.Column(db.String(9), nullable=True, index=True)
    company = db.Column(db.String(200), nullable=False)
    domain = db.Column(db.String(200), nullable=False, unique=True)
    headcount = db.Column(db.Integer, nullable=True)
    industry = db.Column(db.String(120), nullable=True)
    location = db.Column(db.String(160), nullable=True)
    leader_name = db.Column(db.String(160), nullable=True)
    leader_linkedin = db.Column(db.String(300), nullable=True)
    signal = db.Column(db.Text, nullable=True)
    fit = db.Column(db.String(10), nullable=True)
    rationale = db.Column(db.Text, nullable=True)
    flags = db.Column(db.JSON, nullable=True)
    route = db.Column(db.String(20), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_QUEUED, index=True)
    prospect_id = db.Column(db.String(9), nullable=True, index=True)
    discovered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def flag_labels(self) -> list:
        return [self.FLAG_LABELS.get(f, f) for f in (self.flags or [])]

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'profile_id': self.profile_id,
            'company': self.company,
            'domain': self.domain,
            'headcount': self.headcount,
            'industry': self.industry,
            'location': self.location,
            'leader_name': self.leader_name,
            'leader_linkedin': self.leader_linkedin,
            'signal': self.signal,
            'fit': self.fit,
            'rationale': self.rationale,
            'flags': self.flags or [],
            'flag_labels': self.flag_labels,
            'route': self.route,
            'status': self.status,
            'prospect_id': self.prospect_id,
            'discovered_at': self.discovered_at.isoformat() if self.discovered_at else None,
        }
