-- Add cycle_steps to layer6_cycles — the self-serve to-do list rendered under the
-- "Cycle N · AI insight" card on the GCC journey tab, and in the cycle summary email.
--
-- The column was only ever added by the Alembic revision
-- migrations/versions/c5d6e7f8a9b0_add_cycle_steps_col.py. Deploys apply sql/NNN_*.sql
-- by hand, so on any database that has not run Alembic the column is missing and
-- layer6._execute_orchestrator_cycle fails when it assigns cycle.cycle_steps.
--
-- MySQL has no ADD COLUMN IF NOT EXISTS. If the column already exists this errors
-- with #1060 "Duplicate column name: cycle_steps" — safe to ignore, it just means
-- the database is already up to date.

ALTER TABLE layer6_cycles
    ADD COLUMN cycle_steps TEXT NULL COMMENT 'JSON array of self-serve to-do strings';
