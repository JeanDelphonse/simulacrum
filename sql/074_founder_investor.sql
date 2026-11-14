-- Founder Track Phase 3 (FOUNDER_PLAN.md section 3): investor connection tables.
-- Run once on deploy, after 073. Additive. The tables exist from day one, but
-- nothing writes to them until the founder_investor switch is on, and that
-- switch refuses to turn on before an admin records the counsel sign-off date
-- (platform setting fnd_counsel_signoff_at).
--
-- compliance_profile       the offering exemption the founder's lawyer chose, and consents.
-- investor_link            private links to the investor view.
-- investor_access          who may see the investor view (invited, paid, requested).
-- investor_update          founder-approved updates to opted-in investors.
-- investor_profile         admin-curated directory (GLOBAL: not owned by a user).
-- investor_target          the venture's target list.
-- intro_path               connector contacts (found by deterministic code only).
-- venture_outreach_action  drafted / approved / sent actions under the autonomy matrix.
-- founder_alert_rule       admin alert thresholds (GLOBAL).
--
-- PKs are CHAR(9) uppercase from utils.id_gen.generate_pk().

CREATE TABLE compliance_profile (
    id                   CHAR(9)      PRIMARY KEY,
    venture_id           VARCHAR(9)   NOT NULL,
    exemption            VARCHAR(10)  NOT NULL DEFAULT 'undecided' COMMENT '506b | 506c | undecided',
    recorded_by          VARCHAR(9)   NULL,
    recorded_at          DATETIME     NULL,
    mailbox_consent_at   DATETIME     NULL,
    contacts_consent_at  DATETIME     NULL,
    created_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_compliance_profile_venture (venture_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE investor_link (
    id               CHAR(9)       PRIMARY KEY,
    venture_id       VARCHAR(9)    NOT NULL,
    startup_page_id  VARCHAR(9)    NOT NULL,
    label            VARCHAR(200)  NULL,
    token            VARCHAR(64)   NOT NULL,
    investor_email   VARCHAR(255)  NULL,
    created_by       VARCHAR(9)    NULL,
    revoked_at       DATETIME      NULL,
    opened_at        DATETIME      NULL,
    open_count       INT           NOT NULL DEFAULT 0,
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_investor_link_token (token),
    KEY ix_investor_link_venture (venture_id),
    KEY ix_investor_link_page (startup_page_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE investor_access (
    id                 CHAR(9)       PRIMARY KEY,
    startup_page_id    VARCHAR(9)    NOT NULL,
    email              VARCHAR(255)  NOT NULL,
    kind               VARCHAR(10)   NOT NULL DEFAULT 'requested' COMMENT 'invited | paid | requested',
    investor_link_id   VARCHAR(9)    NULL,
    stripe_session_id  VARCHAR(255)  NULL,
    payment_intent_id  VARCHAR(255)  NULL,
    amount_cents       INT           NULL,
    status             VARCHAR(10)   NOT NULL DEFAULT 'pending' COMMENT 'pending | active | requested | denied | refunded',
    approved_by        VARCHAR(9)    NULL,
    opted_in_updates   TINYINT(1)    NOT NULL DEFAULT 0,
    refunded_at        DATETIME      NULL,
    created_at         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_investor_access_page_email (startup_page_id, email),
    KEY ix_investor_access_stripe (stripe_session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE investor_update (
    id               CHAR(9)       PRIMARY KEY,
    venture_id       VARCHAR(9)    NOT NULL,
    subject          VARCHAR(300)  NULL,
    body             TEXT          NULL,
    status           VARCHAR(10)   NOT NULL DEFAULT 'draft' COMMENT 'draft | approved | sent',
    approved_by      VARCHAR(9)    NULL,
    sent_at          DATETIME      NULL,
    recipient_count  INT           NOT NULL DEFAULT 0,
    created_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_investor_update_venture (venture_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE investor_profile (
    id                    CHAR(9)       PRIMARY KEY,
    name                  VARCHAR(200)  NOT NULL,
    firm                  VARCHAR(200)  NULL,
    kind                  VARCHAR(12)   NOT NULL DEFAULT 'investor' COMMENT 'investor | accelerator | grant',
    stages                JSON          NULL,
    check_min             BIGINT        NULL COMMENT 'whole USD',
    check_max             BIGINT        NULL COMMENT 'whole USD',
    sectors               JSON          NULL,
    geographies           JSON          NULL,
    apply_url             VARCHAR(500)  NULL,
    accepts_applications  TINYINT(1)    NOT NULL DEFAULT 0,
    auto_submit_allowed   TINYINT(1)    NOT NULL DEFAULT 0,
    notes                 TEXT          NULL,
    source                VARCHAR(8)    NOT NULL DEFAULT 'admin' COMMENT 'admin | founder',
    added_by              VARCHAR(9)    NULL,
    created_at            DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_investor_profile_kind (kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE investor_target (
    id                   CHAR(9)       PRIMARY KEY,
    venture_id           VARCHAR(9)    NOT NULL,
    investor_profile_id  VARCHAR(9)    NULL,
    name                 VARCHAR(200)  NOT NULL,
    firm                 VARCHAR(200)  NULL,
    kind                 VARCHAR(12)   NOT NULL DEFAULT 'investor',
    fit_reasons          JSON          NULL,
    fit_score            FLOAT         NULL,
    status               VARCHAR(12)   NOT NULL DEFAULT 'candidate' COMMENT 'candidate | calibration | pursuing | passed',
    `position`           INT           NOT NULL DEFAULT 0,
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_investor_target_venture (venture_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE intro_path (
    id                    CHAR(9)      PRIMARY KEY,
    venture_id            VARCHAR(9)   NOT NULL,
    investor_target_id    VARCHAR(9)   NOT NULL,
    connector_contact_id  VARCHAR(9)   NOT NULL,
    strength              FLOAT        NOT NULL DEFAULT 0,
    evidence              JSON         NULL,
    status                VARCHAR(12)  NOT NULL DEFAULT 'candidate',
    created_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_intro_path_venture (venture_id),
    KEY ix_intro_path_target (investor_target_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE venture_outreach_action (
    id              CHAR(9)       PRIMARY KEY,
    venture_id      VARCHAR(9)    NOT NULL,
    plan_step_id    VARCHAR(9)    NULL,
    action_type     VARCHAR(20)   NOT NULL COMMENT 'research | intro_request | followup | first_contact | financial_message | customer_message | application',
    level_required  INT           NULL,
    level_applied   INT           NULL,
    status          VARCHAR(18)   NOT NULL DEFAULT 'drafted' COMMENT 'drafted | awaiting_approval | approved | sent | blocked',
    approved_by     VARCHAR(9)    NULL,
    block_reason    VARCHAR(300)  NULL,
    sent_at         DATETIME      NULL,
    target_ref      VARCHAR(200)  NULL,
    subject         VARCHAR(300)  NULL,
    body            TEXT          NULL,
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_venture_outreach_action_venture (venture_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE founder_alert_rule (
    id          CHAR(9)      PRIMARY KEY,
    metric      VARCHAR(60)  NOT NULL,
    threshold   FLOAT        NOT NULL DEFAULT 0,
    enabled     TINYINT(1)   NOT NULL DEFAULT 1,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_founder_alert_rule_metric (metric)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
