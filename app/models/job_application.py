"""Careers — a job seeker's application against one of the roles in config/jobs.json.

The role itself is static config, not a table, so the role is stored here as a
slug plus a title snapshot: the title travels with the application even if the
posting is later reworded or removed from the config.
"""
from __future__ import annotations
from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class JobApplication(db.Model):
    __tablename__ = 'job_applications'

    STATUS_NEW         = 'new'
    STATUS_REVIEWING   = 'reviewing'
    STATUS_SHORTLISTED = 'shortlisted'
    STATUS_REJECTED    = 'rejected'
    STATUS_HIRED       = 'hired'
    STATUSES = (STATUS_NEW, STATUS_REVIEWING, STATUS_SHORTLISTED, STATUS_REJECTED, STATUS_HIRED)

    STATUS_LABELS = {
        STATUS_NEW:         'New',
        STATUS_REVIEWING:   'Reviewing',
        STATUS_SHORTLISTED: 'Shortlisted',
        STATUS_REJECTED:    'Rejected',
        STATUS_HIRED:       'Hired',
    }

    id        = db.Column(db.CHAR(9), primary_key=True, default=generate_id)
    job_slug  = db.Column(db.String(80),  nullable=False, index=True)
    job_title = db.Column(db.String(160), nullable=False)   # snapshot at submit time

    full_name = db.Column(db.String(160), nullable=False)
    email     = db.Column(db.String(160), nullable=False, index=True)
    phone     = db.Column(db.String(40),  nullable=False)

    resume_filename = db.Column(db.String(255), nullable=False)  # original name, for display
    resume_path     = db.Column(db.String(500), nullable=False)  # absolute path on disk
    resume_type     = db.Column(db.String(10),  nullable=False)  # 'pdf' | 'docx' | 'doc'
    resume_size     = db.Column(db.Integer, nullable=True)       # bytes
    resume_text     = db.Column(db.Text, nullable=True)          # extracted text, for preview/search

    status     = db.Column(db.String(20), nullable=False, default=STATUS_NEW, index=True)
    admin_note = db.Column(db.String(1000), nullable=True)

    # Provenance — useful when judging which channel the applicant came from.
    source_ip  = db.Column(db.String(45),  nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)

    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    reviewed_at  = db.Column(db.DateTime, nullable=True)
    reviewed_by  = db.Column(db.CHAR(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

    @property
    def status_label(self) -> str:
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def is_previewable(self) -> bool:
        """PDFs render in an iframe; .docx falls back to extracted text; .doc has neither."""
        return self.resume_type in ('pdf', 'docx')

    def to_dict(self, include_text: bool = False) -> dict:
        data = {
            'id':              self.id,
            'job_slug':        self.job_slug,
            'job_title':       self.job_title,
            'full_name':       self.full_name,
            'email':           self.email,
            'phone':           self.phone,
            'resume_filename': self.resume_filename,
            'resume_type':     self.resume_type,
            'resume_size':     self.resume_size,
            'has_text':        bool(self.resume_text),
            'status':          self.status,
            'status_label':    self.status_label,
            'admin_note':      self.admin_note,
            'submitted_at':    self.submitted_at.isoformat() if self.submitted_at else None,
            'reviewed_at':     self.reviewed_at.isoformat() if self.reviewed_at else None,
        }
        if include_text:
            data['resume_text'] = self.resume_text
        return data

    def __repr__(self):
        return f'<JobApplication {self.full_name} → {self.job_slug} ({self.status})>'
