-- SIM-PRD-OUTREACH-001 — Admin Outreach Email Automation
-- New-user 3-email drip + existing-user broadcasts, editable templates, segmentation.
-- Run once on deploy.
--
-- Three tables:
--   outreach_templates   — editable default copy for drip_1/2/3 and named broadcast templates
--   outreach_enrollments — per-user drip enrollment state machine
--   outreach_sends       — the queue AND the log: one row per queued/scheduled/sent email
--
-- Config lives in platform_settings (keys: outreach_enabled, outreach_initial_delay_hours,
-- outreach_cadence_days, outreach_require_approval). Segments are computed, not stored.

CREATE TABLE outreach_templates (
    id           CHAR(9)      NOT NULL,
    template_key VARCHAR(50)  NOT NULL,       -- drip_1 / drip_2 / drip_3 / custom keys
    name         VARCHAR(120) NULL,           -- human label for named broadcast templates
    subject      VARCHAR(300) NOT NULL,
    preview_text VARCHAR(200) NULL,
    body         TEXT         NOT NULL,       -- editable copy, may contain {{tokens}}
    is_drip      TINYINT(1)   NOT NULL DEFAULT 0,
    updated_by   CHAR(9)      NULL,
    created_at   DATETIME     NULL,
    updated_at   DATETIME     NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ot_key (template_key)
);

CREATE TABLE outreach_enrollments (
    id           CHAR(9)     NOT NULL,
    user_id      CHAR(9)     NOT NULL,
    sequence     VARCHAR(50) NOT NULL DEFAULT 'new_user_drip',
    current_step INT         NOT NULL DEFAULT 0,       -- 0..3 (last step sent)
    next_send_at DATETIME    NULL,
    status       VARCHAR(20) NOT NULL DEFAULT 'active',-- active/graduated/completed/paused/removed
    created_at   DATETIME    NULL,
    updated_at   DATETIME    NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_oe_user_seq (user_id, sequence),
    KEY idx_oe_status_due (status, next_send_at)
);

CREATE TABLE outreach_sends (
    id             CHAR(9)      NOT NULL,
    user_id        CHAR(9)      NOT NULL,
    enrollment_id  CHAR(9)      NULL,             -- set for drip sends, null for broadcasts
    kind           VARCHAR(20)  NOT NULL DEFAULT 'drip',  -- drip / broadcast
    template_key   VARCHAR(50)  NOT NULL,
    step_number    INT          NULL,             -- 1/2/3 for drip
    subject        VARCHAR(300) NOT NULL,
    preview_text   VARCHAR(200) NULL,
    body_snapshot  TEXT         NOT NULL,         -- exact rendered copy (after any edits)
    was_edited     TINYINT(1)   NOT NULL DEFAULT 0,
    approved_by    CHAR(9)      NULL,
    approved_at    DATETIME     NULL,
    to_email       VARCHAR(255) NOT NULL,
    provider_message_id VARCHAR(100) NULL,
    status         VARCHAR(20)  NOT NULL DEFAULT 'queued',
                   -- queued / awaiting_approval / paused / scheduled / sent /
                   -- skipped / suppressed / failed
    scheduled_at   DATETIME     NULL,
    sent_at        DATETIME     NULL,
    opened_at      DATETIME     NULL,
    open_count     INT          NOT NULL DEFAULT 0,
    clicked_at     DATETIME     NULL,
    click_count    INT          NOT NULL DEFAULT 0,
    created_at     DATETIME     NULL,
    updated_at     DATETIME     NULL,
    PRIMARY KEY (id),
    KEY idx_os_user (user_id, sent_at),
    KEY idx_os_status_due (status, scheduled_at),
    KEY idx_os_provider (provider_message_id),
    KEY idx_os_enrollment (enrollment_id)
);
