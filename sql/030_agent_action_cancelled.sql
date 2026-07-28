-- 030: Agent action cancelled status + celery_task_id + status index
-- celery_task_id stores the Celery task ID so stop/restart can call
-- celery.control.revoke(terminate=True) to SIGTERM the in-flight worker and
-- release the Anthropic API HTTP connection immediately.
--
-- PORTABILITY: this file previously used `ADD COLUMN IF NOT EXISTS` and
-- `CREATE INDEX IF NOT EXISTS`. Both are MariaDB extensions that MySQL 8 rejects
-- as syntax errors, so the migration only ran on a MariaDB host. Rewritten as
-- plain DDL, which both engines accept and which matches every other migration
-- here (see 040, 041, 042).
--
-- Not self-skipping, so re-running raises one of these — both safe to ignore,
-- and both mean the database is already up to date:
--   #1060  Duplicate column name 'celery_task_id'
--   #1061  Duplicate key name 'ix_agent_actions_status'
-- Run the two statements separately if you want to apply one without the other.

ALTER TABLE agent_actions
    ADD COLUMN celery_task_id VARCHAR(255) NULL
    COMMENT 'Celery task ID - used to revoke/terminate the task on stop or restart';

CREATE INDEX ix_agent_actions_status ON agent_actions (status);
