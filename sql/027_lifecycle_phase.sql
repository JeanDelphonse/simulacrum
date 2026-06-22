-- 027_lifecycle_phase.sql
-- SIM-REQ-LIFECYCLE-001: lifecycle phase columns
-- Run in phpMyAdmin or via MySQL CLI before deploying the new build.

-- 1. simulations: lifecycle phase (active / maintenance / dormant)
ALTER TABLE simulations
  ADD COLUMN lifecycle_phase VARCHAR(15) NOT NULL DEFAULT 'active'
  AFTER unlock_all_layers;

-- 2. layer6_configs: lifecycle control fields
ALTER TABLE layer6_configs
  ADD COLUMN active_cycle_limit         INT            NOT NULL DEFAULT 30,
  ADD COLUMN maintenance_frequency_hours INT           NOT NULL DEFAULT 168,
  ADD COLUMN convergence_delta          DECIMAL(6,4)   NOT NULL DEFAULT 0.0200,
  ADD COLUMN convergence_consecutive    INT            NOT NULL DEFAULT 3,
  ADD COLUMN convergence_min_cycles     INT            NOT NULL DEFAULT 15,
  ADD COLUMN maintenance_dispatch_threshold DECIMAL(4,2) NOT NULL DEFAULT 0.70;

-- 3. Seed PlatformSetting defaults for lifecycle admin config
INSERT INTO platform_settings (id, `key`, value, updated_at)
VALUES
  (SUBSTRING(MD5('lc_active_cycle_limit'), 1, 9),            'lc_active_cycle_limit',             '30',   NOW()),
  (SUBSTRING(MD5('lc_maintenance_frequency_hours'), 1, 9),   'lc_maintenance_frequency_hours',    '168',  NOW()),
  (SUBSTRING(MD5('lc_convergence_delta'), 1, 9),             'lc_convergence_delta',              '0.02', NOW()),
  (SUBSTRING(MD5('lc_convergence_consecutive'), 1, 9),       'lc_convergence_consecutive',        '3',    NOW()),
  (SUBSTRING(MD5('lc_convergence_min_cycles'), 1, 9),        'lc_convergence_min_cycles',         '15',   NOW()),
  (SUBSTRING(MD5('lc_maintenance_dispatch_threshold'), 1, 9),'lc_maintenance_dispatch_threshold', '0.70', NOW()),
  (SUBSTRING(MD5('lc_active_cadence_hours'), 1, 9),          'lc_active_cadence_hours',           '24',   NOW())
ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = NOW();
