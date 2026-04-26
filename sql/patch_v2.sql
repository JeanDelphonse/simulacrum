-- Simulacrum — Schema patch v2
-- Run this against the production MySQL database.
-- Safe to run multiple times (uses IF NOT EXISTS / column-exists checks).

-- 1. Add email_verify_token_expires to users (migration c3d4e5f6a7b8)
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS email_verify_token_expires DATETIME NULL;

-- 2. Add amount_charged_cents to simulations (migration d4e5f6a7b8c9)
ALTER TABLE simulations
  ADD COLUMN IF NOT EXISTS amount_charged_cents INT NULL;

-- 3. Create agent_actions table (migration b2c3d4e5f6a7)
CREATE TABLE IF NOT EXISTS agent_actions (
    id VARCHAR(9) PRIMARY KEY,
    simulation_id VARCHAR(9) NOT NULL,
    layer_number INT NOT NULL,
    action_type VARCHAR(50) NOT NULL,
    user_inputs TEXT NULL,
    artifact TEXT NULL,
    archived_artifact TEXT NULL,
    archived_at DATETIME NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    error_message TEXT NULL,
    created_by VARCHAR(9) NULL,
    created_at DATETIME NOT NULL,
    completed_at DATETIME NULL,
    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE,
    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    INDEX ix_agent_actions_simulation_id (simulation_id),
    INDEX ix_agent_actions_layer (simulation_id, layer_number)
);

-- 4. Create agent_context table (migration f6a7b8c9d0e1)
CREATE TABLE IF NOT EXISTS agent_context (
    id VARCHAR(9) PRIMARY KEY,
    simulation_id VARCHAR(9) NOT NULL,
    layer_number INT NOT NULL DEFAULT 0,
    context_key VARCHAR(100) NOT NULL,
    context_value TEXT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_agent_context_sim_layer_key (simulation_id, layer_number, context_key),
    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE,
    INDEX ix_agent_context_simulation_id (simulation_id)
);

-- 5. Partner Program tables
CREATE TABLE IF NOT EXISTS referral_partners (
    id VARCHAR(9) PRIMARY KEY,
    user_id VARCHAR(9) NULL,
    referral_code VARCHAR(9) NULL UNIQUE,
    full_name VARCHAR(200) NOT NULL,
    business_name VARCHAR(200) NULL,
    email VARCHAR(255) NOT NULL,
    partner_type VARCHAR(50) NOT NULL,
    website_url VARCHAR(500) NULL,
    practice_description VARCHAR(300) NULL,
    stripe_connect_id VARCHAR(100) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    applied_at DATETIME NOT NULL,
    approved_at DATETIME NULL,
    approved_by VARCHAR(9) NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (approved_by) REFERENCES users(id) ON DELETE SET NULL,
    INDEX ix_referral_partners_user_id (user_id),
    INDEX ix_referral_partners_email (email),
    INDEX ix_referral_partners_referral_code (referral_code)
);

CREATE TABLE IF NOT EXISTS referral_signups (
    id VARCHAR(9) PRIMARY KEY,
    partner_id VARCHAR(9) NOT NULL,
    referred_user_id VARCHAR(9) NOT NULL UNIQUE,
    referral_code VARCHAR(9) NOT NULL,
    clicked_at DATETIME NOT NULL,
    registered_at DATETIME NOT NULL,
    attributed_at DATETIME NULL,
    FOREIGN KEY (partner_id) REFERENCES referral_partners(id) ON DELETE CASCADE,
    FOREIGN KEY (referred_user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX ix_referral_signups_partner_id (partner_id),
    INDEX ix_referral_signups_referred_user_id (referred_user_id)
);

CREATE TABLE IF NOT EXISTS commissions (
    id VARCHAR(9) PRIMARY KEY,
    partner_id VARCHAR(9) NOT NULL,
    simulation_id VARCHAR(9) NULL,
    client_user_id VARCHAR(9) NULL,
    simulation_charge DECIMAL(8,2) NOT NULL,
    commission_rate DECIMAL(5,4) NOT NULL,
    commission_amount DECIMAL(8,2) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    stripe_transfer_id VARCHAR(255) NULL,
    created_at DATETIME NOT NULL,
    paid_at DATETIME NULL,
    FOREIGN KEY (partner_id) REFERENCES referral_partners(id) ON DELETE CASCADE,
    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE SET NULL,
    FOREIGN KEY (client_user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX ix_commissions_partner_id (partner_id),
    INDEX ix_commissions_simulation_id (simulation_id),
    INDEX ix_commissions_client_user_id (client_user_id)
);

CREATE TABLE IF NOT EXISTS partner_payouts (
    id VARCHAR(9) PRIMARY KEY,
    partner_id VARCHAR(9) NOT NULL,
    payout_amount DECIMAL(8,2) NOT NULL,
    commission_ids TEXT NULL,
    stripe_payout_id VARCHAR(255) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'processing',
    initiated_at DATETIME NOT NULL,
    completed_at DATETIME NULL,
    FOREIGN KEY (partner_id) REFERENCES referral_partners(id) ON DELETE CASCADE,
    INDEX ix_partner_payouts_partner_id (partner_id)
);

CREATE TABLE IF NOT EXISTS advisor_access (
    id VARCHAR(9) PRIMARY KEY,
    simulation_id VARCHAR(9) NOT NULL,
    partner_id VARCHAR(9) NULL,
    pending_email VARCHAR(255) NULL,
    granted_by VARCHAR(9) NOT NULL,
    access_level VARCHAR(20) NOT NULL DEFAULT 'full_read',
    granted_at DATETIME NOT NULL,
    revoked_at DATETIME NULL,
    last_viewed_at DATETIME NULL,
    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE,
    FOREIGN KEY (partner_id) REFERENCES referral_partners(id) ON DELETE CASCADE,
    FOREIGN KEY (granted_by) REFERENCES users(id) ON DELETE CASCADE,
    INDEX ix_advisor_access_simulation_id (simulation_id),
    INDEX ix_advisor_access_partner_id (partner_id)
);

CREATE TABLE IF NOT EXISTS advisor_notes (
    id VARCHAR(9) PRIMARY KEY,
    advisor_access_id VARCHAR(9) NOT NULL,
    simulation_id VARCHAR(9) NOT NULL,
    layer_number INT NULL,
    note_text TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (advisor_access_id) REFERENCES advisor_access(id) ON DELETE CASCADE,
    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE CASCADE,
    INDEX ix_advisor_notes_advisor_access_id (advisor_access_id),
    INDEX ix_advisor_notes_simulation_id (simulation_id)
);

-- 6. Partner Program v2 — new columns (migration b3c4d5e6f7a8)
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS is_partner TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS partner_welcome_shown TINYINT(1) NOT NULL DEFAULT 0;

ALTER TABLE referral_partners
  ADD COLUMN IF NOT EXISTS commission_rate_override DECIMAL(5,4) NULL,
  ADD COLUMN IF NOT EXISTS application_source VARCHAR(20) NOT NULL DEFAULT 'public',
  ADD COLUMN IF NOT EXISTS simulations_at_apply INT NULL,
  ADD COLUMN IF NOT EXISTS last_declined_at DATETIME NULL,
  ADD COLUMN IF NOT EXISTS declined_reason VARCHAR(500) NULL;

-- 7. Referral invitations table
CREATE TABLE IF NOT EXISTS referral_invitations (
    id VARCHAR(9) PRIMARY KEY,
    partner_id VARCHAR(9) NOT NULL,
    recipient_email VARCHAR(255) NOT NULL,
    recipient_first_name VARCHAR(100) NULL,
    personal_message VARCHAR(500) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'sent',
    sent_at DATETIME NOT NULL,
    opened_at DATETIME NULL,
    converted_at DATETIME NULL,
    FOREIGN KEY (partner_id) REFERENCES referral_partners(id) ON DELETE CASCADE,
    INDEX ix_referral_invitations_partner_id (partner_id)
);
