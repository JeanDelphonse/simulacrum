"""Inbound enterprise inquiries from the public marketing pages (the
Transition Package page first). Staff work them at /admin/inquiries and can
promote a qualified one into the outbound CRM (admin_prospects)."""
from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


class EnterpriseInquiry(db.Model):
    __tablename__ = 'enterprise_inquiries'
    __table_args__ = (db.Index('ix_enterprise_inquiries_status', 'status', 'created_at'),
                      db.Index('ix_enterprise_inquiries_ip', 'ip_hash', 'created_at'))

    STATUS_NEW = 'new'
    STATUS_CONTACTED = 'contacted'
    STATUS_CLOSED = 'closed'
    STATUSES = (STATUS_NEW, STATUS_CONTACTED, STATUS_CLOSED)

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    name = db.Column(db.String(160), nullable=False)
    work_email = db.Column(db.String(255), nullable=False)
    company = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(160), nullable=True)
    headcount_band = db.Column(db.String(20), nullable=True)
    timing = db.Column(db.String(30), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(40), nullable=False, default='transition-package')
    mode = db.Column(db.String(20), nullable=True)             # page mode when submitted
    status = db.Column(db.String(10), nullable=False, default=STATUS_NEW)
    ip_hash = db.Column(db.String(64), nullable=True)          # rate limiting only; never the raw IP
    prospect_id = db.Column(db.String(9), nullable=True)       # admin_prospects.id once promoted
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    status_changed_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'work_email': self.work_email, 'company': self.company,
                'role': self.role, 'headcount_band': self.headcount_band, 'timing': self.timing,
                'notes': self.notes, 'source': self.source, 'mode': self.mode, 'status': self.status,
                'prospect_id': self.prospect_id, 'created_at': self.created_at.isoformat(),
                'status_changed_at': self.status_changed_at.isoformat() if self.status_changed_at else None}
