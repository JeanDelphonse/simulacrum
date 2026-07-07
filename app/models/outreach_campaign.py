"""
SIM-PRD-OUTREACH-001 — Admin Outreach Email Automation.

Three models power the outreach engine:
  OutreachTemplate   — editable default copy (drip_1/2/3 + named broadcast templates)
  OutreachEnrollment — per-user new-user-drip state machine
  OutreachSend       — the queue AND the log: one row per queued/scheduled/sent email

Config lives in platform_settings; segments are computed on the fly.
"""
from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class OutreachTemplate(db.Model):
    """Editable email template. Saving updates the default for all future sends."""
    __tablename__ = 'outreach_templates'

    # Fixed drip keys — the 3-email structure is fixed but every email is editable.
    DRIP_KEYS = ('drip_1', 'drip_2', 'drip_3')

    id           = db.Column(db.String(9), primary_key=True, default=generate_id)
    template_key = db.Column(db.String(50), nullable=False, unique=True)
    name         = db.Column(db.String(120), nullable=True)
    subject      = db.Column(db.String(300), nullable=False)
    preview_text = db.Column(db.String(200), nullable=True)
    body         = db.Column(db.Text, nullable=False)
    is_drip      = db.Column(db.Boolean, nullable=False, default=False)
    updated_by   = db.Column(db.String(9), nullable=True)
    created_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                             onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'template_key': self.template_key,
            'name': self.name,
            'subject': self.subject,
            'preview_text': self.preview_text,
            'body': self.body,
            'is_drip': self.is_drip,
            'updated_by': self.updated_by,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class OutreachEnrollment(db.Model):
    """A user's enrollment in the new-user drip sequence."""
    __tablename__ = 'outreach_enrollments'

    STATUS_ACTIVE    = 'active'
    STATUS_GRADUATED = 'graduated'   # created a simulation
    STATUS_COMPLETED = 'completed'   # all 3 emails sent
    STATUS_PAUSED    = 'paused'
    STATUS_REMOVED   = 'removed'     # admin removed from sequence

    id           = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id      = db.Column(db.String(9), nullable=False, index=True)
    sequence     = db.Column(db.String(50), nullable=False, default='new_user_drip')
    current_step = db.Column(db.Integer, nullable=False, default=0)
    next_send_at = db.Column(db.DateTime, nullable=True)
    status       = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE)
    created_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                             onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'sequence', name='uq_oe_user_seq'),
        db.Index('idx_oe_status_due', 'status', 'next_send_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'sequence': self.sequence,
            'current_step': self.current_step,
            'next_send_at': self.next_send_at.isoformat() if self.next_send_at else None,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class OutreachSend(db.Model):
    """One queued/scheduled/sent outreach email — serves as both queue and log."""
    __tablename__ = 'outreach_sends'

    KIND_DRIP      = 'drip'
    KIND_BROADCAST = 'broadcast'

    STATUS_QUEUED            = 'queued'
    STATUS_AWAITING_APPROVAL = 'awaiting_approval'
    STATUS_PAUSED            = 'paused'
    STATUS_SCHEDULED         = 'scheduled'    # broadcast scheduled for a future time
    STATUS_SENT              = 'sent'
    STATUS_SKIPPED           = 'skipped'
    STATUS_SUPPRESSED        = 'suppressed'
    STATUS_FAILED            = 'failed'

    # Statuses that still represent a pending send in the queue.
    PENDING_STATUSES = (STATUS_QUEUED, STATUS_AWAITING_APPROVAL,
                        STATUS_PAUSED, STATUS_SCHEDULED)

    id            = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id       = db.Column(db.String(9), nullable=False, index=True)
    enrollment_id = db.Column(db.String(9), nullable=True, index=True)
    kind          = db.Column(db.String(20), nullable=False, default=KIND_DRIP)
    template_key  = db.Column(db.String(50), nullable=False)
    step_number   = db.Column(db.Integer, nullable=True)
    subject       = db.Column(db.String(300), nullable=False)
    preview_text  = db.Column(db.String(200), nullable=True)
    body_snapshot = db.Column(db.Text, nullable=False)
    was_edited    = db.Column(db.Boolean, nullable=False, default=False)
    approved_by   = db.Column(db.String(9), nullable=True)
    approved_at   = db.Column(db.DateTime, nullable=True)
    to_email      = db.Column(db.String(255), nullable=False)
    provider_message_id = db.Column(db.String(100), nullable=True, index=True)
    status        = db.Column(db.String(20), nullable=False, default=STATUS_QUEUED)
    scheduled_at  = db.Column(db.DateTime, nullable=True)
    sent_at       = db.Column(db.DateTime, nullable=True)
    opened_at     = db.Column(db.DateTime, nullable=True)
    open_count    = db.Column(db.Integer, nullable=False, default=0)
    clicked_at    = db.Column(db.DateTime, nullable=True)
    click_count   = db.Column(db.Integer, nullable=False, default=0)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                              onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_os_user', 'user_id', 'sent_at'),
        db.Index('idx_os_status_due', 'status', 'scheduled_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'enrollment_id': self.enrollment_id,
            'kind': self.kind,
            'template_key': self.template_key,
            'step_number': self.step_number,
            'subject': self.subject,
            'preview_text': self.preview_text,
            'body_snapshot': self.body_snapshot,
            'was_edited': self.was_edited,
            'approved_by': self.approved_by,
            'to_email': self.to_email,
            'status': self.status,
            'scheduled_at': self.scheduled_at.isoformat() if self.scheduled_at else None,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'opened_at': self.opened_at.isoformat() if self.opened_at else None,
            'clicked_at': self.clicked_at.isoformat() if self.clicked_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
