-- SME Console conversational guide (PRD "SME Console Conversational Guide").
-- Run once on deploy. Additive.
--
-- A user-facing AI guide (Simi or one of ten illustrated guides) that helps a
-- user set one goal per income layer through a GROW conversation grounded in
-- their profile and simulations. Not the human-SME console (/sme).
--
-- Transcripts (sme_turns.text_enc), session summaries and briefing snapshots
-- are encrypted at rest; no staff or employer route reads them. Approved goal
-- cards and accepted skills are permanent profile data and survive conversation
-- deletion. goal_card_agent_changes.applied_at stays NULL until goal-driven
-- agent configuration (EXEC configuration path) is switched on.
--
-- Guides are seeded by the app on first use. Settings are platform_settings
-- (guide_*); the beta gate uses feature_flags / user_feature_overrides
-- (feature_key 'sme_console', from sql/060).
--
-- PKs are CHAR(9) from utils.id_gen.generate_id().

CREATE TABLE sme_guides (
    id                CHAR(9)      PRIMARY KEY,
    slug              VARCHAR(30)  NOT NULL UNIQUE,
    display_name      VARCHAR(60)  NOT NULL,
    kind              VARCHAR(12)  NOT NULL DEFAULT 'illustrated' COMMENT 'mascot | illustrated | video',
    style_label       VARCHAR(80)  NULL,
    voice_label       VARCHAR(80)  NULL,
    voice_ref         VARCHAR(120) NULL COMMENT 'neural voice id (provider TBD)',
    gender_hint       VARCHAR(2)   NULL,
    voice_index       INT          NOT NULL DEFAULT 0,
    pitch             FLOAT        NOT NULL DEFAULT 1,
    rate              FLOAT        NOT NULL DEFAULT 1,
    vendor_avatar_ref VARCHAR(120) NULL,
    appearance_json   TEXT         NULL,
    enabled           TINYINT(1)   NOT NULL DEFAULT 1,
    sort_order        INT          NOT NULL DEFAULT 0
);

CREATE TABLE user_guide_prefs (
    id            CHAR(9)    PRIMARY KEY,
    user_id       CHAR(9)    NOT NULL UNIQUE,
    guide_id      CHAR(9)    NULL,
    voice_enabled TINYINT(1) NOT NULL DEFAULT 1,
    video_enabled TINYINT(1) NOT NULL DEFAULT 0,
    updated_at    DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sme_sessions (
    id               CHAR(9)     PRIMARY KEY,
    user_id          CHAR(9)     NOT NULL,
    guide_id         CHAR(9)     NULL,
    mode             VARCHAR(10) NOT NULL DEFAULT 'animated' COMMENT 'animated | video | text',
    layer            INT         NOT NULL DEFAULT 1,
    grow_step        CHAR(1)     NULL COMMENT 'G | R | O | W',
    status           VARCHAR(10) NOT NULL DEFAULT 'active',
    started_at       DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_turn_at     DATETIME    NULL,
    ended_at         DATETIME    NULL,
    turn_count       INT         NOT NULL DEFAULT 0,
    layers_covered   TEXT        NULL,
    model_cost_cents FLOAT       NOT NULL DEFAULT 0,
    media_cost_cents FLOAT       NOT NULL DEFAULT 0,
    input_tokens     INT         NOT NULL DEFAULT 0,
    output_tokens    INT         NOT NULL DEFAULT 0,
    summary_enc      TEXT        NULL COMMENT 'encrypted',
    crisis_flag      TINYINT(1)  NOT NULL DEFAULT 0
);
CREATE INDEX ix_sme_sessions_user ON sme_sessions (user_id, started_at);

CREATE TABLE sme_turns (
    id               CHAR(9)    PRIMARY KEY,
    session_id       CHAR(9)    NOT NULL,
    speaker          VARCHAR(6) NOT NULL COMMENT 'user | guide',
    text_enc         TEXT       NOT NULL COMMENT 'encrypted',
    input_mode       VARCHAR(6) NULL COMMENT 'voice | typed',
    cited_source_ids TEXT       NULL,
    ui_actions_json  TEXT       NULL,
    layer            INT        NULL,
    grow_step        CHAR(1)    NULL,
    created_at       DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_sme_turns_session ON sme_turns (session_id, created_at);

CREATE TABLE sme_briefings (
    id                   CHAR(9)  PRIMARY KEY,
    session_id           CHAR(9)  NOT NULL,
    source_snapshot_json TEXT     NOT NULL COMMENT 'encrypted fact ids and values',
    built_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_sme_briefings_session_id ON sme_briefings (session_id);

CREATE TABLE skill_suggestions (
    id            CHAR(9)      PRIMARY KEY,
    user_id       CHAR(9)      NOT NULL,
    session_id    CHAR(9)      NULL,
    skill_name    VARCHAR(120) NOT NULL,
    evidence_ref  VARCHAR(60)  NULL,
    evidence_text VARCHAR(400) NULL,
    status        VARCHAR(10)  NOT NULL DEFAULT 'proposed' COMMENT 'proposed | accepted | declined',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at    DATETIME     NULL
);
CREATE INDEX ix_skill_suggestions_user ON skill_suggestions (user_id, status);

CREATE TABLE goal_cards (
    id             CHAR(9)      PRIMARY KEY,
    user_id        CHAR(9)      NOT NULL,
    session_id     CHAR(9)      NULL,
    layer          INT          NOT NULL,
    goal_text      VARCHAR(400) NULL,
    measure        VARCHAR(300) NULL,
    target         VARCHAR(120) NULL,
    deadline       VARCHAR(40)  NULL,
    evidence_refs  TEXT         NULL,
    simulation_ref VARCHAR(60)  NULL,
    status         VARCHAR(10)  NOT NULL DEFAULT 'draft' COMMENT 'draft | approved | edited | retired',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_at    DATETIME     NULL,
    review_at      DATETIME     NULL
);
CREATE INDEX ix_goal_cards_user ON goal_cards (user_id, layer, status);

CREATE TABLE goal_card_agent_changes (
    id           CHAR(9)      PRIMARY KEY,
    goal_card_id CHAR(9)      NOT NULL,
    agent_id     VARCHAR(50)  NOT NULL COMMENT 'agents.json action_type',
    setting_key  VARCHAR(80)  NULL,
    old_value    VARCHAR(300) NULL,
    new_value    VARCHAR(300) NULL,
    applied_at   DATETIME     NULL COMMENT 'NULL until goal-driven agent config is live',
    exec_ref     VARCHAR(60)  NULL
);
CREATE INDEX ix_goal_card_agent_changes_goal_card_id ON goal_card_agent_changes (goal_card_id);
