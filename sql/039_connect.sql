-- SIM-PRD-CONNECT-001 — Integration Connection Experience
-- Run once on deploy.
--
-- Graceful degradation: an artifact whose agent needs an unconnected one-tap
-- (oauth) integration is generated fully but marked 'pending_connection' — a
-- "connect to activate" state — rather than hard-failing. When the user connects,
-- pending artifacts flip back to 'active' automatically (no re-run).
--
-- Tier metadata (brokered|oauth|advanced) is static and lives in
-- config/integrations.json; per-user connections continue to use user_integrations.
-- No integration_registry / user_connections tables are created — UserIntegration
-- already serves as the vaulted per-user connection store.

ALTER TABLE agent_actions
    ADD COLUMN pending_connection VARCHAR(40) NULL COMMENT 'registry key of the oauth integration this artifact is waiting on, or NULL',
    ADD COLUMN activation_state   VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT 'active | pending_connection';

CREATE INDEX idx_agent_actions_pending_connection
    ON agent_actions (pending_connection);
