from datetime import datetime
import json
from app.extensions import db
from utils.id_gen import generate_id


class ReferralPartner(db.Model):
    __tablename__ = 'referral_partners'

    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_SUSPENDED = 'suspended'
    STATUS_INACTIVE = 'inactive'

    PARTNER_TYPES = ['Life Coach', 'Financial Advisor', 'Career Coach', 'Business Coach', 'Other']

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'),
                        nullable=True, index=True)
    referral_code = db.Column(db.String(9), unique=True, nullable=True, index=True)
    full_name = db.Column(db.String(200), nullable=False)
    business_name = db.Column(db.String(200), nullable=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    partner_type = db.Column(db.String(50), nullable=False)
    website_url = db.Column(db.String(500), nullable=True)
    practice_description = db.Column(db.String(300), nullable=True)
    stripe_connect_id = db.Column(db.String(100), nullable=True)
    commission_rate_override = db.Column(db.Numeric(5, 4), nullable=True)  # overrides platform default
    application_source = db.Column(db.String(20), nullable=False, default='public')  # 'public' | 'in_app'
    simulations_at_apply = db.Column(db.Integer, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    applied_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_by = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    last_declined_at = db.Column(db.DateTime, nullable=True)
    declined_reason = db.Column(db.String(500), nullable=True)

    commissions = db.relationship('Commission', foreign_keys='Commission.partner_id',
                                  backref='partner', lazy='dynamic')
    payouts = db.relationship('PartnerPayout', backref='partner', lazy='dynamic')
    advisor_accesses = db.relationship('AdvisorAccess', backref='partner', lazy='dynamic')
    referral_signups = db.relationship('ReferralSignup', backref='partner', lazy='dynamic')

    def total_earned(self):
        from sqlalchemy import func
        result = db.session.query(func.sum(Commission.commission_amount)).filter(
            Commission.partner_id == self.id,
            Commission.status.in_(['pending', 'paid']),
        ).scalar()
        return float(result or 0)

    def pending_payout(self):
        from sqlalchemy import func
        result = db.session.query(func.sum(Commission.commission_amount)).filter(
            Commission.partner_id == self.id,
            Commission.status == 'pending',
        ).scalar()
        return float(result or 0)

    def paid_to_date(self):
        from sqlalchemy import func
        result = db.session.query(func.sum(Commission.commission_amount)).filter(
            Commission.partner_id == self.id,
            Commission.status == 'paid',
        ).scalar()
        return float(result or 0)

    def referral_link(self):
        if not self.referral_code:
            return None
        from flask import request
        base = request.host_url.rstrip('/')
        return f'{base}/ref/{self.referral_code}'

    def effective_commission_rate(self):
        if self.commission_rate_override is not None:
            return float(self.commission_rate_override)
        from app.models.platform_settings import PlatformSetting
        return float(PlatformSetting.get('partner_commission_rate', '0.20'))

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'full_name': self.full_name,
            'business_name': self.business_name,
            'email': self.email,
            'partner_type': self.partner_type,
            'referral_code': self.referral_code,
            'website_url': self.website_url,
            'stripe_connect_id': self.stripe_connect_id,
            'commission_rate_override': float(self.commission_rate_override) if self.commission_rate_override else None,
            'effective_commission_rate': self.effective_commission_rate(),
            'application_source': self.application_source,
            'simulations_at_apply': self.simulations_at_apply,
            'status': self.status,
            'applied_at': self.applied_at.isoformat(),
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'last_declined_at': self.last_declined_at.isoformat() if self.last_declined_at else None,
            'declined_reason': self.declined_reason,
            'total_earned': self.total_earned(),
            'pending_payout': self.pending_payout(),
            'paid_to_date': self.paid_to_date(),
        }

    def __repr__(self):
        return f'<ReferralPartner {self.full_name} ({self.status})>'


class ReferralSignup(db.Model):
    """Tracks users attributed to a partner referral."""
    __tablename__ = 'referral_signups'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    partner_id = db.Column(db.String(9), db.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    referred_user_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'),
                                 nullable=False, unique=True, index=True)
    referral_code = db.Column(db.String(9), nullable=False)
    clicked_at = db.Column(db.DateTime, nullable=False)
    registered_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    attributed_at = db.Column(db.DateTime, nullable=True)  # set on first qualifying Simulation

    def __repr__(self):
        return f'<ReferralSignup user={self.referred_user_id} partner={self.partner_id}>'


class Commission(db.Model):
    """One record per commission event (one per paid Simulation for attributed users)."""
    __tablename__ = 'commissions'

    STATUS_PENDING = 'pending'
    STATUS_PAID = 'paid'
    STATUS_REFUNDED = 'refunded'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    partner_id = db.Column(db.String(9), db.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    simulation_id = db.Column(db.String(9), db.ForeignKey('simulations.id', ondelete='SET NULL'),
                              nullable=True, index=True)
    client_user_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'),
                               nullable=True, index=True)
    simulation_charge = db.Column(db.Numeric(8, 2), nullable=False)
    commission_rate = db.Column(db.Numeric(5, 4), nullable=False)
    commission_amount = db.Column(db.Numeric(8, 2), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')
    stripe_transfer_id = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    paid_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'partner_id': self.partner_id,
            'simulation_id': self.simulation_id,
            'client_user_id': self.client_user_id,
            'simulation_charge': float(self.simulation_charge),
            'commission_rate': float(self.commission_rate),
            'commission_amount': float(self.commission_amount),
            'status': self.status,
            'stripe_transfer_id': self.stripe_transfer_id,
            'created_at': self.created_at.isoformat(),
            'paid_at': self.paid_at.isoformat() if self.paid_at else None,
        }


class PartnerPayout(db.Model):
    """One payout batch per partner per cycle."""
    __tablename__ = 'partner_payouts'

    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    partner_id = db.Column(db.String(9), db.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    payout_amount = db.Column(db.Numeric(8, 2), nullable=False)
    _commission_ids = db.Column('commission_ids', db.Text, nullable=True)
    stripe_payout_id = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='processing')
    initiated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)

    @property
    def commission_ids(self):
        return json.loads(self._commission_ids) if self._commission_ids else []

    @commission_ids.setter
    def commission_ids(self, value):
        self._commission_ids = json.dumps(value) if value else None

    def to_dict(self):
        return {
            'id': self.id,
            'partner_id': self.partner_id,
            'payout_amount': float(self.payout_amount),
            'commission_ids': self.commission_ids,
            'stripe_payout_id': self.stripe_payout_id,
            'status': self.status,
            'initiated_at': self.initiated_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


class AdvisorAccess(db.Model):
    """Client grants a partner read-only advisor view of a Simulation."""
    __tablename__ = 'advisor_access'

    ACCESS_LEVEL_FULL_READ = 'full_read'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    partner_id = db.Column(db.String(9), db.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                           nullable=True, index=True)
    pending_email = db.Column(db.String(255), nullable=True)  # if invitee isn't a partner yet
    granted_by = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    access_level = db.Column(db.String(20), nullable=False, default='full_read')
    granted_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = db.Column(db.DateTime, nullable=True)
    last_viewed_at = db.Column(db.DateTime, nullable=True)

    notes = db.relationship('AdvisorNote', backref='advisor_access', lazy='dynamic',
                            cascade='all, delete-orphan')

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'partner_id': self.partner_id,
            'pending_email': self.pending_email,
            'granted_by': self.granted_by,
            'access_level': self.access_level,
            'granted_at': self.granted_at.isoformat(),
            'revoked_at': self.revoked_at.isoformat() if self.revoked_at else None,
            'last_viewed_at': self.last_viewed_at.isoformat() if self.last_viewed_at else None,
        }


class ReferralInvitation(db.Model):
    """Email invitations sent by a partner to prospective clients."""
    __tablename__ = 'referral_invitations'

    STATUS_SENT = 'sent'
    STATUS_CONVERTED = 'converted'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    partner_id = db.Column(db.String(9), db.ForeignKey('referral_partners.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    recipient_email = db.Column(db.String(255), nullable=False)
    recipient_first_name = db.Column(db.String(100), nullable=True)
    personal_message = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='sent')
    sent_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    opened_at = db.Column(db.DateTime, nullable=True)
    converted_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'partner_id': self.partner_id,
            'recipient_email': self.recipient_email,
            'recipient_first_name': self.recipient_first_name,
            'status': self.status,
            'sent_at': self.sent_at.isoformat(),
            'opened_at': self.opened_at.isoformat() if self.opened_at else None,
            'converted_at': self.converted_at.isoformat() if self.converted_at else None,
        }


class AdvisorNote(db.Model):
    """Coaching notes added by the advisor per layer or at Simulation level."""
    __tablename__ = 'advisor_notes'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    advisor_access_id = db.Column(db.String(9), db.ForeignKey('advisor_access.id', ondelete='CASCADE'),
                                  nullable=False, index=True)
    simulation_id = db.Column(db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    layer_number = db.Column(db.Integer, nullable=True)  # NULL = Simulation-level note
    note_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'advisor_access_id': self.advisor_access_id,
            'simulation_id': self.simulation_id,
            'layer_number': self.layer_number,
            'note_text': self.note_text,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }
