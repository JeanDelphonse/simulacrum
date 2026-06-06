from datetime import datetime
import secrets
from app.extensions import db
from utils.id_gen import generate_id


class CorporateAccount(db.Model):
    __tablename__ = 'corporate_accounts'

    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_SUSPENDED = 'suspended'

    TIER_STARTER = 'starter'          # up to 25 seats
    TIER_PROFESSIONAL = 'professional' # up to 100 seats
    TIER_ENTERPRISE = 'enterprise'     # unlimited

    TIER_SEAT_LIMITS = {
        TIER_STARTER: 25,
        TIER_PROFESSIONAL: 100,
        TIER_ENTERPRISE: 9999,
    }

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    org_name = db.Column(db.String(200), nullable=False)
    contact_name = db.Column(db.String(200), nullable=False)
    contact_email = db.Column(db.String(255), nullable=False, index=True)
    admin_user_id = db.Column(
        db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True,
    )
    license_tier = db.Column(db.String(20), nullable=False, default=TIER_STARTER)
    seat_count = db.Column(db.Integer, nullable=False, default=25)
    seats_used = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING, index=True)
    white_label_name = db.Column(db.String(200), nullable=True)
    white_label_logo_url = db.Column(db.String(500), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    activated_at = db.Column(db.DateTime, nullable=True)
    suspended_at = db.Column(db.DateTime, nullable=True)

    employees = db.relationship('CorporateEmployee', backref='org', lazy='dynamic',
                                cascade='all, delete-orphan')

    @property
    def seats_available(self):
        return max(0, self.seat_count - self.seats_used)

    @property
    def completion_rate(self):
        total = self.seats_used
        if not total:
            return 0
        done = self.employees.filter_by(status=CorporateEmployee.STATUS_COMPLETE).count()
        return round(done / total * 100)

    def to_dict(self):
        return {
            'id': self.id,
            'org_name': self.org_name,
            'contact_name': self.contact_name,
            'contact_email': self.contact_email,
            'license_tier': self.license_tier,
            'seat_count': self.seat_count,
            'seats_used': self.seats_used,
            'seats_available': self.seats_available,
            'status': self.status,
            'white_label_name': self.white_label_name,
            'white_label_logo_url': self.white_label_logo_url,
            'completion_rate': self.completion_rate,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'activated_at': self.activated_at.isoformat() if self.activated_at else None,
        }


class CorporateEmployee(db.Model):
    __tablename__ = 'corporate_employees'

    STATUS_INVITED = 'invited'
    STATUS_ACTIVE = 'active'
    STATUS_COMPLETE = 'complete'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    org_id = db.Column(
        db.String(9), db.ForeignKey('corporate_accounts.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    user_id = db.Column(
        db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True,
    )
    email = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_INVITED, index=True)
    simulation_id = db.Column(db.String(9), nullable=True)
    invite_token = db.Column(db.String(64), nullable=True, unique=True, index=True)
    provisioned_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    activated_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('org_id', 'email', name='uq_corp_emp_org_email'),
    )

    @staticmethod
    def generate_invite_token():
        return secrets.token_urlsafe(48)

    def to_dict(self):
        return {
            'id': self.id,
            'org_id': self.org_id,
            'user_id': self.user_id,
            'email': self.email,
            'full_name': self.full_name,
            'status': self.status,
            'simulation_id': self.simulation_id,
            'provisioned_at': self.provisioned_at.isoformat() if self.provisioned_at else None,
            'activated_at': self.activated_at.isoformat() if self.activated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }
