-- Agent Execution Layer P2: actionable units of an artifact, and a log of the
-- verbs applied to artifacts. Run once on deploy. Additive; no data migration.
--
-- artifact_id is agent_actions.id (EXEC_PLAN §2 reuses agent_actions as the
-- artifact object). In P2 the only units written are per-recipient DRAFTS from
-- "Send to contacts" (kind 'variant', status 'draft'): prepared and previewed,
-- never sent. Sending arrives in P5, behind its own confirmation gate.
--
-- All PKs are CHAR(9), from utils.id_gen.generate_id().

CREATE TABLE artifact_units (
    id          CHAR(9)      PRIMARY KEY,
    artifact_id CHAR(9)      NOT NULL                     COMMENT 'agent_actions.id',
    batch_id    CHAR(9)      NULL                         COMMENT 'one prepare-drafts run',
    seq_index   INT          NOT NULL DEFAULT 0,
    kind        VARCHAR(20)  NOT NULL DEFAULT 'variant'   COMMENT 'step | entry | variant',
    contact_id  CHAR(9)      NULL,
    subject     VARCHAR(300) NULL,
    body_ref    MEDIUMTEXT   NULL                         COMMENT 'resolved body for this unit',
    warnings    TEXT         NULL                         COMMENT 'JSON list, e.g. unresolved placeholders',
    status      VARCHAR(20)  NOT NULL DEFAULT 'draft'     COMMENT 'draft | discarded',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_au_action FOREIGN KEY (artifact_id) REFERENCES agent_actions (id) ON DELETE CASCADE
);
CREATE INDEX ix_artifact_units_artifact ON artifact_units (artifact_id, status);

CREATE TABLE artifact_actions (
    id          CHAR(9)      PRIMARY KEY,
    artifact_id CHAR(9)      NOT NULL                     COMMENT 'agent_actions.id',
    unit_id     CHAR(9)      NULL,
    verb        VARCHAR(40)  NOT NULL                     COMMENT 'refine | export | duplicate | prepare_send | discard_drafts ...',
    actor       VARCHAR(10)  NOT NULL DEFAULT 'user'      COMMENT 'user | agent',
    actor_id    CHAR(9)      NULL,
    status      VARCHAR(20)  NOT NULL DEFAULT 'ok',
    result_ref  TEXT         NULL                         COMMENT 'small JSON summary',
    ts          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_aa_action FOREIGN KEY (artifact_id) REFERENCES agent_actions (id) ON DELETE CASCADE
);
CREATE INDEX ix_artifact_actions_artifact ON artifact_actions (artifact_id, ts);
