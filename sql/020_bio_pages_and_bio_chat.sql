-- SIM-PRD-BIO-001 + SIM-PRD-BIOCHAT-001: bio_pages, bio_chat_sessions, bio_chat_messages
-- Run on production MySQL before deploying the zip.

CREATE TABLE IF NOT EXISTS bio_pages (
    id                  VARCHAR(9)      NOT NULL,
    user_id             VARCHAR(9)      NOT NULL,
    simulation_id       VARCHAR(9)      NULL,
    slug                VARCHAR(50)     NOT NULL,
    sections            TEXT            NOT NULL,
    custom_testimonials TEXT            NOT NULL DEFAULT '[]',
    chat_settings       TEXT            NOT NULL,
    theme               VARCHAR(20)     NOT NULL DEFAULT 'default',
    status              VARCHAR(20)     NOT NULL DEFAULT 'draft',
    published_at        DATETIME        NULL,
    unpublished_at      DATETIME        NULL,
    view_count          INT             NOT NULL DEFAULT 0,
    contact_form_count  INT             NOT NULL DEFAULT 0,
    cta_click_count     INT             NOT NULL DEFAULT 0,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_bp_user (user_id),
    UNIQUE KEY uq_bp_slug (slug),
    INDEX ix_bio_pages_user_id (user_id),
    INDEX idx_bp_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS bio_chat_sessions (
    id                  VARCHAR(9)      NOT NULL,
    bio_page_id         VARCHAR(9)      NOT NULL,
    user_id             VARCHAR(9)      NOT NULL,
    contact_id          VARCHAR(9)      NULL,
    visitor_name        VARCHAR(200)    NOT NULL,
    visitor_email       VARCHAR(255)    NOT NULL,
    visitor_phone       VARCHAR(50)     NULL,
    status              VARCHAR(20)     NOT NULL DEFAULT 'active',
    takeover_active     TINYINT(1)      NOT NULL DEFAULT 0,
    takeover_by         VARCHAR(9)      NULL,
    takeover_at         DATETIME        NULL,
    message_count       SMALLINT        NOT NULL DEFAULT 0,
    model_used_summary  VARCHAR(100)    NULL,
    total_tokens        INT             NOT NULL DEFAULT 0,
    started_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at            DATETIME        NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX ix_bcs_bio_page_id (bio_page_id),
    INDEX ix_bcs_user_id (user_id),
    INDEX ix_bcs_contact_id (contact_id),
    INDEX idx_bcs_status (user_id, status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS bio_chat_messages (
    id              VARCHAR(9)      NOT NULL,
    session_id      VARCHAR(9)      NOT NULL,
    role            VARCHAR(20)     NOT NULL,
    content         TEXT            NOT NULL,
    model_used      VARCHAR(50)     NULL,
    complexity      VARCHAR(10)     NULL,
    tokens_input    INT             NULL,
    tokens_output   INT             NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX ix_bcm_session_id (session_id),
    INDEX idx_bcm_session (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
