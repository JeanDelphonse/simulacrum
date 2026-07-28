-- SIM-PRD-CRM-001 — Admin Outreach Pipeline (the founder's sales cockpit).
-- Run once on deploy.
--
-- Three tables: the prospect firms, an append-only touch log, and the
-- stage -> follow-up interval rules. Admin-only data; never exposed to users,
-- SMEs or org admins.
--
-- Following the convention of every other migration here: CHAR(9) ids and plain
-- indexed columns, with NO database-level FOREIGN KEY constraints — relationships
-- are declared on the SQLAlchemy models and enforced by the app. Declaring a real
-- FK to corporate_accounts(id) fails with errno 150 anyway, because that column is
-- CHAR(9) and a VARCHAR(9) child column is not a matching type.
--
-- Safe to re-run: CREATE TABLE IF NOT EXISTS plus INSERT IGNORE, so tuned
-- follow-up intervals are preserved.

CREATE TABLE IF NOT EXISTS admin_prospects (
    id                 CHAR(9) PRIMARY KEY,
    firm_name          VARCHAR(200) NOT NULL,
    lead_name          VARCHAR(160) NULL,
    lead_linkedin      VARCHAR(300) NULL,
    website            VARCHAR(300) NULL,
    contact_path       VARCHAR(200) NULL                     COMMENT 'linkedin|form|email',
    fit                VARCHAR(10)  NOT NULL DEFAULT 'medium' COMMENT 'high|medium|low',
    category           VARCHAR(80)  NULL,
    stage              VARCHAR(30)  NOT NULL DEFAULT 'not_started'
                       COMMENT 'not_started|researched|touch_1_sent|connected|touch_2_sent|replied|meeting_booked|onboarded|passed',
    last_contact       DATE NULL,
    next_followup      DATE NULL                             COMMENT 'drives the due queue and the overdue state',
    notes              TEXT NULL,
    won_org_id         CHAR(9) NULL                          COMMENT 'corporate_accounts.id, set on onboard (ORG-001 link)',
    passed_reason      VARCHAR(200) NULL,
    retouch_on         DATE NULL                             COMMENT 'optional re-touch date months out after a pass',
    draft_text         TEXT NULL                             COMMENT 'cached briefing draft, so opening the tab does not re-bill Claude',
    draft_for_stage    VARCHAR(30) NULL                      COMMENT 'stage the cached draft was written for; mismatch = stale',
    draft_generated_at DATETIME NULL,
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ap_stage (stage),
    INDEX idx_ap_followup (next_followup),
    INDEX idx_ap_firm (firm_name),
    INDEX idx_ap_category (category),
    INDEX idx_ap_won_org (won_org_id)
);

CREATE TABLE IF NOT EXISTS admin_prospect_touches (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    prospect_id CHAR(9)     NOT NULL                        COMMENT 'admin_prospects.id',
    touched_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    channel     VARCHAR(30) NOT NULL DEFAULT 'linkedin'     COMMENT 'linkedin|email|call|note',
    stage_at    VARCHAR(30) NOT NULL                        COMMENT 'stage at the time of the touch',
    summary     TEXT        NULL                            COMMENT 'what was sent',
    drafted_by  VARCHAR(20) NOT NULL DEFAULT 'manual'       COMMENT 'manual|founder_ops',
    INDEX idx_apt_prospect (prospect_id, touched_at),
    INDEX idx_apt_at (touched_at)
);

CREATE TABLE IF NOT EXISTS admin_stage_rules (
    stage         VARCHAR(30) PRIMARY KEY,
    followup_days INT NOT NULL DEFAULT 3   COMMENT 'wait after logging a touch here before it resurfaces',
    drafts_touch  VARCHAR(30) NULL         COMMENT 'research|touch1|touch2|touch3, NULL = waiting stage, remind only'
);

-- Stage rules. A NULL drafts_touch is a waiting stage: the briefing reminds but
-- writes nothing, which is what keeps Touch 1 from ever carrying a pitch
-- (PRD section 2). admin_crm_service falls back to the same values in code, so a
-- partially seeded table cannot break the pipeline.
INSERT IGNORE INTO admin_stage_rules (stage, followup_days, drafts_touch) VALUES
    ('not_started',    0, 'research'),
    ('researched',     0, 'touch1'),
    ('touch_1_sent',   4, NULL),
    ('connected',      0, 'touch2'),
    ('touch_2_sent',   3, NULL),
    ('replied',        3, 'touch3'),
    ('meeting_booked', 1, NULL),
    ('onboarded',      0, NULL),
    ('passed',         0, NULL);
