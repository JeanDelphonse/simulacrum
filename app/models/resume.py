from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id
import json


class Resume(db.Model):
    __tablename__ = 'resumes'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    label = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=True)  # null for LinkedIn source
    file_type = db.Column(db.String(10), nullable=True)   # 'pdf' | 'docx' | null for LinkedIn
    source = db.Column(db.String(20), nullable=False, default='upload')  # 'upload' | 'linkedin'
    parsed_text = db.Column(db.Text, nullable=True)
    _expertise_zones = db.Column('expertise_zones', db.Text, nullable=True)  # JSON cache
    linkedin_access_token_enc = db.Column(db.Text, nullable=True)  # AES-256 encrypted
    linkedin_profile_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    simulations = db.relationship('Simulation', backref='source_resume', lazy='dynamic')

    @property
    def expertise_zones(self):
        if self._expertise_zones:
            return json.loads(self._expertise_zones)
        return None

    @expertise_zones.setter
    def expertise_zones(self, value):
        self._expertise_zones = json.dumps(value) if value is not None else None

    def __repr__(self):
        return f'<Resume {self.label} ({self.source})>'
