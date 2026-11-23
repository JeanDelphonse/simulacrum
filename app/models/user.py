from datetime import datetime
from flask_login import UserMixin
from app.extensions import db
from utils.id_gen import generate_id


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=True)  # nullable for OAuth-only users
    full_name = db.Column(db.String(255), nullable=False)
    google_id = db.Column(db.String(255), unique=True, nullable=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_verify_token = db.Column(db.String(255), nullable=True)
    email_verify_token_expires = db.Column(db.DateTime, nullable=True)
    password_reset_token = db.Column(db.String(255), nullable=True)
    password_reset_expires = db.Column(db.DateTime, nullable=True)
    simulation_count  = db.Column(db.Integer, default=0, nullable=False)
    connection_count  = db.Column(db.Integer, default=0, nullable=False)
    total_spend = db.Column(db.Integer, default=0, nullable=False)  # in cents
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_partner = db.Column(db.Boolean, default=False, nullable=False)
    partner_welcome_shown = db.Column(db.Boolean, default=False, nullable=False)
    # email change flow
    pending_email = db.Column(db.String(255), nullable=True)
    pending_email_token = db.Column(db.String(255), nullable=True)
    pending_email_token_expires = db.Column(db.DateTime, nullable=True)
    # account deletion / recovery
    deleted_at = db.Column(db.DateTime, nullable=True)
    recovery_token = db.Column(db.String(255), nullable=True)
    recovery_token_expires = db.Column(db.DateTime, nullable=True)
    # data retention (FR-TOS-13)
    last_login_at = db.Column(db.DateTime, nullable=True)
    retention_warned_at = db.Column(db.DateTime, nullable=True)
    # onboarding wizard (SIM-PRD-ONBOARD-001)
    onboarding_step = db.Column(db.Integer, default=1, nullable=False, server_default='1')
    onboarding_completed_at = db.Column(db.DateTime, nullable=True)
    # voice training (SIM-PRD-VOICE-001)
    elevenlabs_voice_id       = db.Column(db.String(50),  nullable=True)
    voice_trained_at          = db.Column(db.DateTime,    nullable=True)
    voice_consent_accepted_at = db.Column(db.DateTime,    nullable=True)
    voice_training_paid_at    = db.Column(db.DateTime,    nullable=True)
    # CAN-SPAM: the sender's physical postal address, shown in every commercial
    # email footer; campaigns are blocked until it is set (COMPLIANCE_PLAN C1).
    postal_address_enc        = db.Column(db.Text,        nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    resumes = db.relationship('Resume', backref='owner', lazy='dynamic', cascade='all, delete-orphan')
    simulations = db.relationship('Simulation', backref='owner', lazy='dynamic', cascade='all, delete-orphan')

    def remember_tag(self) -> str:
        """Binds a remember-me cookie to the current password and the latest
        session revocation: changing the password or signing out other
        sessions makes every older remember cookie stop working."""
        import hashlib
        import hmac
        from flask import current_app
        from sqlalchemy import func
        from app.models.profile import UserSession
        last = db.session.query(func.max(UserSession.revoked_at)).filter(UserSession.user_id == self.id).scalar()
        msg = '%s|%s|%s' % (self.id, self.password_hash or '', last.isoformat() if last else '')
        key = (current_app.config.get('SECRET_KEY') or '').encode('utf-8')
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).hexdigest()[:20]

    def get_id(self):
        # "<id>:<tag>" ends up in the session and the remember cookie; the
        # user loader checks the tag when a session is restored from that cookie.
        try:
            return '%s:%s' % (self.id, self.remember_tag())
        except Exception:
            return str(self.id)

    def __repr__(self):
        return f'<User {self.email}>'
