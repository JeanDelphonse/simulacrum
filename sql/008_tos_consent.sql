-- ============================================================
-- SIM-PRD-TOS-001 · Terms of Service & Resume Upload Consent v1.1
-- Run once against production database.
-- ============================================================

-- 1. New table: resume_consents (immutable legal audit trail — NEVER deleted)
CREATE TABLE IF NOT EXISTS resume_consents (
    id                  CHAR(9)         NOT NULL,
    user_id             CHAR(9)         NOT NULL,
    tos_version         VARCHAR(20)     NOT NULL,
    privacy_version     VARCHAR(20)     NOT NULL,
    checkbox_1          TINYINT(1)      NOT NULL DEFAULT 0,
    checkbox_2          TINYINT(1)      NOT NULL DEFAULT 0,
    ip_address          VARCHAR(45)     NULL,
    user_agent          VARCHAR(500)    NULL,
    consent_method      VARCHAR(50)     NOT NULL DEFAULT 'modal_v1',
    withdrawn_at        DATETIME        NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    -- No FK constraint: charset mismatch with users.id prevents InnoDB FK creation.
    -- ON DELETE RESTRICT is enforced at the application layer (ResumeConsent model).
    KEY idx_consent_user    (user_id),
    KEY idx_consent_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Add consent_id to resumes (nullable for legacy rows — FR-TOS-06 migration handling)
ALTER TABLE resumes
    ADD COLUMN consent_id CHAR(9) NULL AFTER user_id,
    ADD INDEX idx_resumes_consent (consent_id);

-- 3. Add data-retention tracking columns to users
ALTER TABLE users
    ADD COLUMN last_login_at       DATETIME NULL AFTER recovery_token_expires,
    ADD COLUMN retention_warned_at DATETIME NULL AFTER last_login_at;

-- 4. Seed platform_settings — ToS version tracking and data retention config
INSERT INTO platform_settings (id, `key`, `value`, updated_by, updated_at)
VALUES
    (SUBSTRING(UUID(), 1, 9), 'tos_version',              '1.0', NULL, NOW()),
    (SUBSTRING(UUID(), 1, 9), 'privacy_policy_version',   '1.0', NULL, NOW()),
    (SUBSTRING(UUID(), 1, 9), 'consent_reconsent_months', '12',  NULL, NOW()),
    (SUBSTRING(UUID(), 1, 9), 'data_retention_months',    '15',  NULL, NOW())
ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = NOW();
