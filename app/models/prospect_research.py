from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from app.extensions import db
from utils.id_gen import generate_id


# ── Data classes (not persisted — used in-memory during the pipeline) ─────────

@dataclass
class TargetingCriteria:
    expertise_zone: str
    expertise_tags: list
    job_titles: list
    seniorities: list
    company_sizes: list        # Apollo ranges e.g. ['51-200', '201-500']
    industries: list
    geographies: list
    target_companies: list = field(default_factory=list)
    pain_point: str = ''
    agent_type: str = ''


@dataclass
class Prospect:
    first_name: str
    last_name: str
    email: str = ''
    email_source: str = ''     # 'apollo'|'web_direct'|'pattern_verified'|'pattern_risky'|'crm'
    email_verified: bool = False
    email_confidence: float = 0.0
    email_risk_note: Optional[str] = None
    email_candidates: list = field(default_factory=list)
    job_title: str = ''
    company_name: str = ''
    company_size: Optional[str] = None
    company_website: Optional[str] = None
    industry: Optional[str] = None
    linkedin_url: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = 'United States'
    source: str = ''
    source_url: Optional[str] = None
    why_this_fits: str = ''
    qualifying_score_preview: Optional[float] = None
    is_existing_crm_contact: bool = False
    crm_contact_id: Optional[str] = None
    crm_pipeline_stage: Optional[str] = None
    apollo_person_id: Optional[str] = None

    @property
    def display_name(self) -> str:
        return f'{self.first_name} {self.last_name}'.strip()

    def to_contact_dict(self) -> dict:
        """Shape expected by record_agent_contacts()."""
        return {
            'first_name': self.first_name,
            'last_name': self.last_name,
            'email': self.email,
            'job_title': self.job_title,
            'company_name': self.company_name,
            'company_size': self.company_size,
            'industry': self.industry,
            'linkedin_url': self.linkedin_url,
            'city': self.city,
            'state': self.state,
            'country': self.country,
            'source': self.email_source or self.source or 'prospect_research',
            'source_notes': self.why_this_fits,
        }


@dataclass
class ProspectList:
    prospects: list
    sources_used: list
    total_researched: int
    total_verified: int
    total_discarded_invalid: int
    total_from_apollo: int
    total_from_web: int
    total_from_crm: int
    research_duration_seconds: float

    def format_for_prompt(self, max_prospects: int = 25) -> str:
        lines = [
            f'VERIFIED PROSPECT LIST — {self.total_verified} contacts verified '
            f'({self.total_discarded_invalid} discarded, invalid email)',
            f'Sources used: {", ".join(self.sources_used)}',
            '',
        ]
        for i, p in enumerate(self.prospects[:max_prospects], 1):
            risk = f'  ⚠ risky email: {p.email_risk_note}' if p.email_risk_note else ''
            existing = '  [existing CRM contact]' if p.is_existing_crm_contact else ''
            location = f', {p.city}, {p.state}' if p.city else ''
            lines.append(
                f'{i}. {p.display_name} — {p.job_title} at {p.company_name}{location}'
                f'\n   Email: {p.email}{risk}{existing}'
                f'\n   Fit: {p.why_this_fits}'
            )
        return '\n'.join(lines)


# ── Persisted model ───────────────────────────────────────────────────────────

class ProspectResearchRun(db.Model):
    __tablename__ = 'prospect_research_runs'

    id                      = db.Column(db.String(9),      primary_key=True, default=generate_id)
    simulation_id           = db.Column(db.String(9),      nullable=False, index=True)
    user_id                 = db.Column(db.String(9),      nullable=False, index=True)
    action_id               = db.Column(db.String(9),      nullable=False, index=True)
    calling_agent           = db.Column(db.String(50),     nullable=False)
    targeting_criteria      = db.Column(db.Text,           nullable=False)   # JSON
    sources_used            = db.Column(db.Text,           nullable=False)   # JSON array
    total_researched        = db.Column(db.SmallInteger,   nullable=False, default=0)
    total_from_apollo       = db.Column(db.SmallInteger,   nullable=False, default=0)
    total_from_web          = db.Column(db.SmallInteger,   nullable=False, default=0)
    total_from_crm          = db.Column(db.SmallInteger,   nullable=False, default=0)
    total_verified          = db.Column(db.SmallInteger,   nullable=False, default=0)
    total_discarded_invalid = db.Column(db.SmallInteger,   nullable=False, default=0)
    total_risky             = db.Column(db.SmallInteger,   nullable=False, default=0)
    verification_cost_cents = db.Column(db.Integer,        nullable=False, default=0)
    apollo_api_calls        = db.Column(db.SmallInteger,   nullable=False, default=0)
    web_search_calls        = db.Column(db.SmallInteger,   nullable=False, default=0)
    extraction_calls        = db.Column(db.SmallInteger,   nullable=False, default=0)
    duration_seconds        = db.Column(db.Numeric(6, 2),  nullable=False, default=0)
    created_at              = db.Column(db.DateTime,       default=datetime.utcnow, nullable=False)
