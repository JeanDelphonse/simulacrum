-- Founder Track: customer communication (CUSTOMER_EMAIL_PLAN.md section 3,
-- SIM-PRD-FND-001 decision of Nov 21, 2026). Run once on deploy, after 079.
--
-- SimiSI sends customer email in the founder's name (Reply-To the founder),
-- only to contacts the founders approved for that agent, after approval.
-- Nothing changes until Admin -> Founder Track switches fnd_customer_email on.
--
-- venture_contact         the venture's contact book (shared by co-founders)
-- venture_contact_access  which approved contacts each customer agent may email
-- venture_email_message   one row per recipient per send: content, status, EmailLog link
-- venture_suppression     the venture's do-not-contact list (opt-outs, bounces, complaints)
--
-- PKs are CHAR(9) uppercase from utils.id_gen.generate_pk().

ALTER TABLE founder_venture
    ADD COLUMN customer_paused_at DATETIME NULL
        COMMENT 'customer email paused (complaint threshold or admin), sql/080',
    ADD COLUMN customer_paused_reason VARCHAR(300) NULL;

ALTER TABLE venture_outreach_action
    ADD COLUMN send_meta JSON NULL
        COMMENT 'customer send settings: locked prices, scheduling link (sql/080)';

CREATE TABLE venture_contact (
    id                 CHAR(9)       PRIMARY KEY,
    venture_id         VARCHAR(9)    NOT NULL,
    email              VARCHAR(255)  NOT NULL,
    name               VARCHAR(200)  NULL,
    company            VARCHAR(200)  NULL,
    role               VARCHAR(120)  NULL,
    segment            VARCHAR(120)  NULL,
    note               VARCHAR(500)  NULL,
    source             VARCHAR(10)   NOT NULL DEFAULT 'manual' COMMENT 'manual | csv | gmail | imap | agent',
    state              VARCHAR(10)   NOT NULL DEFAULT 'approved'
                       COMMENT 'proposed | approved | contacted | replied | opted_out | bounced',
    proposed_by_agent  VARCHAR(60)   NULL,
    poc_customer       TINYINT(1)    NOT NULL DEFAULT 0,
    added_by           VARCHAR(9)    NULL,
    approved_by        VARCHAR(9)    NULL,
    approved_at        DATETIME      NULL,
    contacted_at       DATETIME      NULL,
    replied_at         DATETIME      NULL,
    created_at         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_venture_contact_email (venture_id, email),
    KEY ix_venture_contact_state (venture_id, state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE venture_contact_access (
    id                  CHAR(9)      PRIMARY KEY,
    venture_id          VARCHAR(9)   NOT NULL,
    venture_contact_id  VARCHAR(9)   NOT NULL,
    agent_key           VARCHAR(60)  NOT NULL,
    granted_by          VARCHAR(9)   NULL,
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_venture_contact_access (venture_contact_id, agent_key),
    KEY ix_venture_contact_access_venture (venture_id, agent_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE venture_email_message (
    id                  CHAR(9)       PRIMARY KEY,
    venture_id          VARCHAR(9)    NOT NULL,
    outreach_action_id  VARCHAR(9)    NOT NULL,
    venture_contact_id  VARCHAR(9)    NOT NULL,
    plan_step_id        VARCHAR(9)    NULL,
    agent_key           VARCHAR(60)   NULL,
    to_email            VARCHAR(255)  NOT NULL,
    subject             VARCHAR(300)  NULL,
    body                TEXT          NULL,
    kind                VARCHAR(10)   NOT NULL DEFAULT 'first' COMMENT 'first | followup',
    approval_mode       VARCHAR(10)   NOT NULL DEFAULT 'each' COMMENT 'each | campaign',
    approved_level      INT           NULL,
    approved_by         VARCHAR(9)    NULL,
    sender_user_id      VARCHAR(9)    NULL,
    status              VARCHAR(10)   NOT NULL DEFAULT 'scheduled'
                        COMMENT 'scheduled | sending | sent | skipped | failed | bounced | complained | cancelled',
    reason              VARCHAR(300)  NULL,
    scheduled_for       DATETIME      NULL,
    sent_at             DATETIME      NULL,
    email_log_id        VARCHAR(9)    NULL,
    replied_at          DATETIME      NULL,
    created_at          DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_venture_email_message_venture (venture_id, status),
    KEY ix_venture_email_message_action (outreach_action_id),
    KEY ix_venture_email_message_log (email_log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE venture_suppression (
    id          CHAR(9)       PRIMARY KEY,
    venture_id  VARCHAR(9)    NOT NULL,
    email       VARCHAR(255)  NOT NULL,
    reason      VARCHAR(10)   NOT NULL DEFAULT 'manual' COMMENT 'opt_out | bounce | complaint | manual',
    detail      VARCHAR(300)  NULL,
    added_by    VARCHAR(9)    NULL,
    created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_venture_suppression_email (venture_id, email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
