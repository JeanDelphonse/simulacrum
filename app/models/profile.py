import json
from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class UserProfile(db.Model):
    __tablename__ = 'user_profiles'

    id                    = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id               = db.Column(db.String(9), db.ForeignKey('users.id'), unique=True, nullable=False)
    username              = db.Column(db.String(30), unique=True, nullable=False, index=True)
    display_name          = db.Column(db.String(100))
    tagline               = db.Column(db.String(200))
    bio                   = db.Column(db.Text)
    bio_generated_at      = db.Column(db.DateTime, nullable=True)
    bio_edited            = db.Column(db.Boolean, default=False)
    avatar_path           = db.Column(db.String(500))
    location              = db.Column(db.String(100))
    linkedin_url          = db.Column(db.String(255))
    website_url           = db.Column(db.String(255))
    twitter_url           = db.Column(db.String(255))
    other_link_url        = db.Column(db.String(255))
    other_link_label      = db.Column(db.String(50))
    booking_url           = db.Column(db.String(255))
    booking_btn_label     = db.Column(db.String(50), default='Book a Call')
    show_contact_form     = db.Column(db.Boolean, default=True)
    show_booking_btn      = db.Column(db.Boolean, default=True)
    is_published          = db.Column(db.Boolean, default=False)
    noindex               = db.Column(db.Boolean, default=False)
    created_at            = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at            = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # SIM-PRD-BIO-003: enhanced bio page sections (stored as JSON text)
    _career_history       = db.Column('career_history',      db.Text, nullable=True)
    _notable_work         = db.Column('notable_work',         db.Text, nullable=True)
    _ventures             = db.Column('ventures',             db.Text, nullable=True)
    _education            = db.Column('education',            db.Text, nullable=True)
    _certifications       = db.Column('certifications',       db.Text, nullable=True)
    _references_press     = db.Column('references_press',     db.Text, nullable=True)
    _publications         = db.Column('publications',         db.Text, nullable=True)
    _projects             = db.Column('projects',             db.Text, nullable=True)
    _bio_sections_visible = db.Column('bio_sections_visible', db.Text, nullable=True)

    _BIO_SECTIONS_DEFAULT = {
        'career_timeline': True, 'notable_work': True, 'ventures': True,
        'education': True, 'references': True, 'publications': True, 'projects': True,
    }

    def _get_json_list(self, col): return json.loads(col) if col else []
    def _set_json(self, val): return json.dumps(val) if val is not None else None

    @property
    def career_history(self): return self._get_json_list(self._career_history)
    @career_history.setter
    def career_history(self, v): self._career_history = self._set_json(v)

    @property
    def notable_work(self): return self._get_json_list(self._notable_work)
    @notable_work.setter
    def notable_work(self, v): self._notable_work = self._set_json(v)

    @property
    def ventures(self): return self._get_json_list(self._ventures)
    @ventures.setter
    def ventures(self, v): self._ventures = self._set_json(v)

    @property
    def education(self): return self._get_json_list(self._education)
    @education.setter
    def education(self, v): self._education = self._set_json(v)

    @property
    def certifications(self): return self._get_json_list(self._certifications)
    @certifications.setter
    def certifications(self, v): self._certifications = self._set_json(v)

    @property
    def references_press(self): return self._get_json_list(self._references_press)
    @references_press.setter
    def references_press(self, v): self._references_press = self._set_json(v)

    @property
    def publications(self): return self._get_json_list(self._publications)
    @publications.setter
    def publications(self, v): self._publications = self._set_json(v)

    @property
    def projects(self): return self._get_json_list(self._projects)
    @projects.setter
    def projects(self, v): self._projects = self._set_json(v)

    @property
    def bio_sections_visible(self):
        if self._bio_sections_visible:
            try:
                return {**self._BIO_SECTIONS_DEFAULT, **json.loads(self._bio_sections_visible)}
            except Exception:
                pass
        return dict(self._BIO_SECTIONS_DEFAULT)

    @bio_sections_visible.setter
    def bio_sections_visible(self, v):
        self._bio_sections_visible = json.dumps(v) if v is not None else None

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
