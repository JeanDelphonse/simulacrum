-- PRD-SIM-MAIL-001 phase 1: Email-to-CRM contact extraction, and the admin
-- feature switch that governs it. Run once on deploy. Additive.
--
-- Users connect mailboxes (IMAP with an app password) or a Gmail contacts grant
-- (People API, contacts.readonly + contacts.other.readonly). Senders are scored
-- as real correspondents; high scores become contacts in the existing
-- `contacts` table, mid scores wait in review_items, low scores are kept only
-- as a hash in sender_signals. Email bodies, attachments and signature text
-- are never stored. Nothing here sends email.
--
-- The feature ships "Allowlisted users only" (no feature_flags row = allowlist).
-- Turning it off marks every imported contact Do Not Contact (contact_dnc_flags,
-- one row per reason) and pauses or revokes connections.
--
-- All PKs are CHAR(9), from utils.id_gen.generate_id().

ALTER TABLE contacts
    ADD COLUMN tier             VARCHAR(8)   NULL COMMENT 'warm | known | role',
    ADD COLUMN campaign_ready   TINYINT(1)   NULL,
    ADD COLUMN not_ready_reason VARCHAR(200) NULL,
    ADD COLUMN role_to_user     VARCHAR(20)  NULL COMMENT 'inferred until the user confirms',
    ADD COLUMN expertise        VARCHAR(200) NULL;

CREATE TABLE email_accounts (
    id                CHAR(9)      PRIMARY KEY,
    user_id           CHAR(9)      NOT NULL,
    provider          VARCHAR(10)  NOT NULL COMMENT 'gmail | m365 | imap',
    address           VARCHAR(255) NOT NULL,
    access_level      VARCHAR(12)  NOT NULL DEFAULT 'mail_read' COMMENT 'contacts | mail_read',
    status            VARCHAR(14)  NOT NULL DEFAULT 'active' COMMENT 'active | paused | admin_paused | reauth | disconnected',
    token_enc         TEXT         NULL COMMENT 'encrypted IMAP app password or OAuth refresh token',
    imap_host         VARCHAR(255) NULL,
    imap_port         INT          NULL,
    lookback_months   INT          NOT NULL DEFAULT 24,
    sync_cursor       TEXT         NULL COMMENT 'JSON resume point',
    initial_sync_done TINYINT(1)   NOT NULL DEFAULT 0,
    last_sync_at      DATETIME     NULL,
    last_error        VARCHAR(300) NULL,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_email_accounts_user ON email_accounts (user_id, status);

CREATE TABLE account_exclusions (
    id         CHAR(9)      PRIMARY KEY,
    account_id CHAR(9)      NOT NULL,
    kind       VARCHAR(10)  NOT NULL COMMENT 'domain | label | folder',
    value      VARCHAR(255) NOT NULL
);
CREATE INDEX ix_account_exclusions_account_id ON account_exclusions (account_id);

CREATE TABLE sync_runs (
    id                CHAR(9)      PRIMARY KEY,
    account_id        CHAR(9)      NOT NULL,
    started_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at       DATETIME     NULL,
    messages_scanned  INT          NOT NULL DEFAULT 0,
    senders_evaluated INT          NOT NULL DEFAULT 0,
    imported          INT          NOT NULL DEFAULT 0,
    queued            INT          NOT NULL DEFAULT 0,
    skipped           INT          NOT NULL DEFAULT 0,
    error             VARCHAR(300) NULL
);
CREATE INDEX ix_sync_runs_account_id ON sync_runs (account_id);

CREATE TABLE sender_signals (
    id             CHAR(9)     PRIMARY KEY,
    account_id     CHAR(9)     NOT NULL,
    email_hash     CHAR(64)    NOT NULL,
    score          INT         NOT NULL DEFAULT 0,
    tier           VARCHAR(8)  NULL,
    decision       VARCHAR(8)  NOT NULL COMMENT 'pending | import | review | skip',
    reasons_json   TEXT        NULL,
    signature_hash CHAR(64)    NULL,
    facts_json     TEXT        NULL COMMENT 'running counts and flags only; no names or addresses',
    evaluated_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sender_signal UNIQUE (account_id, email_hash)
);

CREATE TABLE contact_field_sources (
    id         CHAR(9)      PRIMARY KEY,
    contact_id CHAR(9)      NOT NULL,
    field      VARCHAR(30)  NOT NULL,
    value      VARCHAR(500) NULL,
    source     VARCHAR(14)  NOT NULL COMMENT 'header | signature | address_book | enrichment | inferred | user',
    confidence FLOAT        NOT NULL DEFAULT 0.5,
    account_id CHAR(9)      NULL,
    message_id VARCHAR(300) NULL,
    seen_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked     TINYINT(1)   NOT NULL DEFAULT 0 COMMENT 'user-edited: never overwritten by a sync'
);
CREATE INDEX ix_cfs_contact ON contact_field_sources (contact_id, field);

CREATE TABLE contact_account_links (
    id             CHAR(9)  PRIMARY KEY,
    contact_id     CHAR(9)  NOT NULL,
    account_id     CHAR(9)  NOT NULL,
    first_seen     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_count     INT      NOT NULL DEFAULT 0,
    received_count INT      NOT NULL DEFAULT 0,
    CONSTRAINT uq_contact_account UNIQUE (contact_id, account_id)
);
CREATE INDEX ix_contact_account_links_contact_id ON contact_account_links (contact_id);
CREATE INDEX ix_contact_account_links_account_id ON contact_account_links (account_id);

CREATE TABLE review_items (
    id           CHAR(9)     PRIMARY KEY,
    user_id      CHAR(9)     NOT NULL,
    account_id   CHAR(9)     NULL,
    contact_id   CHAR(9)     NULL,
    kind         VARCHAR(12) NOT NULL DEFAULT 'mid_score',
    email_hash   CHAR(64)    NULL,
    payload_json TEXT        NULL,
    status       VARCHAR(10) NOT NULL DEFAULT 'pending' COMMENT 'pending | approved | rejected',
    created_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at   DATETIME    NULL
);
CREATE INDEX ix_review_items_user ON review_items (user_id, status);

CREATE TABLE suppression_list (
    id         CHAR(9)     PRIMARY KEY,
    user_id    CHAR(9)     NOT NULL,
    email_hash CHAR(64)    NOT NULL,
    reason     VARCHAR(16) NOT NULL COMMENT 'unsubscribe | bounce | complaint | do_not_contact | deleted',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_suppression UNIQUE (user_id, email_hash)
);

CREATE TABLE feature_flags (
    id                CHAR(9)      PRIMARY KEY,
    feature_key       VARCHAR(40)  NOT NULL UNIQUE,
    mode              VARCHAR(10)  NOT NULL COMMENT 'off | allowlist | on',
    connection_action VARCHAR(8)   NOT NULL DEFAULT 'pause' COMMENT 'pause | revoke',
    reason            VARCHAR(300) NULL,
    updated_by        CHAR(9)      NULL,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_feature_overrides (
    id                CHAR(9)      PRIMARY KEY,
    user_id           CHAR(9)      NOT NULL,
    feature_key       VARCHAR(40)  NOT NULL,
    state             VARCHAR(14)  NOT NULL DEFAULT 'follow_global' COMMENT 'follow_global | allowed | blocked',
    connection_action VARCHAR(8)   NULL,
    reason            VARCHAR(300) NULL,
    set_by            CHAR(9)      NULL,
    set_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_feature UNIQUE (user_id, feature_key)
);

CREATE TABLE contact_dnc_flags (
    id               CHAR(9)     PRIMARY KEY,
    contact_id       CHAR(9)     NOT NULL,
    reason           VARCHAR(22) NOT NULL COMMENT 'integration_disabled | unsubscribe | complaint | bounce | user | admin',
    source_change_id CHAR(9)     NULL COMMENT 'feature_changes.id that set it',
    prior_dnc        TINYINT(1)  NOT NULL DEFAULT 0 COMMENT 'already DNC (no flag row) when set',
    set_by           CHAR(9)     NULL,
    set_at           DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lifted_by        CHAR(9)     NULL,
    lifted_at        DATETIME    NULL
);
CREATE INDEX ix_dnc_contact ON contact_dnc_flags (contact_id, lifted_at);
CREATE INDEX ix_dnc_change ON contact_dnc_flags (source_change_id);

CREATE TABLE feature_changes (
    id                CHAR(9)      PRIMARY KEY,
    feature_key       VARCHAR(40)  NOT NULL,
    scope             VARCHAR(10)  NOT NULL COMMENT 'global | user | provider',
    scope_ref         VARCHAR(300) NULL,
    old_value         VARCHAR(20)  NULL,
    new_value         VARCHAR(20)  NOT NULL,
    connection_action VARCHAR(8)   NULL,
    reason            VARCHAR(300) NOT NULL,
    contacts_marked   INT          NOT NULL DEFAULT 0,
    accounts_affected INT          NOT NULL DEFAULT 0,
    flags_lifted_at   DATETIME     NULL,
    changed_by        CHAR(9)      NOT NULL,
    changed_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
