"""SIM-PRD-SME-001 — Simi SME Assignment models.

SimiSME          — a human subject-matter expert covering one or more expertise zones.
ExpertiseCategory — the extensible canonical taxonomy shared with the Explore directory.

Canonical-zone and SME-assignment columns live on UserProfile (see profile.py); they are
altered onto user_profiles by sql/037_sme.sql.
"""
import json
from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class SimiSME(db.Model):
    __tablename__ = 'simi_smes'

    STATUS_ACTIVE = 'active'
    STATUS_INACTIVE = 'inactive'

    id             = db.Column(db.String(9), primary_key=True, default=generate_id)
    first_name     = db.Column(db.String(80), nullable=False)
    last_name      = db.Column(db.String(80), nullable=False)
    email          = db.Column(db.String(160), nullable=False, unique=True, index=True)
    bio_url        = db.Column(db.String(500), nullable=True)
    phone          = db.Column(db.String(40), nullable=True)
    _zones         = db.Column('zones', db.Text, nullable=False, default='[]')  # JSON array of slugs
    capacity       = db.Column(db.Integer, nullable=False, default=50)
    assigned_count = db.Column(db.Integer, nullable=False, default=0)
    status         = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE)
    needs_review   = db.Column(db.Boolean, nullable=False, default=False)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def zones(self):
        if self._zones:
            try:
                return json.loads(self._zones)
            except (ValueError, TypeError):
                return []
        return []

    @zones.setter
    def zones(self, value):
        self._zones = json.dumps(value or [])

    @property
    def full_name(self):
        return ' '.join(p for p in (self.first_name, self.last_name) if p).strip()

    @property
    def is_active(self):
        return self.status == self.STATUS_ACTIVE

    @property
    def headroom(self):
        """Available capacity — how many more users this SME can take before hitting the soft cap."""
        return (self.capacity or 0) - (self.assigned_count or 0)

    def to_dict(self, include_users=False):
        data = {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'full_name': self.full_name,
            'email': self.email,
            'bio_url': self.bio_url,
            'phone': self.phone,
            'zones': self.zones,
            'capacity': self.capacity,
            'assigned_count': self.assigned_count,
            'headroom': self.headroom,
            'status': self.status,
            'needs_review': bool(self.needs_review),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if include_users:
            from app.models.profile import UserProfile
            profiles = UserProfile.query.filter_by(sme_id=self.id).all()
            data['assigned_users'] = [
                {
                    'user_id': p.user_id,
                    'display_name': p.display_name or p.username,
                    'username': p.username,
                    'primary_zone': p.primary_zone,
                    'assignment_type': p.sme_assignment_type,
                }
                for p in profiles
            ]
        return data

    def __repr__(self):
        return f'<SimiSME {self.full_name} {self.zones}>'


class ExpertiseCategory(db.Model):
    __tablename__ = 'expertise_categories'

    id         = db.Column(db.String(9), primary_key=True, default=generate_id)
    name       = db.Column(db.String(80), nullable=False)
    slug       = db.Column(db.String(80), nullable=False, unique=True, index=True)
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'is_active': bool(self.is_active),
            'sort_order': self.sort_order,
        }

    def __repr__(self):
        return f'<ExpertiseCategory {self.slug}>'
