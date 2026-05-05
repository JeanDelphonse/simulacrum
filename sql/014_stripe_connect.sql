-- ============================================================
-- SIM-PRD-STRIPE-001 · Stripe Connect Integration — DB Migration
-- Run once against production database.
-- ============================================================

-- Add generic provider metadata columns to user_integrations
-- (provider_account_id = Stripe acct_xxxx, provider_scope = OAuth scope)
ALTER TABLE user_integrations
    ADD COLUMN IF NOT EXISTS provider_account_id VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS provider_scope       VARCHAR(100) NULL;
