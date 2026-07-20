-- SIM-PRD-ORG-001 — Organizations & Bulk Provisioning
-- Run once on deploy.
--
-- MERGE NOTE: this PRD is implemented by EXTENDING the pre-existing corporate
-- feature (corporate_accounts / corporate_employees) rather than introducing a
-- parallel organizations/org_members hierarchy. The credit pool supersedes the
-- old named-seat model; the seat_* columns are retained for backward
-- compatibility but new orgs are provisioned as credit pools.
--
--   organizations  -> corporate_accounts  (credit-pool columns added below)
--   org_members    -> corporate_employees (role/join_source/reminder columns)
--   credit_redemptions / org_invoices / org_sme_pod -> new tables (below)
--   user_profiles.org_id / hide_org_cobrand -> added below

-- ── Credit pool + contract + co-branding on the org ───────────────────────────
ALTER TABLE corporate_accounts
    ADD COLUMN org_type           VARCHAR(20)   NOT NULL DEFAULT 'pilot'  COMMENT 'pilot|cohort|enterprise|partner',
    ADD COLUMN credits_purchased  INT           NOT NULL DEFAULT 0,
    ADD COLUMN credits_remaining  INT           NOT NULL DEFAULT 0,
    ADD COLUMN credit_value_cents INT           NOT NULL DEFAULT 0        COMMENT 'simulation price locked at contract time',
    ADD COLUMN discount_pct       DECIMAL(5,2)  NOT NULL DEFAULT 0        COMMENT 'contract discount %, surfaced in admin for pricing discipline',
    ADD COLUMN auto_join_domains  JSON          NULL                      COMMENT 'e.g. ["acme.com"] — signups on these domains auto-join the org',
    ADD COLUMN provisioning_trigger VARCHAR(10) NOT NULL DEFAULT 'issue'  COMMENT 'issue|payment — when credits are provisioned',
    ADD COLUMN contract_start     DATE          NULL,
    ADD COLUMN contract_end       DATE          NULL,
    ADD COLUMN invite_token       VARCHAR(64)   NULL                      COMMENT 'shareable bulk-invite link token',
    ADD COLUMN invite_cap         INT           NULL                      COMMENT 'max joins via invite link, NULL = unlimited',
    ADD COLUMN invite_uses        INT           NOT NULL DEFAULT 0,
    ADD COLUMN invite_expires_at  DATETIME      NULL;

CREATE INDEX idx_corp_invite_token ON corporate_accounts (invite_token);

-- ── Membership: role, join source, activation reminders ───────────────────────
ALTER TABLE corporate_employees
    ADD COLUMN role            VARCHAR(20) NOT NULL DEFAULT 'member' COMMENT 'member|org_admin',
    ADD COLUMN join_source     VARCHAR(20) NULL                      COMMENT 'csv|domain|link|manual',
    ADD COLUMN reminder_count  INT         NOT NULL DEFAULT 0        COMMENT 'activation nudges sent (Day 3 / Day 10)',
    ADD COLUMN last_reminded_at DATETIME   NULL;

-- ── Credit redemption ledger (high volume) ────────────────────────────────────
CREATE TABLE credit_redemptions (
    id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    org_id             CHAR(9)   NOT NULL,
    user_id            CHAR(9)   NOT NULL,
    simulation_id      CHAR(9)   NULL,
    credit_value_cents INT       NOT NULL,
    redeemed_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_cr_org (org_id, redeemed_at),
    INDEX idx_cr_user (user_id)
);

-- ── Org invoices (Stripe Invoicing / PO / net terms) ──────────────────────────
CREATE TABLE org_invoices (
    id               CHAR(9) PRIMARY KEY,
    org_id           CHAR(9) NOT NULL,
    stripe_ref       VARCHAR(120) NULL,
    po_number        VARCHAR(80)  NULL,
    credits          INT NOT NULL,
    unit_price_cents INT NOT NULL,
    amount_cents     INT NOT NULL,
    net_terms        INT NOT NULL DEFAULT 30,
    status           VARCHAR(20) NOT NULL DEFAULT 'issued',  -- issued|paid|void
    issued_at        DATETIME NULL,
    paid_at          DATETIME NULL,
    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_oi_org (org_id)
);

-- ── Cohort SME pod (extends SIM-PRD-SME-001 matching) ─────────────────────────
CREATE TABLE org_sme_pod (
    org_id CHAR(9) NOT NULL,
    sme_id CHAR(9) NOT NULL,
    PRIMARY KEY (org_id, sme_id)
);

-- ── Member linkage + co-brand opt-out on the profile ──────────────────────────
ALTER TABLE user_profiles
    ADD COLUMN org_id           CHAR(9)  NULL,
    ADD COLUMN hide_org_cobrand BOOLEAN  NOT NULL DEFAULT FALSE;

CREATE INDEX idx_user_profiles_org ON user_profiles (org_id);
