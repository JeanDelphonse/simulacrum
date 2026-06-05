-- SIM-PRD-ORCH-001 v2.0 migration
-- layer6_configs.cadence: new values every_12h, every_48h, every_72h, every_168h are valid (VARCHAR, no constraint)
-- layer6_cycles.phase: new value 'transition' is valid (VARCHAR, no constraint)
-- bayesian_posteriors: cold start priors seeded at runtime by seed_cold_start_priors()
-- No schema changes required.
SELECT 1; -- no-op to confirm migration applied
