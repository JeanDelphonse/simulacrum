-- Founder Track (FOUNDER_PLAN.md section 3): evaluation tables.
-- Run once on deploy, after 069. Additive.
--
-- evaluation_run       one scoring pass over the venture's current artifacts.
-- evaluation_finding   a gap, contradiction, assumption or strength, with the
--                      cited source passage and a Beta-posterior confidence.
-- finding_rating       the founder's "is this fair?" rating of a finding.
-- founder_eval_cache   one model answer per (artifact version, dimension).
--
-- PKs are CHAR(9) uppercase from utils.id_gen.generate_pk().

CREATE TABLE evaluation_run (
    id                 CHAR(9)      PRIMARY KEY,
    venture_id         VARCHAR(9)   NOT NULL,
    artifact_versions  JSON         NULL COMMENT '{kind: version}',
    status             VARCHAR(10)  NOT NULL DEFAULT 'running' COMMENT 'running | done | failed',
    scores             JSON         NULL COMMENT '{dimension: 0-100}',
    overall_score      FLOAT        NULL,
    rung_at_run        INT          NULL,
    obs_run_id         VARCHAR(9)   NULL,
    cost_cents         INT          NOT NULL DEFAULT 0,
    started_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at        DATETIME     NULL,
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_evaluation_run_venture (venture_id, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE evaluation_finding (
    id                 CHAR(9)       PRIMARY KEY,
    venture_id         VARCHAR(9)    NOT NULL,
    evaluation_run_id  VARCHAR(9)    NOT NULL,
    artifact_id        VARCHAR(9)    NULL COMMENT 'venture_artifact.id',
    dimension          VARCHAR(20)   NOT NULL COMMENT 'problem | customer | market | solution | gtm | business_model | operations | team | fundraising',
    layer              INT           NOT NULL DEFAULT 1 COMMENT '1-5',
    ftype              VARCHAR(14)   NOT NULL COMMENT 'gap | contradiction | assumption | strength',
    severity           VARCHAR(8)    NOT NULL DEFAULT 'medium' COMMENT 'low | medium | high',
    title              VARCHAR(300)  NOT NULL,
    detail             TEXT          NULL,
    alpha              FLOAT         NOT NULL DEFAULT 1,
    beta               FLOAT         NOT NULL DEFAULT 1,
    confidence         FLOAT         NOT NULL DEFAULT 0.5,
    citation_text      TEXT          NULL,
    citation_ref       VARCHAR(60)   NULL COMMENT 'artifact kind and version',
    recommended_agent  VARCHAR(60)   NULL,
    superseded_at      DATETIME      NULL,
    created_at         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_evaluation_finding_venture (venture_id, superseded_at),
    KEY ix_evaluation_finding_run (evaluation_run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE finding_rating (
    id          CHAR(9)      PRIMARY KEY,
    finding_id  VARCHAR(9)   NOT NULL,
    user_id     VARCHAR(9)   NOT NULL,
    fair        TINYINT(1)   NOT NULL DEFAULT 1,
    comment     TEXT         NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_finding_rating_finding (finding_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE founder_eval_cache (
    id           CHAR(9)      PRIMARY KEY,
    artifact_id  VARCHAR(9)   NOT NULL COMMENT 'venture_artifact.id',
    dimension    VARCHAR(20)  NOT NULL,
    payload      JSON         NULL,
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_founder_eval_cache (artifact_id, dimension)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
