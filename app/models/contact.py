from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id

STAGE_ORDER = ['prospect', 'active', 'client']

COMPANY_SIZE_OPTIONS = ['solo', '2-10', '11-50', '51-200', '201-500', '501-1000', '1001-5000', '5000+']
SENIORITY_OPTIONS = [
    'individual_contributor', 'manager', 'director',
    'vp', 'c_suite', 'founder', 'partner',
]
SOURCE_OPTIONS = [
    'manual_entry', 'csv_import', 'linkedin_import',
    'agent_generated', 'inbound_referral',
]


class Contact(db.Model):
    __tablename__ = 'contacts'

    id                  = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id             = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'),
                                    nullable=False, index=True)
    first_name          = db.Column(db.String(100), nullable=False)
    last_name           = db.Column(db.String(100), nullable=False)
    email               = db.Column(db.String(255), nullable=False)
    phone               = db.Column(db.String(50), nullable=True)
    job_title           = db.Column(db.String(200), nullable=True)
    company_name        = db.Column(db.String(200), nullable=True, index=True)
    company_size        = db.Column(db.String(50), nullable=True)
    industry            = db.Column(db.String(100), nullable=True)
    department          = db.Column(db.String(100), nullable=True)
    seniority           = db.Column(db.String(50), nullable=True)
    linkedin_url        = db.Column(db.String(500), nullable=True)
    linkedin_headline   = db.Column(db.String(500), nullable=True)
    website_url         = db.Column(db.String(500), nullable=True)
    company_website     = db.Column(db.String(500), nullable=True)
    twitter_url         = db.Column(db.String(500), nullable=True)
    other_url           = db.Column(db.String(500), nullable=True)
    city                = db.Column(db.String(100), nullable=True)
    state_region        = db.Column(db.String(100), nullable=True)
    country             = db.Column(db.String(100), nullable=True, default='United States')
    timezone            = db.Column(db.String(100), nullable=True)
    source              = db.Column(db.String(50), nullable=False, default='manual_entry')
    source_action_id    = db.Column(db.String(9), nullable=True)
    source_artifact_id  = db.Column(db.String(9), nullable=True)
    source_notes        = db.Column(db.String(500), nullable=True)
    qualifying_score    = db.Column(db.Numeric(4, 3), nullable=True, index=True)
    pipeline_stage      = db.Column(
        db.Enum('prospect', 'active', 'client', 'closed_lost'),
        nullable=False, default='prospect', index=True,
    )
    last_contacted_at   = db.Column(db.DateTime, nullable=True)
    is_archived         = db.Column(db.Boolean, nullable=False, default=False)
    do_not_contact      = db.Column(db.Boolean, nullable=False, default=False)
    notes               = db.Column(db.Text, nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at          = db.Column(db.DateTime, default=datetime.utcnow,
                                    onupdate=datetime.utcnow, nullable=False)

    activities = db.relationship('ContactActivity', backref='contact',
                                  lazy='dynamic', cascade='all, delete-orphan',
                                  order_by='ContactActivity.activity_date.desc()')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'email', name='uq_contact_email'),
    )

    @property
    def display_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def score_pct(self):
        if self.qualifying_score is None:
            return None
        return int(float(self.qualifying_score) * 100)

    def advance_stage(self, new_stage: str, created_by: str = 'agent',
                      simulation_id: str = None, action_id: str = None, notes: str = None):
        """Advance pipeline_stage (only forward). Returns True if changed."""
        if new_stage == self.pipeline_stage:
            return False
        current_idx = STAGE_ORDER.index(self.pipeline_stage) if self.pipeline_stage in STAGE_ORDER else -1
        new_idx = STAGE_ORDER.index(new_stage) if new_stage in STAGE_ORDER else -1
        if created_by == 'agent' and new_idx <= current_idx:
            return False
        from_stage = self.pipeline_stage
        self.pipeline_stage = new_stage
        activity = ContactActivity(
            id=generate_id(),
            contact_id=self.id,
            simulation_id=simulation_id,
            action_id=action_id,
            activity_type='stage_changed',
            pipeline_stage_from=from_stage,
            pipeline_stage_to=new_stage,
            created_by=created_by,
            notes=notes,
        )
        db.session.add(activity)
        return True

    def to_dict(self):
        return {
            'id': self.id,
            'display_name': self.display_name,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'email': self.email,
            'phone': self.phone,
            'job_title': self.job_title,
            'company_name': self.company_name,
            'company_size': self.company_size,
            'industry': self.industry,
            'seniority': self.seniority,
            'linkedin_url': self.linkedin_url,
            'linkedin_headline': self.linkedin_headline,
            'website_url': self.website_url,
            'city': self.city,
            'state_region': self.state_region,
            'country': self.country,
            'source': self.source,
            'qualifying_score': float(self.qualifying_score) if self.qualifying_score is not None else None,
            'score_pct': self.score_pct,
            'pipeline_stage': self.pipeline_stage,
            'last_contacted_at': self.last_contacted_at.isoformat() if self.last_contacted_at else None,
            'is_archived': self.is_archived,
            'do_not_contact': self.do_not_contact,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class ContactActivity(db.Model):
    __tablename__ = 'contact_activities'

    id                  = db.Column(db.String(9), primary_key=True, default=generate_id)
    contact_id          = db.Column(db.String(9), db.ForeignKey('contacts.id', ondelete='CASCADE'),
                                    nullable=False, index=True)
    simulation_id       = db.Column(db.String(9), nullable=True, index=True)
    action_id           = db.Column(db.String(9), nullable=True, index=True)
    activity_type       = db.Column(db.String(50), nullable=False, index=True)
    activity_date       = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    notes               = db.Column(db.Text, nullable=True)
    pipeline_stage_from = db.Column(db.String(30), nullable=True)
    pipeline_stage_to   = db.Column(db.String(30), nullable=True)
    created_by          = db.Column(db.String(20), nullable=False, default='agent')

    def to_dict(self):
        return {
            'id': self.id,
            'contact_id': self.contact_id,
            'simulation_id': self.simulation_id,
            'action_id': self.action_id,
            'activity_type': self.activity_type,
            'activity_date': self.activity_date.isoformat() if self.activity_date else None,
            'notes': self.notes,
            'pipeline_stage_from': self.pipeline_stage_from,
            'pipeline_stage_to': self.pipeline_stage_to,
            'created_by': self.created_by,
        }
