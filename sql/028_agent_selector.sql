-- SIM-PRD-AGENTSEL-001: Agent Selector columns on simulations table
-- Run once on the production database after deploying the code.

ALTER TABLE simulations
  ADD COLUMN IF NOT EXISTS selected_agents             MEDIUMTEXT    NULL COMMENT 'JSON list of selected agent action_types',
  ADD COLUMN IF NOT EXISTS agent_relevance_scores      MEDIUMTEXT    NULL COMMENT 'JSON dict: action_type -> {score, recommended, matched_zones}',
  ADD COLUMN IF NOT EXISTS agent_personalized_descriptions MEDIUMTEXT NULL COMMENT 'JSON dict: action_type -> personalised one-line description (cached)',
  ADD COLUMN IF NOT EXISTS agent_selection_confirmed_at DATETIME     NULL COMMENT 'When the user confirmed their agent selection (NULL = selector not yet shown)';

-- Backfill: mark all existing simulations as having skipped the selector
-- (so they continue working without seeing the agent selector screen).
-- Only run this once, immediately after the ALTER TABLE above.
UPDATE simulations
SET    agent_selection_confirmed_at = created_at
WHERE  agent_selection_confirmed_at IS NULL;
