from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class UserProfile(db.Model):
    __tablename__ = 'user_profiles'

    id                = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id           = db.Column(db.String(9), db.ForeignKey('users.id'), unique=True, nullable=False)
    username          = db.Column(db.String(30), unique=True, nullable=False, index=True)
    display_name      = db.Column(db.String(100))
    tagline           = db.Column(db.String(200))
    bio               = db.Column(db.Text)
    bio_generated_at  = db.Column(db.DateTime, nullable=True)
    bio_edited        = db.Column(db.Boolean, default=False)
    avatar_path       = db.Column(db.String(500))
    location          = db.Column(db.String(100))
    linkedin_url      = db.Column(db.String(255))
    website_url       = db.Column(db.String(255))
    twitter_url       = db.Column(db.String(255))
    other_link_url    = db.Column(db.String(255))
    other_link_label  = db.Column(db.String(50))
    booking_url       = db.Column(db.String(255))
    booking_btn_label = db.Column(db.String(50), default='Book a Call')
    show_contact_form = db.Column(db.Boolean, default=True)
    show_booking_btn  = db.Column(db.Boolean, default=True)
    is_published      = db.Column(db.Boolean, default=False)
    noindex           = db.Column(db.Boolean, default=False)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('profile', uselist=False))

    @property
    def completeness(self):
        score = 0
        if self.avatar_path:
            score += 25
        if self.tagline:
            score += 25
        if self.bio:
            score += 25
        if SimulationVisibility.query.filter_by(user_id=self.user_id, is_public=True).first():
            score += 25
        return score

    def effective_booking_url(self):
        if self.booking_url:
            return self.booking_url
        from app.models.agent_action import AgentAction
        action = AgentAction.query.filter_by(
            action_type='booking_page', status='complete',
        ).join(
            __import__('app.models.simulation', fromlist=['Simulation']).Simulation,
            AgentAction.simulation_id == __import__('app.models.simulation', fromlist=['Simulation']).Simulation.id,
        ).filter_by(user_id=self.user_id).order_by(AgentAction.completed_at.desc()).first()
        if action and action.artifact:
            import re
            m = re.search(r'https?://cal\.com/\S+', action.artifact)
            if m:
                return m.group(0)
        return None


class SimulationVisibility(db.Model):
    __tablename__ = 'simulation_visibility'

    id            = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(db.String(9), db.ForeignKey('simulations.id'), nullable=False)
    user_id       = db.Column(db.String(9), db.ForeignKey('users.id'), nullable=False)
    is_public     = db.Column(db.Boolean, default=False)
    display_order = db.Column(db.Integer, default=0)
    zone_tagline  = db.Column(db.String(200))
    services      = db.Column(db.JSON)
    availability  = db.Column(
        db.Enum('available', 'limited', 'unavailable', 'hidden', name='sim_availability'),
        default='hidden',
    )
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    simulation = db.relationship('Simulation', backref=db.backref('visibility_config', uselist=False))

    @property
    def services_list(self):
        return self.services or []


class ProfileInquiry(db.Model):
    __tablename__ = 'profile_inquiries'

    id              = db.Column(db.String(9), primary_key=True, default=generate_id)
    profile_user_id = db.Column(db.String(9), db.ForeignKey('users.id'), nullable=False, index=True)
    visitor_name    = db.Column(db.String(100), nullable=False)
    visitor_email   = db.Column(db.String(255), nullable=False)
    subject         = db.Column(db.String(100))
    message         = db.Column(db.Text, nullable=False)
    ip_hash         = db.Column(db.String(64))
    recaptcha_score = db.Column(db.Float)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class UserSession(db.Model):
    __tablename__ = 'user_sessions'

    id          = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id     = db.Column(db.String(9), db.ForeignKey('users.id'), nullable=False, index=True)
    jti         = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_agent  = db.Column(db.String(500))
    ip_address  = db.Column(db.String(45))
    last_active = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at  = db.Column(db.DateTime, nullable=False)
    revoked_at  = db.Column(db.DateTime, nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > datetime.utcnow()

    @property
    def device_label(self):
        ua = self.user_agent or ''
        if 'Mobile' in ua:
            return 'Mobile'
        for browser in ('Chrome', 'Firefox', 'Safari', 'Edge'):
            if browser in ua:
                return browser
        return 'Browser'

    @property
    def ip_truncated(self):
        if not self.ip_address:
            return '—'
        parts = self.ip_address.split('.')
        if len(parts) == 4:
            return '.'.join(parts[:3]) + '.xxx'
        return self.ip_address[:8] + '...'
