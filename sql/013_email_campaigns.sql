-- ============================================================
-- SIM-PRD-EMAIL-001 · Email Delivery Infrastructure — DB Migration
-- Run once against production database.
-- ============================================================

-- Per-user OAuth token storage for third-party integrations (Apollo, etc.)
CREATE TABLE IF NOT EXISTS user_integrations (
    id                      VARCHAR(9)      NOT NULL,
    user_id                 VARCHAR(9)      NOT NULL,
    provider                VARCHAR(50)     NOT NULL,   -- 'apollo'
    access_token_enc        TEXT            NULL,
    refresh_token_enc       TEXT            NULL,
    token_expires_at        DATETIME        NULL,
    apollo_daily_limit      SMALLINT        NOT NULL DEFAULT 30,
    created_at              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_ui_user_provider (user_id, provider),
    KEY idx_ui_user (user_id)
) ENGINE=InnoDB;


-- Apollo sequence deployments created by outreach agents
CREATE TABLE IF NOT EXISTS email_campaigns (
    id                  VARCHAR(9)      NOT NULL,
    simulation_id       VARCHAR(9)      NOT NULL,
    action_id           VARCHAR(9)      NOT NULL,
    apollo_sequence_id  VARCHAR(100)    NULL,
    status              VARCHAR(20)     NOT NULL DEFAULT 'active',
    -- 'active' | 'paused' | 'completed' | 'cancelled'
    contact_count       SMALLINT        NOT NULL DEFAULT 0,
    sent_count          SMALLINT        NOT NULL DEFAULT 0,
    reply_count         SMALLINT        NOT NULL DEFAULT 0,
    bounce_count        SMALLINT        NOT NULL DEFAULT 0,
    unsubscribe_count   SMALLINT        NOT NULL DEFAULT 0,
    daily_limit         SMALLINT        NOT NULL DEFAULT 30,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_ec_simulation (simulation_id),
    KEY idx_ec_action (action_id)
) ENGINE=InnoDB;
