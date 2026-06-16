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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    resumes = db.relationship('Resume', backref='owner', lazy='dynamic', cascade='all, delete-orphan')
    simulations = db.relationship('Simulation', backref='owner', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.email}>'
