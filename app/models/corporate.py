from datetime import datetime, date
import secrets
from app.extensions import db
from utils.id_gen import generate_id


class CorporateAccount(db.Model):
    """Organization (SIM-PRD-ORG-001).

    A parent account holding a pool of simulation credits, one Org Admin, and
    many member users. Supersedes the original named-seat model (the seat_*
    columns are retained for backward compatibility; new orgs are credit pools).
    """
    __tablename__ = 'corporate_accounts'

    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_SUSPENDED = 'suspended'
    STATUS_EXPIRED = 'expired'

    # Legacy seat tiers (kept for backward compatibility)
    TIER_STARTER = 'starter'          # up to 25 seats
    TIER_PROFESSIONAL = 'professional' # up to 100 seats
    TIER_ENTERPRISE = 'enterprise'     # unlimited

    TIER_SEAT_LIMITS = {
        TIER_STARTER: 25,
        TIER_PROFESSIONAL: 100,
        TIER_ENTERPRISE: 9999,
    }

    # ORG-001 offer tiers (org_type)
    ORG_PILOT = 'pilot'            # 25 credits, $3,500 flat, 60 days
    ORG_COHORT = 'cohort'          # 100-500 credits, annual
    ORG_ENTERPRISE = 'enterprise'  # 500+ credits, annual
    ORG_PARTNER = 'partner'        # association / channel
    ORG_TYPES = (ORG_PILOT, ORG_COHORT, ORG_ENTERPRISE, ORG_PARTNER)

    # Default credit expiry window (days) by org type
    DEFAULT_EXPIRY_DAYS = {ORG_PILOT: 60, ORG_COHORT: 365, ORG_ENTERPRISE: 365, ORG_PARTNER: 365}

    PROVISION_ON_ISSUE = 'issue'
    PROVISION_ON_PAYMENT = 'payment'

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

    # ── ORG-001: credit pool + contract + co-branding + bulk-invite link ──
    org_type = db.Column(db.String(20), nullable=False, default=ORG_PILOT)
    credits_purchased = db.Column(db.Integer, nullable=False, default=0)
    credits_remaining = db.Column(db.Integer, nullable=False, default=0)
    credit_value_cents = db.Column(db.Integer, nullable=False, default=0)  # locked at contract time
    discount_pct = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    auto_join_domains = db.Column(db.JSON, nullable=True)                   # ['acme.com']
    provisioning_trigger = db.Column(db.String(10), nullable=False, default=PROVISION_ON_ISSUE)
    contract_start = db.Column(db.Date, nullable=True)
    contract_end = db.Column(db.Date, nullable=True)
    invite_token = db.Column(db.String(64), nullable=True, index=True)     # shareable join link
    invite_cap = db.Column(db.Integer, nullable=True)                      # NULL = unlimited
    invite_uses = db.Column(db.Integer, nullable=False, default=0)
    invite_expires_at = db.Column(db.DateTime, nullable=True)

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

    # ── Credit pool helpers ──
    @property
    def credits_used(self):
        return max(0, (self.credits_purchased or 0) - (self.credits_remaining or 0))

    @property
    def is_credit_pool(self):
        return (self.credits_purchased or 0) > 0

    @property
    def contract_expired(self):
        return bool(self.contract_end and self.contract_end < date.today())

    @property
    def can_redeem(self):
        """True if a member may draw a credit right now."""
        return (self.status == self.STATUS_ACTIVE
                and not self.contract_expired
                and (self.credits_remaining or 0) > 0)

    @property
    def display_name(self):
        return self.white_label_name or self.org_name

    @staticmethod
    def generate_invite_token():
        return secrets.token_urlsafe(24)

    def to_dict(self):
        return {
            'id': self.id,
            'org_name': self.org_name,
            'display_name': self.display_name,
            'contact_name': self.contact_name,
            'contact_email': self.contact_email,
            'org_type': self.org_type,
            'license_tier': self.license_tier,
            'seat_count': self.seat_count,
            'seats_used': self.seats_used,
            'seats_available': self.seats_available,
            'credits_purchased': self.credits_purchased,
            'credits_remaining': self.credits_remaining,
            'credits_used': self.credits_used,
            'credit_value_cents': self.credit_value_cents,
            'discount_pct': float(self.discount_pct or 0),
            'auto_join_domains': self.auto_join_domains or [],
            'provisioning_trigger': self.provisioning_trigger,
            'contract_start': self.contract_start.isoformat() if self.contract_start else None,
            'contract_end': self.contract_end.isoformat() if self.contract_end else None,
            'contract_expired': self.contract_expired,
            'status': self.status,
            'white_label_name': self.white_label_name,
            'white_label_logo_url': self.white_label_logo_url,
            'completion_rate': self.completion_rate,
            'invite_token': self.invite_token,
            'invite_cap': self.invite_cap,
            'invite_uses': self.invite_uses,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'activated_at': self.activated_at.isoformat() if self.activated_at else None,
        }


class CorporateEmployee(db.Model):
    """Org membership link (ORG-001 org_members). The member's account, bio
    page, and simulations belong to them; the org only sponsors access."""
    __tablename__ = 'corporate_employees'

    STATUS_INVITED = 'invited'
    STATUS_ACTIVE = 'active'
    STATUS_COMPLETE = 'complete'
    STATUS_DECLINED = 'declined'
    STATUS_REMOVED = 'removed'

    ROLE_MEMBER = 'member'
    ROLE_ORG_ADMIN = 'org_admin'

    JOIN_CSV = 'csv'
    JOIN_DOMAIN = 'domain'
    JOIN_LINK = 'link'
    JOIN_MANUAL = 'manual'

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
    role = db.Column(db.String(20), nullable=False, default=ROLE_MEMBER)
    join_source = db.Column(db.String(20), nullable=True)
    simulation_id = db.Column(db.String(9), nullable=True)
    invite_token = db.Column(db.String(64), nullable=True, unique=True, index=True)
    reminder_count = db.Column(db.Integer, nullable=False, default=0)
    last_reminded_at = db.Column(db.DateTime, nullable=True)
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
            'role': self.role,
            'join_source': self.join_source,
            'simulation_id': self.simulation_id,
            'reminder_count': self.reminder_count,
            'provisioned_at': self.provisioned_at.isoformat() if self.provisioned_at else None,
            'activated_at': self.activated_at.isoformat() if self.activated_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


class CreditRedemption(db.Model):
    """One row per credit drawn from an org pool (ORG-001). High volume."""
    __tablename__ = 'credit_redemptions'

    # BIGINT on MySQL (high volume); INTEGER rowid on SQLite so autoincrement works in dev.
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'),
                   primary_key=True, autoincrement=True)
    org_id = db.Column(db.String(9), nullable=False, index=True)
    user_id = db.Column(db.String(9), nullable=False, index=True)
    simulation_id = db.Column(db.String(9), nullable=True)
    credit_value_cents = db.Column(db.Integer, nullable=False)
    redeemed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'org_id': self.org_id,
            'user_id': self.user_id,
            'simulation_id': self.simulation_id,
            'credit_value_cents': self.credit_value_cents,
            'redeemed_at': self.redeemed_at.isoformat() if self.redeemed_at else None,
        }


class OrgInvoice(db.Model):
    """Annual/PO invoicing for a credit pool (ORG-001), via Stripe Invoicing."""
    __tablename__ = 'org_invoices'

    STATUS_ISSUED = 'issued'
    STATUS_PAID = 'paid'
    STATUS_VOID = 'void'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    org_id = db.Column(db.String(9), nullable=False, index=True)
    stripe_ref = db.Column(db.String(120), nullable=True)
    po_number = db.Column(db.String(80), nullable=True)
    credits = db.Column(db.Integer, nullable=False)
    unit_price_cents = db.Column(db.Integer, nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    net_terms = db.Column(db.Integer, nullable=False, default=30)
    status = db.Column(db.String(20), nullable=False, default=STATUS_ISSUED)
    issued_at = db.Column(db.DateTime, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'org_id': self.org_id,
            'stripe_ref': self.stripe_ref,
            'po_number': self.po_number,
            'credits': self.credits,
            'unit_price_cents': self.unit_price_cents,
            'amount_cents': self.amount_cents,
            'net_terms': self.net_terms,
            'status': self.status,
            'issued_at': self.issued_at.isoformat() if self.issued_at else None,
            'paid_at': self.paid_at.isoformat() if self.paid_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class OrgSmePod(db.Model):
    """SMEs assigned to cover an org's cohort (ORG-001, extends SME-001)."""
    __tablename__ = 'org_sme_pod'

    org_id = db.Column(db.String(9), primary_key=True)
    sme_id = db.Column(db.String(9), primary_key=True)
