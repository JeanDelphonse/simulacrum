-- 030: Agent action cancelled status + celery_task_id + status index
-- celery_task_id stores the Celery task ID so stop/restart can call
-- celery.control.revoke(terminate=True) to SIGTERM the in-flight worker and
-- release the Anthropic API HTTP connection immediately.

ALTER TABLE agent_actions
  ADD COLUMN IF NOT EXISTS celery_task_id VARCHAR(255) NULL
    COMMENT 'Celery task ID — used to revoke/terminate the task on stop or restart';

CREATE INDEX IF NOT EXISTS ix_agent_actions_status ON agent_actions (status);
