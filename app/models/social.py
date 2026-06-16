"""
SIM-PRD-SOCIAL-001 — Social Network Layer
Models: BioPageLike, UserConnection, ActivityEvent, PlatformChat, PlatformChatMessage
"""
from __future__ import annotations
import json
from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class BioPageLike(db.Model):
    """One like per authenticated user per bio page. FR-SOC-01."""
    __tablename__ = 'bio_page_likes'

    id          = db.Column(db.String(9), primary_key=True, default=generate_id)
    bio_page_id = db.Column(db.String(9), nullable=False, index=True)
    user_id     = db.Column(db.String(9), nullable=False, index=True)
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('bio_page_id', 'user_id', name='uq_bpl_page_user'),
        db.Index('idx_bpl_user_date', 'user_id', 'created_at'),
    )


class UserConnection(db.Model):
    """
    Symmetric connection graph. Canonical ordering: user_a_id < user_b_id.
    No approval flow — clicking Connect is instant. FR-SOC-04, FR-SOC-07.
    """
    __tablename__ = 'user_connections'

    id         = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_a_id  = db.Column(db.String(9), nullable=False, index=True)
    user_b_id  = db.Column(db.String(9), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_a_id', 'user_b_id', name='uq_uc_pair'),
        db.Index('idx_uc_b', 'user_b_id', 'created_at'),
    )

    @staticmethod
    def canonical(uid1: str, uid2: str) -> tuple:
        return (uid1, uid2) if uid1 < uid2 else (uid2, uid1)

    @staticmethod
    def are_connected(uid1: str, uid2: str) -> bool:
        a, b = UserConnection.canonical(uid1, uid2)
        return UserConnection.query.filter_by(user_a_id=a, user_b_id=b).first() is not None

    @staticmethod
    def first_degree_ids(user_id: str) -> list:
        from sqlalchemy import or_
        rows = UserConnection.query.filter(
            or_(UserConnection.user_a_id == user_id,
                UserConnection.user_b_id == user_id)
        ).all()
        return [
            r.user_b_id if r.user_a_id == user_id else r.user_a_id
            for r in rows
        ]

    @staticmethod
    def second_degree_ids(user_id: str) -> set:
        """Users connected to any 1st-degree connection, excluding self and 1st-degree."""
        first = set(UserConnection.first_degree_ids(user_id))
        if not first:
            return set()
        from sqlalchemy import or_
        second = set()
        for fid in first:
            rows = UserConnection.query.filter(
                or_(UserConnection.user_a_id == fid,
                    UserConnection.user_b_id == fid)
            ).all()
            for r in rows:
                other = r.user_b_id if r.user_a_id == fid else r.user_a_id
                if other != user_id and other not in first:
                    second.add(other)
        return second

    @staticmethod
    def via_name(user_id: str, target_user_id: str) -> str:
        """Return display name of a mutual 1st-degree connection for 2nd-degree badge."""
        first = set(UserConnection.first_degree_ids(user_id))
        target_first = set(UserConnection.first_degree_ids(target_user_id))
        mutual_ids = first & target_first
        if not mutual_ids:
            return ''
        from app.models.profile import UserProfile
        mid = next(iter(mutual_ids))
        profile = UserProfile.query.filter_by(user_id=mid).first()
        return profile.display_name or '' if profile else ''


class ActivityEvent(db.Model):
    """
    System-generated feed events shown on /feed for 1st-degree connections.
    FR-SOC-09, FR-SOC-10. 30-day retention.
    """
    __tablename__ = 'activity_events'

    EVENT_BIO_PUBLISHED   = 'bio_page_published'
    EVENT_SIM_STARTED     = 'simulation_started'
    EVENT_LIKES_MILESTONE = 'likes_milestone'
    EVENT_CONNECTION_MADE = 'connection_made'
    EVENT_BIO_UPDATED     = 'bio_page_updated'

    id         = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id    = db.Column(db.String(9), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False)
    _metadata  = db.Column('metadata', db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_ae_user_date', 'user_id', 'created_at'),
        db.Index('idx_ae_type_date', 'event_type', 'created_at'),
    )

    @property
    def event_data(self) -> dict:
        try:
            return json.loads(self._metadata) if self._metadata else {}
        except (ValueError, TypeError):
            return {}

    @event_data.setter
    def event_data(self, value: dict):
        self._metadata = json.dumps(value or {})

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'event_type': self.event_type,
            'metadata': self.event_data,
            'created_at': self.created_at.isoformat(),
        }


class PlatformChat(db.Model):
    """
    Authenticated user chatting with another user's AI assistant.
    One chat per (owner, chatter) pair — persistent across sessions. FR-SOC-11–FR-SOC-15.
    """
    __tablename__ = 'platform_chats'

    STATUS_ACTIVE = 'active'
    STATUS_CLOSED = 'closed'

    id              = db.Column(db.String(9), primary_key=True, default=generate_id)
    owner_user_id   = db.Column(db.String(9), nullable=False, index=True)
    bio_page_id     = db.Column(db.String(9), nullable=False, index=True)
    chatter_user_id = db.Column(db.String(9), nullable=False, index=True)
    contact_id      = db.Column(db.String(9), nullable=True)

    status          = db.Column(db.String(20), nullable=False, default=STATUS_ACTIVE)
    message_count   = db.Column(db.SmallInteger, nullable=False, default=0)
    total_tokens    = db.Column(db.Integer, nullable=False, default=0)
    last_message_at = db.Column(db.DateTime, nullable=True)
    _tool_calls     = db.Column('tool_calls', db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('owner_user_id', 'chatter_user_id', name='uq_pc_pair'),
        db.Index('idx_pc_chatter', 'chatter_user_id', 'updated_at'),
        db.Index('idx_pc_owner',   'owner_user_id',   'created_at'),
    )

    @property
    def tool_calls(self) -> list:
        try:
            return json.loads(self._tool_calls) if self._tool_calls else []
        except (ValueError, TypeError):
            return []

    @tool_calls.setter
    def tool_calls(self, value: list):
        self._tool_calls = json.dumps(value or [])

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'owner_user_id': self.owner_user_id,
            'bio_page_id': self.bio_page_id,
            'chatter_user_id': self.chatter_user_id,
            'contact_id': self.contact_id,
            'status': self.status,
            'message_count': self.message_count,
            'last_message_at': self.last_message_at.isoformat() if self.last_message_at else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }


class PlatformChatMessage(db.Model):
    """Individual messages in a platform chat. FR-SOC-15."""
    __tablename__ = 'platform_chat_messages'

    ROLE_USER      = 'user'
    ROLE_ASSISTANT = 'assistant'

    id            = db.Column(db.String(9), primary_key=True, default=generate_id)
    chat_id       = db.Column(db.String(9), nullable=False, index=True)
    role          = db.Column(db.String(20), nullable=False)
    content       = db.Column(db.Text, nullable=False)
    model_used    = db.Column(db.String(50), nullable=True)
    tokens_input  = db.Column(db.Integer, nullable=True)
    tokens_output = db.Column(db.Integer, nullable=True)
    created_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_pcm_chat', 'chat_id', 'created_at'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'chat_id': self.chat_id,
            'role': self.role,
            'content': self.content,
            'model_used': self.model_used,
            'created_at': self.created_at.isoformat(),
        }
