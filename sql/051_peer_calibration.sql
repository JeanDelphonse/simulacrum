-- PRD-SIM-PEER-001 — Peer Calibration (schema).
-- Run once on deploy.
--
-- An owner marks specific Layer 1 / Layer 2 agents shareable, invites a trusted
-- circle, and invited peers propose parameter values — blind. At the next cycle
-- run the owner's value (prior) and the peer proposals (likelihood) are blended
-- into a recommendation the owner accepts or ignores. Nothing here ever changes
-- a live parameter: acceptance writes through the existing agent_context store,
-- which stays the single source of truth for what an agent runs on (PRD §4).
--
-- Every threshold, weight, cap and window is admin-editable and lives in
-- platform_settings under a peer_ prefix — see SETTINGS_SPEC in
-- app/services/peer_service.py. Nothing is hardcoded in code (PRD §7, §8).
--
-- All PKs are CHAR(9), from utils.id_gen.generate_id().


-- ── Sharing — which agents a peer may propose on (PRD §5) ────────────────────
-- Layer is constrained to L1/L2 in the service rather than by a CHECK, because
-- the ceiling is itself admin-configurable (peer_max_layer). The row is the
-- grant: no agent_shares row means no peer can propose on that agent, and
-- un-sharing flips is_shareable rather than deleting, so the suggestion history
-- that references it survives (PRD §5, revocation).
CREATE TABLE agent_shares (
    id            CHAR(9)     PRIMARY KEY,
    owner_user_id CHAR(9)     NOT NULL,
    simulation_id CHAR(9)     NOT NULL,
    agent_key     VARCHAR(50) NOT NULL                     COMMENT 'canonical action_type, alias-resolved',
    layer         VARCHAR(2)  NOT NULL                     COMMENT 'L1 | L2 only - upper layers are never offered',
    is_shareable  TINYINT(1)  NOT NULL DEFAULT 1,
    created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT uq_ash_sim_agent UNIQUE (simulation_id, agent_key),
    CONSTRAINT fk_ash_owner FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX idx_ash_owner ON agent_shares (owner_user_id, is_shareable);


-- ── The invited circle (PRD §5) ─────────────────────────────────────────────
-- One table for both directions. invite_out is the owner reaching a peer (the
-- peer may not resolve to a user yet, hence the nullable peer_user_id plus the
-- invite_email it was addressed to); request_in is a bio-page visitor asking to
-- join. Neither grants anything until status = accepted.
CREATE TABLE peer_invites (
    id            CHAR(9)      PRIMARY KEY,
    owner_user_id CHAR(9)      NOT NULL,
    peer_user_id  CHAR(9)      NULL                        COMMENT 'NULL until an invited email registers',
    invite_email  VARCHAR(160) NULL                        COMMENT 'what an invite_out was addressed to',
    peer_label    VARCHAR(160) NULL                        COMMENT 'display name captured at invite time',
    direction     VARCHAR(12)  NOT NULL DEFAULT 'invite_out' COMMENT 'invite_out | request_in',
    status        VARCHAR(10)  NOT NULL DEFAULT 'pending'  COMMENT 'pending | accepted | declined | revoked',
    message       VARCHAR(500) NULL                        COMMENT 'the note on a request_in',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at   DATETIME     NULL,

    CONSTRAINT fk_pi_owner FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE CASCADE
);
-- The hot read is "is this viewer in this owner's circle" - one indexed probe.
CREATE INDEX idx_pi_owner_peer   ON peer_invites (owner_user_id, peer_user_id, status);
CREATE INDEX idx_pi_owner_status ON peer_invites (owner_user_id, status);
CREATE INDEX idx_pi_peer         ON peer_invites (peer_user_id, status);
CREATE INDEX idx_pi_email        ON peer_invites (invite_email, status);


-- ── Proposals (PRD §4 step 2, §6) ───────────────────────────────────────────
-- param_kind carries the three shapes §6 distinguishes: a bare number, a choice
-- from a declared option set (§6 groups these with numbers as "numeric /
-- structured"), and free text. The kind decides both what the peer is shown and
-- how the blend is computed - numbers and choices by math, text by LLM
-- synthesis (PRD §7).
--
-- guardrail_ok records the OBS-001 verdict at submission. A 0 row is kept
-- deliberately: it is the record of what was rejected and never entered the
-- pool (PRD §14, guardrailed and audited).
CREATE TABLE parameter_suggestions (
    id            CHAR(9)       PRIMARY KEY,
    share_id      CHAR(9)       NOT NULL,
    peer_user_id  CHAR(9)       NOT NULL,
    agent_key     VARCHAR(50)   NOT NULL,
    param_key     VARCHAR(100)  NOT NULL,
    param_kind    VARCHAR(10)   NOT NULL DEFAULT 'numeric' COMMENT 'numeric | choice | text',
    value_json    JSON          NULL                       COMMENT '{"value": ...} - the proposal, verbatim',
    -- Parsed once at submission so the blend never re-parses and the outlier
    -- trim reads a plain numeric column.
    numeric_value DECIMAL(18,4) NULL,
    note          VARCHAR(500)  NULL                       COMMENT 'optional why, shown with the proposal',
    status        VARCHAR(10)   NOT NULL DEFAULT 'pending' COMMENT 'pending | blended | rejected | discarded',
    guardrail_ok  TINYINT(1)    NOT NULL DEFAULT 1,
    reject_reason VARCHAR(500)  NULL,
    is_quality    TINYINT(1)    NOT NULL DEFAULT 0         COMMENT '1 = counts toward the reciprocity gate (PRD §8)',
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_ps_share FOREIGN KEY (share_id) REFERENCES agent_shares (id) ON DELETE CASCADE,
    CONSTRAINT fk_ps_peer  FOREIGN KEY (peer_user_id) REFERENCES users (id) ON DELETE CASCADE
);
-- The blend's read: every live proposal for one parameter.
CREATE INDEX idx_ps_share_param  ON parameter_suggestions (share_id, param_key, status);
-- The rate-limit and dedupe reads: this peer, this parameter, recently.
CREATE INDEX idx_ps_peer_param   ON parameter_suggestions (peer_user_id, share_id, param_key, created_at);
CREATE INDEX idx_ps_peer_created ON parameter_suggestions (peer_user_id, created_at);


-- ── Blends (PRD §7) ─────────────────────────────────────────────────────────
-- One row per (share, param, cycle): the posterior, the distribution the owner
-- is shown, and their decision. owner_prior_json keeps the value the owner held
-- *at blend time*, so the drawer can still show "your $150" after they change
-- it.
CREATE TABLE suggestion_blends (
    id               CHAR(9)       PRIMARY KEY,
    share_id         CHAR(9)       NOT NULL,
    agent_key        VARCHAR(50)   NOT NULL,
    param_key        VARCHAR(100)  NOT NULL,
    param_kind       VARCHAR(10)   NOT NULL DEFAULT 'numeric',
    owner_prior_json JSON          NULL                     COMMENT '{"value": ...} the owner held when this resolved',
    posterior_json   JSON          NULL                     COMMENT '{"value":..,"cluster":..,"mean":..,"method":..}',
    range_low        DECIMAL(18,4) NULL,
    range_high       DECIMAL(18,4) NULL,
    spread           DECIMAL(18,4) NULL                     COMMENT 'standard deviation of the surviving proposals',
    spread_pct       DECIMAL(10,4) NULL                     COMMENT 'spread over the cluster - the explore/exploit read',
    -- Not `signal`: that is a reserved word in MySQL (the SIGNAL statement).
    spread_signal    VARCHAR(10)   NULL                     COMMENT 'tight | wide - exploit the cluster, or keep collecting',
    rationale        VARCHAR(1000) NULL,
    n_suggestions    INT           NOT NULL DEFAULT 0       COMMENT 'proposals that survived the trim',
    n_trimmed        INT           NOT NULL DEFAULT 0       COMMENT 'proposals the outlier trim dropped',
    cycle_id         CHAR(9)       NULL,
    owner_decision   VARCHAR(10)   NOT NULL DEFAULT 'pending' COMMENT 'accepted | kept | pending',
    decided_at       DATETIME      NULL,
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_sb_share FOREIGN KEY (share_id) REFERENCES agent_shares (id) ON DELETE CASCADE
);
-- A parameter resolves at most once per cycle, so this doubles as the
-- idempotence guard for a cycle that gets re-run (PRD §12, Caching).
CREATE UNIQUE INDEX uq_sb_share_param_cycle ON suggestion_blends (share_id, param_key, cycle_id);
CREATE INDEX idx_sb_share_decision ON suggestion_blends (share_id, owner_decision);
CREATE INDEX idx_sb_created        ON suggestion_blends (created_at);


-- ── The reciprocity gate (PRD §8) ───────────────────────────────────────────
-- One row per peer, on a rolling window. quality_suggestions_count only counts
-- substantive proposals: within guardrail bounds, not a duplicate, not trimmed
-- as an outlier. unlocked is denormalized so the gate check is a single read.
CREATE TABLE reciprocity_ledger (
    id                        CHAR(9)    PRIMARY KEY,
    peer_user_id              CHAR(9)    NOT NULL,
    quality_suggestions_count INT        NOT NULL DEFAULT 0,
    total_suggestions_count   INT        NOT NULL DEFAULT 0,
    unlocked                  TINYINT(1) NOT NULL DEFAULT 0,
    window_start              DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT uq_rl_peer UNIQUE (peer_user_id),
    CONSTRAINT fk_rl_peer FOREIGN KEY (peer_user_id) REFERENCES users (id) ON DELETE CASCADE
);
