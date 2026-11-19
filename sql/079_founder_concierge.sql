-- Founder Track: concierge onboarding (FOUNDER_CONCIERGE_PLAN.md section 3).
-- Run once on deploy, after 078.
--
-- A concierge venture is a normal founder_venture with track 'concierge',
-- status 'concierge' and NO owner until the primary founder accepts and pays.
-- Self-serve ventures always set owner_user_id, so their behaviour is unchanged.
--
-- concierge_case     one per concierge venture: state, primary founder, consent,
--                    fee override, share window, payment and the pre-filled fields.
-- concierge_edit     every admin edit to a pre-filled field or a finding, with the
--                    AI's original value (the AI-vs-admin record).
-- concierge_message  revision requests from the founder and admin replies.
--
-- venture_artifact.encrypted = 1 means the stored file and text_content are
-- Fernet-encrypted with ENCRYPTION_KEY (concierge uploads). Self-serve rows stay 0.
--
-- PKs are CHAR(9) uppercase from utils.id_gen.generate_pk().

ALTER TABLE founder_venture
    MODIFY owner_user_id VARCHAR(9) NULL COMMENT 'NULL = concierge venture not yet accepted (sql/079)';

ALTER TABLE venture_artifact
    ADD COLUMN encrypted TINYINT(1) NOT NULL DEFAULT 0
    COMMENT '1 = file and text_content are Fernet-encrypted (concierge uploads, sql/079)';

CREATE TABLE concierge_case (
    id                   CHAR(9)       PRIMARY KEY,
    venture_id           VARCHAR(9)    NOT NULL,
    state                VARCHAR(20)   NOT NULL DEFAULT 'draft'
                         COMMENT 'draft | documents_uploaded | evaluating | ready_for_review | confirmed | shared | viewed | revision_requested | accepted_paid | active | declined | expired | deleted',
    primary_name         VARCHAR(200)  NULL,
    primary_email        VARCHAR(255)  NULL,
    cofounder_emails     JSON          NULL,
    source               VARCHAR(120)  NULL,
    referrer             VARCHAR(200)  NULL,
    notes                TEXT          NULL,
    consent_given_by     VARCHAR(200)  NULL,
    consent_at           DATETIME      NULL,
    consent_version      VARCHAR(40)   NULL,
    nda                  TINYINT(1)    NOT NULL DEFAULT 0,
    fee_override_cents   INT           NULL,
    fee_override_reason  VARCHAR(500)  NULL,
    fee_override_by      VARCHAR(9)    NULL,
    confirmed_at         DATETIME      NULL,
    confirmed_by         VARCHAR(9)    NULL,
    shared_at            DATETIME      NULL,
    expires_at           DATETIME      NULL,
    extended_at          DATETIME      NULL,
    extended_by          VARCHAR(9)    NULL,
    extend_reason        VARCHAR(500)  NULL,
    viewed_at            DATETIME      NULL,
    accepted_at          DATETIME      NULL,
    accepted_user_id     VARCHAR(9)    NULL,
    declined_at          DATETIME      NULL,
    decline_reason       VARCHAR(500)  NULL,
    expired_at           DATETIME      NULL,
    documents_deleted_at DATETIME      NULL,
    invite_token_hash    VARCHAR(64)   NULL,
    invite_version       INT           NOT NULL DEFAULT 0,
    invite_sent_at       DATETIME      NULL,
    reminders_sent       JSON          NULL,
    payment_intent_id    VARCHAR(255)  NULL,
    amount_cents         INT           NULL,
    refunded_at          DATETIME      NULL,
    prefill              JSON          NULL COMMENT '{field: {value, status: ai|admin|needs_input, citation}}',
    created_by           VARCHAR(9)    NULL,
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_concierge_case_venture (venture_id),
    KEY ix_concierge_case_state (state),
    KEY ix_concierge_case_email (primary_email),
    UNIQUE KEY uq_concierge_case_token (invite_token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE concierge_edit (
    id          CHAR(9)      PRIMARY KEY,
    case_id     VARCHAR(9)   NOT NULL,
    entity      VARCHAR(10)  NOT NULL COMMENT 'prefill | finding',
    entity_id   VARCHAR(40)  NULL COMMENT 'prefill field name, or evaluation_finding.id',
    field       VARCHAR(40)  NOT NULL,
    ai_value    TEXT         NULL,
    before_value TEXT        NULL,
    after_value TEXT         NULL,
    by_user_id  VARCHAR(9)   NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_concierge_edit_case (case_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE concierge_message (
    id          CHAR(9)      PRIMARY KEY,
    case_id     VARCHAR(9)   NOT NULL,
    direction   VARCHAR(10)  NOT NULL COMMENT 'founder | admin',
    body        TEXT         NOT NULL,
    by_user_id  VARCHAR(9)   NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_concierge_message_case (case_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
