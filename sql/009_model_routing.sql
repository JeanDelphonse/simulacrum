-- ============================================================
-- SIM-ENG-MODEL-001 · Claude Model Routing — DB Migration
-- Run once against production database.
-- ============================================================

-- Add model_tier to execution log for cost tracking per action
ALTER TABLE layer6_execution_log
    ADD COLUMN model_tier VARCHAR(20) NULL AFTER reasoning;
