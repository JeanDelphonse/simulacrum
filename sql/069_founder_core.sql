-- Founder Track (SIM-PRD-FND-001, FOUNDER_PLAN.md section 3): core tables.
-- Run once on deploy, before 070-074. Additive: no existing table changes.
--
-- founder_venture    one venture per owner account (owner_user_id UNIQUE).
-- venture_member     the owner and co-founders (invited -> active -> left).
-- venture_artifact   uploaded or transcribed documents, versioned per kind.
-- founder_run_meta   founder context for one observability run (agent_runs.id).
--
-- PKs are CHAR(9) uppercase from utils.id_gen.generate_pk(). Prices, caps and
-- switches are platform_settings (fnd_*) / feature_flags rows, created on first
-- use; the defaults live in app/services/founder_config.py.

CREATE TABLE founder_venture (
    id                    CHAR(9)       PRIMARY KEY,
    owner_user_id         VARCHAR(9)    NOT NULL,
    name                  VARCHAR(200)  NOT NULL,
    one_liner             VARCHAR(300)  NULL,
    stage                 VARCHAR(12)   NOT NULL DEFAULT 'idea' COMMENT 'idea | pre-seed | seed | series-a',
    status                VARCHAR(10)   NOT NULL DEFAULT 'active' COMMENT 'active | paused | archived',
    rung                  INT           NOT NULL DEFAULT 1 COMMENT 'evidence ladder 1-5',
    track                 VARCHAR(12)   NOT NULL DEFAULT 'standalone' COMMENT 'standalone | addon',
    paid_at               DATETIME      NULL,
    payment_intent_id     VARCHAR(255)  NULL,
    amount_cents          INT           NULL,
    paused_at             DATETIME      NULL,
    support_access_until  DATETIME      NULL,
    checkin_cadence_days  INT           NULL,
    created_at            DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_founder_venture_owner (owner_user_id),
    KEY ix_founder_venture_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE venture_member (
    id            CHAR(9)       PRIMARY KEY,
    venture_id    VARCHAR(9)    NOT NULL,
    user_id       VARCHAR(9)    NULL,
    invite_email  VARCHAR(255)  NULL,
    invite_token  VARCHAR(64)   NULL,
    role          VARCHAR(10)   NOT NULL DEFAULT 'cofounder' COMMENT 'owner | cofounder',
    title         VARCHAR(120)  NULL,
    status        VARCHAR(10)   NOT NULL DEFAULT 'invited' COMMENT 'invited | active | left',
    joined_at     DATETIME      NULL,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_venture_member_invite_token (invite_token),
    KEY ix_venture_member_venture (venture_id, status),
    KEY ix_venture_member_user (user_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE venture_artifact (
    id            CHAR(9)       PRIMARY KEY,
    venture_id    VARCHAR(9)    NOT NULL,
    kind          VARCHAR(20)   NOT NULL COMMENT 'startup_plan | exec_summary | ops_plan | pitch_deck | financial_model | pitch_transcript',
    version       INT           NOT NULL DEFAULT 1,
    is_current    TINYINT(1)    NOT NULL DEFAULT 1,
    filename      VARCHAR(255)  NULL,
    file_path     VARCHAR(500)  NULL,
    text_content  LONGTEXT      NULL,
    char_count    INT           NOT NULL DEFAULT 0,
    uploaded_by   VARCHAR(9)    NULL,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_venture_artifact_kind (venture_id, kind, is_current)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE founder_run_meta (
    id              CHAR(9)       PRIMARY KEY,
    obs_run_id      VARCHAR(9)    NOT NULL COMMENT 'agent_runs.id',
    venture_id      VARCHAR(9)    NOT NULL,
    plan_step_id    VARCHAR(9)    NULL,
    agent_key       VARCHAR(60)   NOT NULL,
    `trigger`       VARCHAR(10)   NOT NULL DEFAULT 'founder' COMMENT 'dispatch | founder | schedule | rerun',
    autonomy_level  INT           NULL,
    approved_by     VARCHAR(9)    NULL,
    prompt_version  VARCHAR(40)   NULL,
    cost_cents      INT           NOT NULL DEFAULT 0,
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_founder_run_meta_obs_run (obs_run_id),
    KEY ix_founder_run_meta_venture (venture_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
