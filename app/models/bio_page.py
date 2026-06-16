"""
SIM-PRD-BIO-001 + SIM-PRD-BIOCHAT-001 + SIM-PRD-BIO-002
Bio page, bio page chat, and PLG distribution models.
"""
from __future__ import annotations
import json
from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id

_DEFAULT_SECTIONS = {
    'hero': {
        'professional_title': None,
        'positioning': None,
        'cta_url': None,
        'cta_label': 'Book a call',
        'hero_image_url': None,
        'is_custom_title': False,
        'is_custom_positioning': False,
    },
    'about': {'bio_text': None, 'is_custom': False},
    'services': {'hidden_tiers': [], 'tier_order': []},
    'products': {'hidden_products': [], 'product_order': []},
    'speaking': {'hidden_topics': [], 'topic_order': [], 'custom_topics': []},
    'blog': {'pinned_article': None, 'enabled': True},
    'section_order': [
        'hero', 'about', 'services', 'products',
        'speaking', 'blog', 'testimonials', 'footer',
    ],
    'visibility': {
        'services': True, 'products': True,
        'speaking': True, 'blog': True, 'testimonials': True,
    },
}

_DEFAULT_CHAT_SETTINGS = {
    'enabled': True,
    'live_takeover_enabled': False,
    'custom_welcome': None,
    'daily_session_limit': 50,
}


class BioPage(db.Model):
    """One bio page per user at /u/:slug."""
    __tablename__ = 'bio_pages'

    STATUS_DRAFT       = 'draft'
    STATUS_PUBLISHED   = 'published'
    STATUS_UNPUBLISHED = 'unpublished'

    THEME_DEFAULT = 'default'
    THEME_DARK    = 'dark'
    THEME_WARM    = 'warm'

    id              = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id         = db.Column(db.String(9), nullable=False, index=True)
    simulation_id   = db.Column(db.String(9), nullable=True)
    slug            = db.Column(db.String(50), nullable=False)

    _sections       = db.Column('sections', db.Text, nullable=False,
                                 default=lambda: json.dumps(_DEFAULT_SECTIONS))
    _custom_testimonials = db.Column('custom_testimonials', db.Text,
                                      nullable=False, default='[]')
    _chat_settings  = db.Column('chat_settings', db.Text, nullable=False,
                                 default=lambda: json.dumps(_DEFAULT_CHAT_SETTINGS))

    theme           = db.Column(db.String(20), nullable=False, default=THEME_DEFAULT)
    status          = db.Column(db.String(20), nullable=False, default=STATUS_DRAFT)
    published_at    = db.Column(db.DateTime, nullable=True)
    unpublished_at  = db.Column(db.DateTime, nullable=True)

    view_count          = db.Column(db.Integer, nullable=False, default=0)
    contact_form_count  = db.Column(db.Integer, nullable=False, default=0)
    cta_click_count     = db.Column(db.Integer, nullable=False, default=0)

    # Social layer (SIM-PRD-SOCIAL-001)
    like_count          = db.Column(db.Integer, nullable=False, default=0)

    # PLG distribution (SIM-PRD-BIO-002)
    show_badge          = db.Column(db.Boolean, nullable=False, default=True)
    show_on_explore     = db.Column(db.Boolean, nullable=False, default=True)
    share_prompt_shown  = db.Column(db.Boolean, nullable=False, default=False)

    created_at      = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                                onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', name='uq_bp_user'),
        db.UniqueConstraint('slug', name='uq_bp_slug'),
        db.Index('idx_bp_status', 'status'),
    )

    # ── JSON properties ────────────────────────────────────────────────────

    @property
    def sections(self) -> dict:
        try:
            return json.loads(self._sections) if self._sections else dict(_DEFAULT_SECTIONS)
        except (ValueError, TypeError):
            return dict(_DEFAULT_SECTIONS)

    @sections.setter
    def sections(self, value: dict):
        self._sections = json.dumps(value) if value is not None else json.dumps(_DEFAULT_SECTIONS)

    @property
    def custom_testimonials(self) -> list:
        try:
            return json.loads(self._custom_testimonials) if self._custom_testimonials else []
        except (ValueError, TypeError):
            return []

    @custom_testimonials.setter
    def custom_testimonials(self, value: list):
        self._custom_testimonials = json.dumps(value or [])

    @property
    def chat_settings(self) -> dict:
        try:
            return json.loads(self._chat_settings) if self._chat_settings else dict(_DEFAULT_CHAT_SETTINGS)
        except (ValueError, TypeError):
            return dict(_DEFAULT_CHAT_SETTINGS)

    @chat_settings.setter
    def chat_settings(self, value: dict):
        self._chat_settings = json.dumps(value) if value is not None else json.dumps(_DEFAULT_CHAT_SETTINGS)

    @property
    def chat_enabled(self) -> bool:
        return self.chat_settings.get('enabled', True)

    @property
    def is_published(self) -> bool:
        return self.status == self.STATUS_PUBLISHED

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'slug': self.slug,
            'sections': self.sections,
            'custom_testimonials': self.custom_testimonials,
            'chat_settings': self.chat_settings,
            'theme': self.theme,
            'status': self.status,
            'is_published': self.is_published,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'view_count': self.view_count,
            'like_count': self.like_count,
            'contact_form_count': self.contact_form_count,
            'cta_click_count': self.cta_click_count,
            'show_badge': self.show_badge,
            'show_on_explore': self.show_on_explore,
            'share_prompt_shown': self.share_prompt_shown,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }


class BioChatSession(db.Model):
    """One chat session per prospect visit to a bio page."""
    __tablename__ = 'bio_chat_sessions'

    STATUS_ACTIVE  = 'active'
    STATUS_ENDED   = 'ended'
    STATUS_DELETED = 'deleted'

    id              = db.Column(db.String(9), primary_key=True, default=generate_id)
    bio_page_id     = db.Column(db.String(9), nullable=False, index=True)
    user_id         = db.Column(db.String(9), nullable=False, index=True)
    contact_id      = db.Column(db.String(9), nullable=True, index=True)

    visitor_name    = db.Column(db.String(200), nullable=False)
    visitor_email   = db.Column(db.String(255), nullable=False)
    visitor_phone   = db.Column(db.String(50), nullable=True)

    status              = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE)
    takeover_active     = db.Column(db.Boolean, nullable=False, default=False)
    takeover_by         = db.Column(db.String(9), nullable=True)
    takeover_at         = db.Column(db.DateTime, nullable=True)

    message_count       = db.Column(db.SmallInteger, nullable=False, default=0)
    model_used_summary  = db.Column(db.String(100), nullable=True)
    total_tokens        = db.Column(db.Integer, nullable=False, default=0)

    started_at      = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ended_at        = db.Column(db.DateTime, nullable=True)
    created_at      = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_bcs_status', 'user_id', 'status', 'started_at'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'bio_page_id': self.bio_page_id,
            'user_id': self.user_id,
            'contact_id': self.contact_id,
            'visitor_name': self.visitor_name,
            'visitor_email': self.visitor_email,
            'visitor_phone': self.visitor_phone,
            'status': self.status,
            'takeover_active': self.takeover_active,
            'message_count': self.message_count,
            'model_used_summary': self.model_used_summary,
            'started_at': self.started_at.isoformat(),
            'ended_at': self.ended_at.isoformat() if self.ended_at else None,
            'created_at': self.created_at.isoformat(),
        }


class BioPageVisit(db.Model):
    """First-party visitor record for bio page analytics (SIM-PRD-BIO-002)."""
    __tablename__ = 'bio_page_visits'

    id           = db.Column(db.String(9),   primary_key=True, default=generate_id)
    bio_page_id  = db.Column(db.String(9),   nullable=False, index=True)
    visitor_hash = db.Column(db.String(64),  nullable=False)
    referrer     = db.Column(db.String(255), nullable=False, default='')
    utm_source   = db.Column(db.String(100), nullable=False, default='')
    created_at   = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_bpv_page_date', 'bio_page_id', 'created_at'),
        db.Index('idx_bpv_hash',      'bio_page_id', 'visitor_hash'),
    )


class BioChatMessage(db.Model):
    """Individual message within a bio chat session."""
    __tablename__ = 'bio_chat_messages'

    ROLE_VISITOR   = 'visitor'
    ROLE_ASSISTANT = 'assistant'
    ROLE_TAKEOVER  = 'user_takeover'

    id          = db.Column(db.String(9), primary_key=True, default=generate_id)
    session_id  = db.Column(db.String(9), nullable=False, index=True)
    role        = db.Column(db.String(20), nullable=False)
    content     = db.Column(db.Text, nullable=False)
    model_used  = db.Column(db.String(50), nullable=True)
    complexity  = db.Column(db.String(10), nullable=True)
    tokens_input  = db.Column(db.Integer, nullable=True)
    tokens_output = db.Column(db.Integer, nullable=True)
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_bcm_session', 'session_id', 'created_at'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'session_id': self.session_id,
            'role': self.role,
            'content': self.content,
            'model_used': self.model_used,
            'complexity': self.complexity,
            'created_at': self.created_at.isoformat(),
        }
