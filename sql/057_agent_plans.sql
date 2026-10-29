-- Agent Execution Layer P3: tool registry, plans and plan steps, per-agent
-- autonomy settings, and approval decisions. Run once on deploy. Additive.
--
-- P3 is L1 (plan + suggest): agents propose inspectable plans and users approve
-- or reject steps. NOTHING EXECUTES in P3. Execution of approved reversible
-- steps arrives in P4; sends in P5. tool_registry rows are seeded by the app on
-- first use (app/services/agent_plans.TOOL_SEED); admins may edit enabled and
-- cost_cents. Autonomy is capped at L3 (L4 is never allowed), Layer 5 agents
-- are locked at L0 (generate-only), money-movement tools ship disabled.
--
-- All PKs are CHAR(9), from utils.id_gen.generate_id().

CREATE TABLE tool_registry (
    id                CHAR(9)      PRIMARY KEY,
    tool_key          VARCHAR(60)  NOT NULL,
    integration       VARCHAR(40)  NULL,
    side_effect_class VARCHAR(20)  NOT NULL DEFAULT 'none',
    blast_radius      INT          NOT NULL DEFAULT 0     COMMENT '0 none, 1 one person, 2 a list, 3 public',
    cost_cents        INT          NOT NULL DEFAULT 0,
    reversible        TINYINT(1)   NOT NULL DEFAULT 1,
    enabled           TINYINT(1)   NOT NULL DEFAULT 1,
    CONSTRAINT uq_tool_key UNIQUE (tool_key)
);

CREATE TABLE agent_plans (
    id             CHAR(9)      PRIMARY KEY,
    agent_key      VARCHAR(50)  NOT NULL,
    simulation_id  CHAR(9)      NOT NULL,
    action_id      CHAR(9)      NULL                  COMMENT 'agent_actions.id the plan acts on',
    user_id        CHAR(9)      NULL,
    goal           TEXT         NULL,
    method         VARCHAR(60)  NULL,
    autonomy_level VARCHAR(4)   NOT NULL DEFAULT 'L1' COMMENT 'level when the plan was made',
    status         VARCHAR(20)  NOT NULL DEFAULT 'proposed' COMMENT 'proposed | approved | rejected | superseded',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_ap_simulation FOREIGN KEY (simulation_id) REFERENCES simulations (id) ON DELETE CASCADE
);
CREATE INDEX ix_agent_plans_action ON agent_plans (action_id, status);

CREATE TABLE plan_steps (
    id              CHAR(9)      PRIMARY KEY,
    plan_id         CHAR(9)      NOT NULL,
    seq_index       INT          NOT NULL DEFAULT 0,
    tool_key        VARCHAR(60)  NULL                 COMMENT 'NULL = manual / review step',
    method          VARCHAR(60)  NOT NULL,
    inputs_json     TEXT         NULL,
    expected        VARCHAR(300) NULL,
    reversibility   VARCHAR(20)  NOT NULL DEFAULT 'reversible',
    blast           INT          NOT NULL DEFAULT 0,
    est_cost_cents  INT          NOT NULL DEFAULT 0,
    required_reason VARCHAR(20)  NULL                 COMMENT 'hard_gate | irreversible | blast',
    approval_status VARCHAR(20)  NOT NULL DEFAULT 'not_required',
    CONSTRAINT fk_ps_plan FOREIGN KEY (plan_id) REFERENCES agent_plans (id) ON DELETE CASCADE
);
CREATE INDEX ix_plan_steps_plan ON plan_steps (plan_id, seq_index);

CREATE TABLE agent_autonomy_config (
    id             CHAR(9)      PRIMARY KEY,
    user_id        CHAR(9)      NOT NULL,
    agent_key      VARCHAR(50)  NOT NULL,
    level          VARCHAR(4)   NOT NULL              COMMENT 'L0 | L1 | L2 | L3 (never L4)',
    overrides_json TEXT         NULL,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT uq_autonomy_user_agent UNIQUE (user_id, agent_key),
    CONSTRAINT fk_aac_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE action_approvals (
    id              CHAR(9)      PRIMARY KEY,
    target_type     VARCHAR(20)  NOT NULL             COMMENT 'plan_step',
    target_id       CHAR(9)      NOT NULL,
    required_reason VARCHAR(20)  NULL,
    decided_by      CHAR(9)      NULL,
    decision        VARCHAR(10)  NOT NULL             COMMENT 'approve | reject',
    ts              DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_action_approvals_target ON action_approvals (target_type, target_id);
