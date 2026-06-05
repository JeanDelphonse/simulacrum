-- 021_integration_activity_log.sql
-- Creates integration_activity_log and integration_audit_log tables.
-- Run once on production MySQL. Safe to re-run (IF NOT EXISTS guards).

CREATE TABLE IF NOT EXISTS integration_activity_log (
    id          VARCHAR(9)   NOT NULL,
    user_id     VARCHAR(9)   NOT NULL,
    provider    VARCHAR(50)  NOT NULL,
    event_type  VARCHAR(80)  NOT NULL,
    direction   VARCHAR(10)  NOT NULL DEFAULT 'outbound',
    status      VARCHAR(20)  NOT NULL DEFAULT 'success',
    detail      VARCHAR(500) NULL,
    action_id   VARCHAR(9)   NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_ial_user_provider (user_id, provider),
    KEY idx_ial_created       (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS integration_audit_log (
    id               VARCHAR(9)  NOT NULL,
    admin_user_id    VARCHAR(9)  NOT NULL,
    target_user_id   VARCHAR(9)  NOT NULL,
    integration_type VARCHAR(30) NOT NULL,
    action           VARCHAR(50) NOT NULL,
    changes          TEXT        NULL,
    approved_by      VARCHAR(9)  NULL,
    ip_address       VARCHAR(50) NULL,
    created_at       DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_aal_target (target_user_id),
    KEY idx_aal_admin  (admin_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
