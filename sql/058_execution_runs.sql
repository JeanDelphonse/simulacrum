-- Agent Execution Layer P4: executions of approved plan steps. Run once on
-- deploy. Additive.
--
-- P4 executes only REVERSIBLE steps the user approved (a Stripe payment link,
-- a Cal.com booking link), on the user's own connected account, through the
-- observability guardrail check at the tool-call boundary. Sends, signatures,
-- posts and trades are not executable. Limits are admin-configurable platform
-- settings: exec_max_runs_per_day, exec_max_runs_per_agent_per_day,
-- exec_budget_cents_per_month.
--
-- All PKs are CHAR(9), from utils.id_gen.generate_id().

CREATE TABLE execution_runs (
    id               CHAR(9)      PRIMARY KEY,
    plan_id          CHAR(9)      NOT NULL,
    step_id          CHAR(9)      NOT NULL,
    user_id          CHAR(9)      NOT NULL,
    agent_key        VARCHAR(50)  NOT NULL,
    tool_key         VARCHAR(60)  NOT NULL,
    status           VARCHAR(20)  NOT NULL DEFAULT 'running' COMMENT 'running | succeeded | failed | blocked | undone',
    request_ref      TEXT         NULL                       COMMENT 'JSON inputs the user confirmed',
    response_ref     TEXT         NULL                       COMMENT 'JSON result, e.g. the payment link id and URL',
    guardrail_result VARCHAR(300) NULL,
    error            VARCHAR(500) NULL,
    cost_cents       INT          NOT NULL DEFAULT 0,
    obs_run_id       CHAR(9)      NULL                       COMMENT 'observability agent_runs.id',
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at      DATETIME     NULL
    -- No foreign keys on purpose: an execution created something real on the
    -- user's account (a payment link, an event type). Like sent email, its record
    -- is kept as evidence when an admin deletes the cycle's plans.
);
CREATE INDEX ix_execution_runs_step ON execution_runs (step_id, status);
CREATE INDEX ix_execution_runs_user ON execution_runs (user_id, created_at);
