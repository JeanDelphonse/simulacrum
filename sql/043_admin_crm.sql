-- SIM-PRD-CRM-001 — Admin Outreach Pipeline (the founder's sales cockpit).
--
-- Three tables: the prospect firms, an append-only touch log, and the
-- stage -> follow-up interval rules. Admin-only data; never exposed to users,
-- SMEs or org admins.
--
-- Safe to run once. If a table already exists the CREATE is skipped by
-- IF NOT EXISTS; the stage-rule seed uses INSERT OR IGNORE so re-running will
-- not clobber intervals the founder has tuned.

CREATE TABLE IF NOT EXISTS admin_prospects (
    id                 VARCHAR(9) PRIMARY KEY,
    firm_name          VARCHAR(200) NOT NULL,
    lead_name          VARCHAR(160),
    lead_linkedin      VARCHAR(300),
    website            VARCHAR(300),
    contact_path       VARCHAR(200),                    -- linkedin | form | email
    fit                VARCHAR(10)  NOT NULL DEFAULT 'medium',   -- high | medium | low
    category           VARCHAR(80),
    stage              VARCHAR(30)  NOT NULL DEFAULT 'not_started',
    last_contact       DATE,
    next_followup      DATE,
    notes              TEXT,
    won_org_id         VARCHAR(9) REFERENCES corporate_accounts(id) ON DELETE SET NULL,
    passed_reason      VARCHAR(200),
    retouch_on         DATE,
    draft_text         TEXT,
    draft_for_stage    VARCHAR(30),
    draft_generated_at TIMESTAMP,
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_admin_prospects_stage      ON admin_prospects (stage);
CREATE INDEX IF NOT EXISTS ix_admin_prospects_followup   ON admin_prospects (next_followup);
CREATE INDEX IF NOT EXISTS ix_admin_prospects_firm_name  ON admin_prospects (firm_name);
CREATE INDEX IF NOT EXISTS ix_admin_prospects_category   ON admin_prospects (category);
CREATE INDEX IF NOT EXISTS ix_admin_prospects_won_org_id ON admin_prospects (won_org_id);

CREATE TABLE IF NOT EXISTS admin_prospect_touches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prospect_id VARCHAR(9) NOT NULL REFERENCES admin_prospects(id) ON DELETE CASCADE,
    touched_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    channel     VARCHAR(30) NOT NULL DEFAULT 'linkedin',   -- linkedin | email | call | note
    stage_at    VARCHAR(30) NOT NULL,
    summary     TEXT,
    drafted_by  VARCHAR(20) NOT NULL DEFAULT 'manual'      -- manual | founder_ops
);

CREATE INDEX IF NOT EXISTS ix_admin_touches_prospect ON admin_prospect_touches (prospect_id);
CREATE INDEX IF NOT EXISTS ix_admin_touches_at       ON admin_prospect_touches (touched_at);

CREATE TABLE IF NOT EXISTS admin_stage_rules (
    stage         VARCHAR(30) PRIMARY KEY,
    followup_days INTEGER NOT NULL DEFAULT 3,
    drafts_touch  VARCHAR(30)                              -- research | touch1 | touch2 | touch3
);

-- Stage rules. followup_days is how long to wait after logging a touch at this
-- stage before it resurfaces in the due queue; drafts_touch is what the morning
-- briefing writes for a prospect sitting in this stage (NULL = waiting stage,
-- reminder only, no draft — Touch 1 is never pitched, per PRD section 2).
INSERT OR IGNORE INTO admin_stage_rules (stage, followup_days, drafts_touch) VALUES
    ('not_started',    0, 'research'),
    ('researched',     0, 'touch1'),
    ('touch_1_sent',   4, NULL),
    ('connected',      0, 'touch2'),
    ('touch_2_sent',   3, NULL),
    ('replied',        3, 'touch3'),
    ('meeting_booked', 1, NULL),
    ('onboarded',      0, NULL),
    ('passed',         0, NULL);
