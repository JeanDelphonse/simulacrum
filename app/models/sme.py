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

    # Zones that unlock full Layer 5 financial detail (SIM-PRD-SME-002 §3).
    L5_ZONES = frozenset({'finance', 'consulting'})

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
    # SIM-PRD-SME-002: console login identity (links to a users.id) + last login
    auth_user_id   = db.Column(db.String(9), db.ForeignKey('users.id'), nullable=True, index=True)
    last_login_at  = db.Column(db.DateTime, nullable=True)
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

    @property
    def can_view_l5(self):
        """SIM-PRD-SME-002 §3 — full Layer 5 financial detail is only for Finance/Consulting SMEs."""
        return bool(self.L5_ZONES & set(self.zones))

    @property
    def has_login(self):
        return bool(self.auth_user_id)

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
            'can_view_l5': self.can_view_l5,
            'has_login': self.has_login,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
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


class SmeRecommendation(db.Model):
    """SIM-PRD-SME-002 §4 — a typed recommendation an SME issues to a user.

    The SME advises; the user acts. Structured types (swap/add/remove agent, adjust_rate)
    render a one-click Apply in the user's GCC — but the user clicks, so the user is the
    actor of record. note / revise_artifact are advisory only (no one-click apply).
    """
    __tablename__ = 'sme_recommendations'

    STATUS_PENDING = 'pending'
    STATUS_APPLIED = 'applied'
    STATUS_DISMISSED = 'dismissed'
    STATUS_EXPIRED = 'expired'

    TYPE_SWAP_AGENT = 'swap_agent'
    TYPE_ADD_AGENT = 'add_agent'
    TYPE_REMOVE_AGENT = 'remove_agent'
    TYPE_ADJUST_RATE = 'adjust_rate'
    TYPE_REVISE_ARTIFACT = 'revise_artifact'
    TYPE_NOTE = 'note'

    ALL_TYPES = (
        TYPE_SWAP_AGENT, TYPE_ADD_AGENT, TYPE_REMOVE_AGENT,
        TYPE_ADJUST_RATE, TYPE_REVISE_ARTIFACT, TYPE_NOTE,
    )
    # Types that render a one-click Apply in the user's GCC.
    ONE_CLICK_TYPES = frozenset({
        TYPE_SWAP_AGENT, TYPE_ADD_AGENT, TYPE_REMOVE_AGENT, TYPE_ADJUST_RATE,
    })

    id            = db.Column(db.String(9), primary_key=True, default=generate_id)
    sme_id        = db.Column(db.String(9), db.ForeignKey('simi_smes.id'), nullable=False, index=True)
    user_id       = db.Column(db.String(9), db.ForeignKey('users.id'), nullable=False, index=True)
    simulation_id = db.Column(db.String(9), nullable=True)
    type          = db.Column(db.String(30), nullable=False)
    _payload      = db.Column('payload', db.Text, nullable=True)  # JSON
    rationale     = db.Column(db.Text, nullable=False)
    status        = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    dismiss_reason = db.Column(db.Text, nullable=True)
    seen_at       = db.Column(db.DateTime, nullable=True)
    expires_at    = db.Column(db.DateTime, nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at   = db.Column(db.DateTime, nullable=True)
    resolved_by   = db.Column(db.String(9), nullable=True)  # the USER who applied/dismissed

    sme = db.relationship('SimiSME', foreign_keys=[sme_id])

    @property
    def payload(self):
        if self._payload:
            try:
                return json.loads(self._payload)
            except (ValueError, TypeError):
                return {}
        return {}

    @payload.setter
    def payload(self, value):
        self._payload = json.dumps(value) if value else None

    @property
    def is_one_click(self):
        return self.type in self.ONE_CLICK_TYPES

    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING

    def to_dict(self, include_sme=False):
        data = {
            'id': self.id,
            'sme_id': self.sme_id,
            'user_id': self.user_id,
            'simulation_id': self.simulation_id,
            'type': self.type,
            'payload': self.payload,
            'rationale': self.rationale,
            'status': self.status,
            'dismiss_reason': self.dismiss_reason,
            'is_one_click': self.is_one_click,
            'seen_at': self.seen_at.isoformat() if self.seen_at else None,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
        }
        if include_sme and self.sme:
            data['sme_name'] = self.sme.full_name
        return data

    def __repr__(self):
        return f'<SmeRecommendation {self.type} {self.status}>'


class SmeAccessLog(db.Model):
    """SIM-PRD-SME-002 §8 — audit of every SME view and access denial."""
    __tablename__ = 'sme_access_log'

    id         = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'),
                           primary_key=True, autoincrement=True)
    sme_id     = db.Column(db.String(9), nullable=False, index=True)
    user_id    = db.Column(db.String(9), nullable=True, index=True)
    action     = db.Column(db.String(40), nullable=False)
    _detail    = db.Column('detail', db.Text, nullable=True)  # JSON
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def detail(self):
        if self._detail:
            try:
                return json.loads(self._detail)
            except (ValueError, TypeError):
                return {}
        return {}

    @detail.setter
    def detail(self, value):
        self._detail = json.dumps(value) if value else None

    def to_dict(self):
        return {
            'id': self.id,
            'sme_id': self.sme_id,
            'user_id': self.user_id,
            'action': self.action,
            'detail': self.detail,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f'<SmeAccessLog {self.action} sme={self.sme_id}>'
