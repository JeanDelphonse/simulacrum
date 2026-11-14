-- Founder Track (FOUNDER_PLAN.md section 3): team, plan and proof tables.
-- Run once on deploy, after 070. Additive.
--
-- agent_blueprint       a founder agent recommended for the venture, with defaults.
-- agent_param_override  a founder's change to a default, kept with the old value.
-- venture_plan_step     one step of the venture's plan (locked -> ready -> ... -> done).
-- venture_step_event    every state change; the workspace SSE stream polls it.
-- venture_agent_output  what an agent produced for a step, versioned.
-- proof_plan / proof_result  the proof-of-concept path (evidence rungs 3-5).
--
-- Column names avoid session_id / run_id / step_id (plan_step_id, obs_run_id).
-- PKs are CHAR(9) uppercase from utils.id_gen.generate_pk().

CREATE TABLE agent_blueprint (
    id          CHAR(9)      PRIMARY KEY,
    venture_id  VARCHAR(9)   NOT NULL,
    agent_key   VARCHAR(60)  NOT NULL,
    layer       INT          NOT NULL,
    status      VARCHAR(12)  NOT NULL DEFAULT 'recommended' COMMENT 'recommended | accepted | declined',
    finding_id  VARCHAR(9)   NULL,
    params      JSON         NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_agent_blueprint_venture (venture_id, layer, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE agent_param_override (
    id              CHAR(9)      PRIMARY KEY,
    venture_id      VARCHAR(9)   NOT NULL,
    blueprint_id    VARCHAR(9)   NULL,
    layer           INT          NULL COMMENT 'set for a layer-wide override',
    param_key       VARCHAR(30)  NOT NULL,
    previous_value  TEXT         NULL,
    new_value       TEXT         NULL,
    user_id         VARCHAR(9)   NOT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_agent_param_override_venture (venture_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE venture_plan_step (
    id               CHAR(9)       PRIMARY KEY,
    venture_id       VARCHAR(9)    NOT NULL,
    layer            INT           NOT NULL,
    kind             VARCHAR(10)   NOT NULL DEFAULT 'agent' COMMENT 'agent | manual | alignment | proof | legal',
    agent_key        VARCHAR(60)   NULL,
    title            VARCHAR(300)  NOT NULL,
    description      TEXT          NULL,
    state            VARCHAR(18)   NOT NULL DEFAULT 'locked' COMMENT 'locked | ready | in_progress | awaiting_approval | done | skipped',
    prereq_keys      JSON          NULL,
    effort           INT           NOT NULL DEFAULT 3 COMMENT '1-5',
    sort_order       INT           NOT NULL DEFAULT 0,
    owner_member_id  VARCHAR(9)    NULL,
    output_id        VARCHAR(9)    NULL,
    evidence_note    TEXT          NULL,
    skip_reason      TEXT          NULL,
    completed_at     DATETIME      NULL,
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_venture_plan_step_venture (venture_id, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE venture_step_event (
    id             CHAR(9)      PRIMARY KEY,
    venture_id     VARCHAR(9)   NOT NULL,
    plan_step_id   VARCHAR(9)   NULL COMMENT 'NULL for venture-level events',
    from_state     VARCHAR(18)  NULL,
    to_state       VARCHAR(40)  NULL,
    actor_user_id  VARCHAR(9)   NULL,
    actor_kind     VARCHAR(8)   NOT NULL DEFAULT 'system' COMMENT 'founder | agent | system',
    note           TEXT         NULL,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_venture_step_event_venture (venture_id, created_at),
    KEY ix_venture_step_event_step (plan_step_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE venture_agent_output (
    id            CHAR(9)      PRIMARY KEY,
    venture_id    VARCHAR(9)   NOT NULL,
    plan_step_id  VARCHAR(9)   NOT NULL,
    agent_key     VARCHAR(60)  NOT NULL,
    content       LONGTEXT     NULL,
    version       INT          NOT NULL DEFAULT 1,
    obs_run_id    VARCHAR(9)   NULL,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_venture_agent_output_step (plan_step_id, version),
    KEY ix_venture_agent_output_venture (venture_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE proof_plan (
    id                CHAR(9)       PRIMARY KEY,
    venture_id        VARCHAR(9)    NOT NULL,
    customer_name     VARCHAR(200)  NULL,
    pain              TEXT          NULL,
    baseline          TEXT          NULL,
    smallest_build    TEXT          NULL,
    success_criteria  JSON          NULL,
    duration          VARCHAR(100)  NULL,
    provides          JSON          NULL,
    on_success        TEXT          NULL,
    status            VARCHAR(10)   NOT NULL DEFAULT 'draft' COMMENT 'draft | agreed | complete',
    agreed_at         DATETIME      NULL,
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_proof_plan_venture (venture_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE proof_result (
    id                CHAR(9)      PRIMARY KEY,
    venture_id        VARCHAR(9)   NOT NULL,
    proof_plan_id     VARCHAR(9)   NOT NULL,
    results           JSON         NULL,
    measured          TINYINT(1)   NOT NULL DEFAULT 0,
    benefits_text     TEXT         NULL,
    quote_text        TEXT         NULL,
    quote_permission  TINYINT(1)   NOT NULL DEFAULT 0,
    paid_at           DATETIME     NULL,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_proof_result_plan (proof_plan_id),
    KEY ix_proof_result_venture (venture_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
