"""Email-to-CRM contact extraction (PRD-SIM-MAIL-001, phase 1) and the
platform feature switch it is governed by.

All tables are additive. Contacts themselves stay in the existing `contacts`
table (extended in sql/060); these tables hold the connections, the scoring
trail, per-field provenance and the Do Not Contact ledger. Email bodies,
attachments and signature text are never stored anywhere.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


def email_hash(email: str) -> str:
    """Stable hash of a normalised address, for skipped senders and suppression."""
    return hashlib.sha256((email or '').strip().lower().encode('utf-8')).hexdigest()


class EmailAccount(db.Model):
    """One connected mailbox (or Gmail contacts grant) belonging to a user."""
    __tablename__ = 'email_accounts'
    __table_args__ = (db.Index('ix_email_accounts_user', 'user_id', 'status'),)

    PROVIDER_GMAIL = 'gmail'
    PROVIDER_M365 = 'm365'
    PROVIDER_IMAP = 'imap'
    PROVIDERS = (PROVIDER_GMAIL, PROVIDER_M365, PROVIDER_IMAP)

    ACCESS_CONTACTS = 'contacts'          # Gmail step 1: People API only
    ACCESS_MAIL_READ = 'mail_read'        # headers + signature window

    STATUS_ACTIVE = 'active'
    STATUS_PAUSED = 'paused'              # the user paused it
    STATUS_ADMIN_PAUSED = 'admin_paused'  # the feature switch paused it
    STATUS_REAUTH = 'reauth'              # credentials stopped working
    STATUS_DISCONNECTED = 'disconnected'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False)
    provider = db.Column(db.String(10), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    access_level = db.Column(db.String(12), nullable=False, default=ACCESS_MAIL_READ)
    status = db.Column(db.String(14), nullable=False, default=STATUS_ACTIVE)
    token_enc = db.Column(db.Text, nullable=True)        # IMAP app password / OAuth refresh token
    imap_host = db.Column(db.String(255), nullable=True)
    imap_port = db.Column(db.Integer, nullable=True)
    lookback_months = db.Column(db.Integer, nullable=False, default=24)
    _cursor = db.Column('sync_cursor', db.Text, nullable=True)
    initial_sync_done = db.Column(db.Boolean, nullable=False, default=False)
    last_sync_at = db.Column(db.DateTime, nullable=True)
    last_error = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def cursor(self) -> dict:
        return json.loads(self._cursor) if self._cursor else {}

    @cursor.setter
    def cursor(self, value):
        self._cursor = json.dumps(value) if value else None

    def to_dict(self):
        return {
            'id': self.id, 'provider': self.provider, 'address': self.address,
            'access_level': self.access_level, 'status': self.status,
            'lookback_months': self.lookback_months, 'initial_sync_done': self.initial_sync_done,
            'last_sync_at': self.last_sync_at.isoformat() if self.last_sync_at else None,
            'last_error': self.last_error,
            'exclusions': [e.to_dict() for e in AccountExclusion.query.filter_by(account_id=self.id).all()],
        }


class AccountExclusion(db.Model):
    __tablename__ = 'account_exclusions'

    KINDS = ('domain', 'label', 'folder')

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    account_id = db.Column(db.String(9), nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)
    value = db.Column(db.String(255), nullable=False)

    def to_dict(self):
        return {'id': self.id, 'kind': self.kind, 'value': self.value}


class SyncRun(db.Model):
    __tablename__ = 'sync_runs'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    account_id = db.Column(db.String(9), nullable=False, index=True)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)
    messages_scanned = db.Column(db.Integer, nullable=False, default=0)
    senders_evaluated = db.Column(db.Integer, nullable=False, default=0)
    imported = db.Column(db.Integer, nullable=False, default=0)
    queued = db.Column(db.Integer, nullable=False, default=0)
    skipped = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.String(300), nullable=True)

    def to_dict(self):
        return {'id': self.id, 'started_at': self.started_at.isoformat(),
                'finished_at': self.finished_at.isoformat() if self.finished_at else None,
                'messages_scanned': self.messages_scanned, 'senders_evaluated': self.senders_evaluated,
                'imported': self.imported, 'queued': self.queued, 'skipped': self.skipped,
                'error': self.error}


class SenderSignal(db.Model):
    """The scoring trail for one sender on one account. Skipped senders live
    only here, as a hash — never as contacts."""
    __tablename__ = 'sender_signals'
    __table_args__ = (db.UniqueConstraint('account_id', 'email_hash', name='uq_sender_signal'),)

    DECISION_IMPORT = 'import'
    DECISION_REVIEW = 'review'
    DECISION_SKIP = 'skip'
    DECISION_PENDING = 'pending'     # still collecting facts during a sync

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    account_id = db.Column(db.String(9), nullable=False)
    email_hash = db.Column(db.String(64), nullable=False)
    score = db.Column(db.Integer, nullable=False, default=0)
    tier = db.Column(db.String(8), nullable=True)            # warm | known | role
    decision = db.Column(db.String(8), nullable=False)
    reasons_json = db.Column(db.Text, nullable=True)
    signature_hash = db.Column(db.String(64), nullable=True)  # re-extract only when it changes
    # Running counts and flags only (no names, no addresses), so a skipped
    # sender stays a hash with a score.
    facts_json = db.Column(db.Text, nullable=True)
    evaluated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def reasons(self) -> list:
        return json.loads(self.reasons_json) if self.reasons_json else []

    @property
    def facts(self) -> dict:
        return json.loads(self.facts_json) if self.facts_json else {}

    @facts.setter
    def facts(self, value):
        self.facts_json = json.dumps(value) if value else None


class ContactFieldSource(db.Model):
    """Where one field value of a contact came from, and how sure we are."""
    __tablename__ = 'contact_field_sources'
    __table_args__ = (db.Index('ix_cfs_contact', 'contact_id', 'field'),)

    SOURCES = ('header', 'signature', 'address_book', 'enrichment', 'inferred', 'user')

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    contact_id = db.Column(db.String(9), nullable=False)
    field = db.Column(db.String(30), nullable=False)
    value = db.Column(db.String(500), nullable=True)
    source = db.Column(db.String(14), nullable=False)
    confidence = db.Column(db.Float, nullable=False, default=0.5)
    account_id = db.Column(db.String(9), nullable=True)
    message_id = db.Column(db.String(300), nullable=True)
    seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    locked = db.Column(db.Boolean, nullable=False, default=False)   # user-edited: never overwritten

    def to_dict(self):
        return {'field': self.field, 'value': self.value, 'source': self.source,
                'confidence': round(self.confidence, 2), 'account_id': self.account_id,
                'seen_at': self.seen_at.isoformat(), 'locked': self.locked}


class ContactAccountLink(db.Model):
    __tablename__ = 'contact_account_links'
    __table_args__ = (db.UniqueConstraint('contact_id', 'account_id', name='uq_contact_account'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    contact_id = db.Column(db.String(9), nullable=False, index=True)
    account_id = db.Column(db.String(9), nullable=False, index=True)
    first_seen = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sent_count = db.Column(db.Integer, nullable=False, default=0)
    received_count = db.Column(db.Integer, nullable=False, default=0)


class ReviewItem(db.Model):
    """A sender waiting for the user's decision (phase 1: mid-score senders)."""
    __tablename__ = 'review_items'
    __table_args__ = (db.Index('ix_review_items_user', 'user_id', 'status'),)

    KIND_MID_SCORE = 'mid_score'
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False)
    account_id = db.Column(db.String(9), nullable=True)
    contact_id = db.Column(db.String(9), nullable=True)
    kind = db.Column(db.String(12), nullable=False, default=KIND_MID_SCORE)
    email_hash = db.Column(db.String(64), nullable=True)
    payload_json = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(10), nullable=False, default=STATUS_PENDING)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime, nullable=True)

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json) if self.payload_json else {}

    def to_dict(self):
        p = self.payload
        return {'id': self.id, 'kind': self.kind, 'status': self.status, 'account_id': self.account_id,
                'email': p.get('email'), 'name': p.get('name'), 'company': (p.get('fields') or {}).get('company_name'),
                'score': p.get('score'), 'reasons': p.get('reasons', []),
                'created_at': self.created_at.isoformat()}


class UserSuppression(db.Model):
    """Per-user suppression, hashed (PRD: a deleted contact's opt-out still holds)."""
    __tablename__ = 'suppression_list'
    __table_args__ = (db.UniqueConstraint('user_id', 'email_hash', name='uq_user_suppression'),)

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False)
    email_hash = db.Column(db.String(64), nullable=False)
    reason = db.Column(db.String(16), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class FeatureFlag(db.Model):
    """Global switch for a feature ('email_to_crm', 'email_to_crm.imap', …)."""
    __tablename__ = 'feature_flags'

    MODE_OFF = 'off'
    MODE_ALLOWLIST = 'allowlist'
    MODE_ON = 'on'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    feature_key = db.Column(db.String(40), nullable=False, unique=True)
    mode = db.Column(db.String(10), nullable=False)
    connection_action = db.Column(db.String(8), nullable=False, default='pause')   # pause | revoke
    reason = db.Column(db.String(300), nullable=True)
    updated_by = db.Column(db.String(9), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class UserFeatureOverride(db.Model):
    __tablename__ = 'user_feature_overrides'
    __table_args__ = (db.UniqueConstraint('user_id', 'feature_key', name='uq_user_feature'),)

    STATE_FOLLOW = 'follow_global'
    STATE_ALLOWED = 'allowed'
    STATE_BLOCKED = 'blocked'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False)
    feature_key = db.Column(db.String(40), nullable=False)
    state = db.Column(db.String(14), nullable=False, default=STATE_FOLLOW)
    connection_action = db.Column(db.String(8), nullable=True)
    reason = db.Column(db.String(300), nullable=True)
    set_by = db.Column(db.String(9), nullable=True)
    set_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class ContactDncFlag(db.Model):
    """One row per reason a contact is Do Not Contact. A contact is DNC while
    any flag is unlifted, so lifting the switch's flags never clears an
    unsubscribe or the user's own choice."""
    __tablename__ = 'contact_dnc_flags'
    __table_args__ = (db.Index('ix_dnc_contact', 'contact_id', 'lifted_at'),
                      db.Index('ix_dnc_change', 'source_change_id'))

    REASONS = ('integration_disabled', 'unsubscribe', 'complaint', 'bounce', 'user', 'admin')

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    contact_id = db.Column(db.String(9), nullable=False)
    reason = db.Column(db.String(22), nullable=False)
    source_change_id = db.Column(db.String(9), nullable=True)
    prior_dnc = db.Column(db.Boolean, nullable=False, default=False)   # DNC already, with no flag row
    set_by = db.Column(db.String(9), nullable=True)
    set_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    lifted_by = db.Column(db.String(9), nullable=True)
    lifted_at = db.Column(db.DateTime, nullable=True)


class FeatureChange(db.Model):
    """Every switch change: who, why, scope, when, and how many contacts it marked."""
    __tablename__ = 'feature_changes'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    feature_key = db.Column(db.String(40), nullable=False)
    scope = db.Column(db.String(10), nullable=False)          # global | user | provider
    scope_ref = db.Column(db.String(300), nullable=True)      # user ids / provider
    old_value = db.Column(db.String(20), nullable=True)
    new_value = db.Column(db.String(20), nullable=False)
    connection_action = db.Column(db.String(8), nullable=True)
    reason = db.Column(db.String(300), nullable=False)
    contacts_marked = db.Column(db.Integer, nullable=False, default=0)
    accounts_affected = db.Column(db.Integer, nullable=False, default=0)
    flags_lifted_at = db.Column(db.DateTime, nullable=True)
    changed_by = db.Column(db.String(9), nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {'id': self.id, 'feature_key': self.feature_key, 'scope': self.scope,
                'scope_ref': self.scope_ref, 'old_value': self.old_value, 'new_value': self.new_value,
                'connection_action': self.connection_action, 'reason': self.reason,
                'contacts_marked': self.contacts_marked, 'accounts_affected': self.accounts_affected,
                'flags_lifted_at': self.flags_lifted_at.isoformat() if self.flags_lifted_at else None,
                'changed_by': self.changed_by, 'changed_at': self.changed_at.isoformat()}
