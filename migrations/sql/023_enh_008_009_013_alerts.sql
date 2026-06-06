-- SIM-PRD-ENHANCE-001: ENH-008 user_insight, ENH-009 trust_level, ENH-013 unlock_all_layers, ENH-003 integration_signals
-- Run in phpMyAdmin against the simulacrum database

-- ENH-08: Plain-language AI narration per cycle
ALTER TABLE layer6_cycles
  ADD COLUMN user_insight TEXT NULL;

-- ENH-09: Trust level preset on config
ALTER TABLE layer6_configs
  ADD COLUMN trust_level VARCHAR(20) NOT NULL DEFAULT 'balanced';

-- ENH-13: Skip progressive layer unlock
ALTER TABLE simulations
  ADD COLUMN unlock_all_layers TINYINT(1) NOT NULL DEFAULT 0;

-- ENH-03: Track highest milestone alert already sent
ALTER TABLE layer6_momentum
  ADD COLUMN last_milestone_reached_cents INT NULL;

-- ENH-03: Raw integration signal events
CREATE TABLE IF NOT EXISTS integration_signals (
  id              VARCHAR(9)  NOT NULL,
  simulation_id   VARCHAR(9)  NOT NULL,
  user_id         VARCHAR(9)  NOT NULL,
  signal_type     VARCHAR(50) NOT NULL,
  payload         TEXT        NULL,
  alert_created   TINYINT(1)  NOT NULL DEFAULT 0,
  created_at      DATETIME    NOT NULL,
  PRIMARY KEY (id),
  INDEX ix_int_sig_sim  (simulation_id),
  INDEX ix_int_sig_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
