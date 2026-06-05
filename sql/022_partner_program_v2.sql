-- 022_partner_program_v2.sql
-- Applies Alembic migration b3c4d5e6f7a8 to production MySQL.
-- Creates referral_invitations table and adds missing columns.
-- Run once. Safe to re-run (IF NOT EXISTS guards on tables).
-- NOTE: The ALTER TABLE statements will error with "Duplicate column name"
--       if the column already exists — those errors are safe to ignore.

-- 1. referral_invitations table
CREATE TABLE IF NOT EXISTS referral_invitations (
    id                  VARCHAR(9)   NOT NULL,
    partner_id          VARCHAR(9)   NOT NULL,
    recipient_email     VARCHAR(255) NOT NULL,
    recipient_first_name VARCHAR(100) NULL,
    personal_message    VARCHAR(500) NULL,
    status              VARCHAR(20)  NOT NULL DEFAULT 'sent',
    sent_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    opened_at           DATETIME     NULL,
    converted_at        DATETIME     NULL,
    PRIMARY KEY (id),
    KEY idx_ri_partner (partner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. users table — partner flags
ALTER TABLE users
    ADD COLUMN is_partner             TINYINT(1) NOT NULL DEFAULT 0 AFTER id;
ALTER TABLE users
    ADD COLUMN partner_welcome_shown  TINYINT(1) NOT NULL DEFAULT 0 AFTER is_partner;

-- 3. referral_partners table — new columns from v2
ALTER TABLE referral_partners
    ADD COLUMN commission_rate_override DECIMAL(5,4) NULL;
ALTER TABLE referral_partners
    ADD COLUMN application_source VARCHAR(20) NOT NULL DEFAULT 'public';
ALTER TABLE referral_partners
    ADD COLUMN simulations_at_apply INT NULL;
ALTER TABLE referral_partners
    ADD COLUMN last_declined_at DATETIME NULL;
ALTER TABLE referral_partners
    ADD COLUMN declined_reason VARCHAR(500) NULL;
