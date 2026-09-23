-- PRD-SIM-OBS-001 — Agent Observability & Governance (schema).
-- Run once on deploy.
--
-- Eight tables in three retention bands (PRD §6, §12):
--
--   1. Shared run header — agent_runs. One row per agent invocation, kept for
--      the product lifecycle. This is the source of truth the console reads and
--      that calibration and the World view can read; it is written by the single
--      emit hook in app/services/observability_service.py, never by a consumer.
--      It holds no raw PII: params are masked at capture.
--
--   2. Run detail — agent_run_tools, agent_events, agent_artifacts. Heavier and
--      more sensitive (tool I/O, decision traces, artifact summaries), so it is
--      purged on its own short window (obs_retention_detail_days, default 30)
--      without touching the header.
--
--   3. Governance & audit — observability_flags, guardrail_rules,
--      guardrail_policies, admin_access_audit. Kept long (flags 13 months,
--      access audit 24 months) because they are the record of what we governed
--      and of who looked at whose data.
--
-- Every retention window, sampling rate and redaction toggle is admin-editable
-- and lives in platform_settings under an obs_ prefix — see SETTINGS_SPEC in
-- app/services/observability_service.py. Nothing here is hardcoded in code.
--
-- All PKs are CHAR(9), from utils.id_gen.generate_id().


-- ── 1. Shared run header ─────────────────────────────────────────────────────
-- Deliberately carries no foreign key to agent_actions: an orchestrator run
-- opens its header before the AgentAction row exists, and a run blocked at
-- dispatch never produces one. user_id cascades so deleting a user takes their
-- telemetry with them (PRD §12).
CREATE TABLE agent_runs (
    id            CHAR(9)     PRIMARY KEY,
    user_id       CHAR(9)     NULL,
    simulation_id CHAR(9)     NULL,
    action_id     CHAR(9)     NULL                            COMMENT 'AgentAction this run produced, when there is one',
    agent_key     VARCHAR(50) NOT NULL                        COMMENT 'canonical action_type, alias-resolved',
    layer         VARCHAR(2)  NOT NULL                        COMMENT 'L0 free tier (pre-simulation), L1..L5 wealth layers, L6 orchestration',

    status        VARCHAR(20) NOT NULL DEFAULT 'running'      COMMENT 'running | success | error | blocked',
    model         VARCHAR(80) NULL,
    prompt_tokens     INT     NULL,
    completion_tokens INT     NULL,
    latency_ms    INT         NULL,

    -- The parameters the agent ran on, after masking. Never raw PII.
    params_json     JSON          NULL,
    -- The headline number the run produced, when it emitted one (METRICS block).
    -- Kept on the header so calibration and the World can read a value without
    -- touching the short-retention detail tables.
    projected_value DECIMAL(16,4) NULL,

    error_code      VARCHAR(80) NULL,
    dispatch_source VARCHAR(30) NULL                          COMMENT 'orchestrator | user_rerun | ...',
    is_sampled      TINYINT(1)  NOT NULL DEFAULT 1            COMMENT '0 = header only, detail not captured',
    flag_count      INT         NOT NULL DEFAULT 0            COMMENT 'denormalized so the runs list needs no join',

    started_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at   DATETIME    NULL,

    CONSTRAINT fk_ar_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
-- The console's hot reads: by-layer rollup, per-agent rollup, recent runs.
CREATE INDEX idx_ar_layer_started ON agent_runs (layer, started_at);
CREATE INDEX idx_ar_agent_started ON agent_runs (agent_key, started_at);
CREATE INDEX idx_ar_started       ON agent_runs (started_at);
CREATE INDEX idx_ar_user          ON agent_runs (user_id, started_at);
CREATE INDEX idx_ar_action        ON agent_runs (action_id);


-- ── 2. Run detail (purged on the short window) ───────────────────────────────
CREATE TABLE agent_run_tools (
    id             CHAR(9)      PRIMARY KEY,
    run_id         CHAR(9)      NOT NULL,
    tool_name      VARCHAR(60)  NOT NULL,
    sequence_index INT          NOT NULL DEFAULT 0,
    input_summary  VARCHAR(500) NULL                          COMMENT 'a summary, never the payload',
    output_summary VARCHAR(500) NULL,
    duration_ms    INT          NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'success'    COMMENT 'success | error | blocked',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_art_run FOREIGN KEY (run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
);
CREATE INDEX idx_art_run  ON agent_run_tools (run_id, sequence_index);
CREATE INDEX idx_art_tool ON agent_run_tools (tool_name, created_at);


CREATE TABLE agent_events (
    id           CHAR(9)     PRIMARY KEY,
    run_id       CHAR(9)     NOT NULL,
    type         VARCHAR(20) NOT NULL DEFAULT 'step'          COMMENT 'decision | step | warning',
    payload_json JSON        NULL,
    ts           DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_ae_run FOREIGN KEY (run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
);
CREATE INDEX idx_ae_run ON agent_events (run_id, ts);


-- A summary plus a reference, not a second copy of the deliverable (PRD §12).
-- withheld_content is the one exception: an output a blocking guardrail
-- quarantines is never written to the product store at all, so the only copy
-- lives here until an admin releases it or the detail window purges it.
CREATE TABLE agent_artifacts (
    id            CHAR(9)      PRIMARY KEY,
    run_id        CHAR(9)      NOT NULL,
    user_id       CHAR(9)      NULL,
    agent_key     VARCHAR(50)  NOT NULL,
    layer         VARCHAR(2)   NOT NULL,
    artifact_type VARCHAR(80)  NOT NULL                       COMMENT 'human label, e.g. "Rate Card"',
    summary_json  JSON         NULL,
    content_ref   VARCHAR(120) NULL                           COMMENT 'agent_action:<id> — where the real content lives',
    size          INT          NULL                           COMMENT 'characters',

    is_quarantined   TINYINT(1) NOT NULL DEFAULT 0,
    withheld_content TEXT       NULL                          COMMENT 'only ever set while quarantined',
    released_at      DATETIME   NULL,
    released_by      CHAR(9)    NULL,

    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_aa_run FOREIGN KEY (run_id) REFERENCES agent_runs (id) ON DELETE CASCADE
);
CREATE INDEX idx_aa_run   ON agent_artifacts (run_id);
CREATE INDEX idx_aa_layer ON agent_artifacts (layer, created_at);
CREATE INDEX idx_aa_type  ON agent_artifacts (artifact_type, created_at);


-- ── 3. Governance & audit (kept long) ────────────────────────────────────────
CREATE TABLE guardrail_rules (
    id              CHAR(9)      PRIMARY KEY,
    name            VARCHAR(80)  NOT NULL                     COMMENT 'slug shown in the flags list, e.g. invest-cap-55',
    scope           VARCHAR(20)  NOT NULL DEFAULT 'global'    COMMENT 'global | layer | agent',
    scope_ref       VARCHAR(50)  NULL                         COMMENT 'L5, or an agent_key; NULL when global',
    rule_type       VARCHAR(30)  NOT NULL                     COMMENT 'tool_allowlist | param_bound | output_filter | content_policy | rate_limit | model_constraint',
    definition_json JSON         NULL,
    -- Progressive by design (PRD §10, Overfitting): a new rule starts at log.
    action          VARCHAR(10)  NOT NULL DEFAULT 'log'       COMMENT 'log | warn | block',
    severity        VARCHAR(10)  NOT NULL DEFAULT 'medium'    COMMENT 'low | medium | high | critical',
    notes           VARCHAR(500) NULL,
    is_enabled      TINYINT(1)   NOT NULL DEFAULT 1,

    -- Effect measurement (PRD §9). Counters, so the rules table never scans flags.
    eval_count      INT          NOT NULL DEFAULT 0,
    fire_count      INT          NOT NULL DEFAULT 0,
    block_count     INT          NOT NULL DEFAULT 0,

    created_by      CHAR(9)      NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT uq_gr_name UNIQUE (name)
);
CREATE INDEX idx_gr_scope ON guardrail_rules (scope, scope_ref, is_enabled);


CREATE TABLE guardrail_policies (
    id            CHAR(9)      PRIMARY KEY,
    name          VARCHAR(80)  NOT NULL,
    description   VARCHAR(500) NULL,
    scope         VARCHAR(20)  NOT NULL DEFAULT 'global',
    scope_ref     VARCHAR(50)  NULL,
    rule_ids_json JSON         NULL,
    is_active     TINYINT(1)   NOT NULL DEFAULT 1,
    created_by    CHAR(9)      NULL,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT uq_gp_name UNIQUE (name)
);


-- A flag outlives the run detail it describes (13 months vs 30 days), so the
-- layer/agent/user context is denormalized onto the row rather than joined.
CREATE TABLE observability_flags (
    id          CHAR(9)      PRIMARY KEY,
    target_type VARCHAR(12)  NOT NULL                         COMMENT 'run | tool | artifact',
    target_id   CHAR(9)      NOT NULL,
    run_id      CHAR(9)      NULL,
    rule_id     CHAR(9)      NULL,
    rule_name   VARCHAR(80)  NULL,
    layer       VARCHAR(2)   NULL,
    agent_key   VARCHAR(50)  NULL,
    user_id     CHAR(9)      NULL,
    severity    VARCHAR(10)  NOT NULL DEFAULT 'medium',
    action      VARCHAR(10)  NOT NULL DEFAULT 'log'           COMMENT 'what the rule did: log | warn | block',
    reason      VARCHAR(500) NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_of_rule FOREIGN KEY (rule_id) REFERENCES guardrail_rules (id) ON DELETE SET NULL
);
CREATE INDEX idx_of_created ON observability_flags (created_at);
CREATE INDEX idx_of_rule    ON observability_flags (rule_id, created_at);
CREATE INDEX idx_of_run     ON observability_flags (run_id);


-- Governance integrity: the admins are themselves observed (PRD §12).
CREATE TABLE admin_access_audit (
    id              CHAR(9)      PRIMARY KEY,
    admin_user_id   CHAR(9)      NULL,
    action          VARCHAR(30)  NOT NULL                     COMMENT 'view_run | view_artifact | export | release_artifact',
    target_id       VARCHAR(120) NULL,
    subject_user_id CHAR(9)      NULL                         COMMENT 'whose data was looked at',
    ts              DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_aaa_admin ON admin_access_audit (admin_user_id, ts);
CREATE INDEX idx_aaa_ts    ON admin_access_audit (ts);
