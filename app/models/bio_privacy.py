"""
SIM-PRD-PRIVACY-001 — Bio Page Private Mode.

Models backing the LinkedIn-verified access-request → approval → warm-lead loop.
Per-user mode flags (privacy_mode / accepting_requests / request_notify) live on
UserProfile; these three tables hold the requests, the persistent grants, and the
allow/block rules.
"""
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


class BioAccessRequest(db.Model):
    """A LinkedIn-verified request to view a private bio page's gated content."""
    __tablename__ = 'bio_access_requests'

    STATUS_PENDING       = 'pending'
    STATUS_APPROVED      = 'approved'
    STATUS_AUTO_APPROVED = 'auto_approved'
    STATUS_REVOKED       = 'revoked'
    STATUS_EXPIRED       = 'expired'
    STATUS_BLOCKED       = 'blocked'

    id                 = db.Column(db.String(9), primary_key=True, default=generate_id)
    owner_user_id      = db.Column(db.String(9), nullable=False, index=True)
    requester_name     = db.Column(db.String(160), nullable=False)
    requester_linkedin = db.Column(db.String(300), nullable=False)   # identity anchor
    requester_company  = db.Column(db.String(200), nullable=True)
    requester_industry = db.Column(db.String(120), nullable=True)
    requester_email    = db.Column(db.String(160), nullable=True)
    requester_avatar   = db.Column(db.String(500), nullable=True)
    message            = db.Column(db.Text, nullable=True)
    status             = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    created_at         = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    resolved_at        = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('owner_user_id', 'requester_linkedin', name='uq_owner_requester'),
        db.Index('idx_bar_owner_status', 'owner_user_id', 'status'),
    )

    @property
    def is_pending(self) -> bool:
        return self.status == self.STATUS_PENDING

    @property
    def is_approved(self) -> bool:
        return self.status in (self.STATUS_APPROVED, self.STATUS_AUTO_APPROVED)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'requester_name': self.requester_name,
            'requester_linkedin': self.requester_linkedin,
            'requester_company': self.requester_company,
            'requester_industry': self.requester_industry,
            'requester_email': self.requester_email,
            'requester_avatar': self.requester_avatar,
            'message': self.message,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
        }


class BioAccessGrant(db.Model):
    """A person-to-page grant: this verified identity may see this owner's gated
    content until revoked. Persists across visits; source of truth for the gate."""
    __tablename__ = 'bio_access_grants'

    id                 = db.Column(db.String(9), primary_key=True, default=generate_id)
    owner_user_id      = db.Column(db.String(9), nullable=False, index=True)
    requester_linkedin = db.Column(db.String(300), nullable=False)
    requester_name     = db.Column(db.String(160), nullable=True)
    granted_at         = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    revoked_at         = db.Column(db.DateTime, nullable=True)
    last_viewed_at     = db.Column(db.DateTime, nullable=True)
    view_count         = db.Column(db.Integer, nullable=False, default=0)

    __table_args__ = (
        db.UniqueConstraint('owner_user_id', 'requester_linkedin', name='uq_grant'),
        db.Index('idx_bag_owner_active', 'owner_user_id', 'revoked_at'),
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'requester_linkedin': self.requester_linkedin,
            'requester_name': self.requester_name,
            'granted_at': self.granted_at.isoformat() if self.granted_at else None,
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
            'last_viewed_at': self.last_viewed_at.isoformat() if self.last_viewed_at else None,
            'view_count': self.view_count or 0,
            'is_active': self.is_active,
        }


class BioAccessRule(db.Model):
    """Allow/block rule evaluated against a verified requester before it becomes a
    manual pending request (FR-PRV-07)."""
    __tablename__ = 'bio_access_rules'

    RULE_ALLOW = 'allow'
    RULE_BLOCK = 'block'

    MATCH_DOMAIN   = 'domain'    # verified email domain
    MATCH_COMPANY  = 'company'   # verified company name (substring, case-insensitive)
    MATCH_LINKEDIN = 'linkedin'  # exact identity anchor

    id            = db.Column(db.String(9), primary_key=True, default=generate_id)
    owner_user_id = db.Column(db.String(9), nullable=False, index=True)
    rule_type     = db.Column(db.String(10), nullable=False)   # allow | block
    match_type    = db.Column(db.String(20), nullable=False)   # domain | company | linkedin
    match_value   = db.Column(db.String(200), nullable=False)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'rule_type': self.rule_type,
            'match_type': self.match_type,
            'match_value': self.match_value,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
