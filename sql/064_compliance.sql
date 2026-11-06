-- Compliance risk reduction (COMPLIANCE_PLAN.md): earnings claims, Layer 5
-- education-only rules, outreach rules. Run once on deploy. Additive.
--
-- legal_texts        the one source for disclaimer / disclosure / claims wording.
--                    Seeded by the app on first use, every row status 'draft';
--                    only an admin marks a text 'counsel_approved'.
-- outcome_checkins   day 30/90/180 self-reported outcomes. Internal only: no
--                    public page reads this until counsel approves the presentation.
-- compliance_events  Layer 5 outputs that broke the education-only rules.
-- users.postal_address_enc  the sender's postal address (encrypted), required
--                    before any commercial email sends.
--
-- New PKs are CHAR(9) uppercase from utils.id_gen.generate_pk(). Limits, caps
-- and switches are platform_settings / feature_flags rows, created on first use.

CREATE TABLE legal_texts (
    id          CHAR(9)      PRIMARY KEY,
    `key`       VARCHAR(60)  NOT NULL UNIQUE,
    body        TEXT         NOT NULL,
    status      VARCHAR(20)  NOT NULL DEFAULT 'draft' COMMENT 'draft | counsel_approved',
    version     INT          NOT NULL DEFAULT 1,
    updated_by  CHAR(9)      NULL,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE outcome_checkins (
    id                   CHAR(9)       PRIMARY KEY,
    user_id              CHAR(9)       NOT NULL,
    simulation_id        CHAR(9)       NOT NULL,
    checkin_day          INT           NOT NULL COMMENT '30 | 90 | 180',
    income_gained_range  VARCHAR(20)   NULL COMMENT '0 | under_1k | 1k_5k | 5k_20k | 20k_plus | prefer_not',
    first_client         TINYINT(1)    NULL,
    job_landed           TINYINT(1)    NULL,
    notes                VARCHAR(1000) NULL,
    dismissed            TINYINT(1)    NOT NULL DEFAULT 0,
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_outcome_checkin_day UNIQUE (simulation_id, checkin_day)
);
CREATE INDEX ix_outcome_checkins_user_id ON outcome_checkins (user_id);

CREATE TABLE compliance_events (
    id          CHAR(9)      PRIMARY KEY,
    user_id     CHAR(9)      NULL,
    agent       VARCHAR(60)  NOT NULL,
    rule        VARCHAR(60)  NOT NULL,
    excerpt     VARCHAR(300) NULL,
    outcome     VARCHAR(20)  NULL COMMENT 'regenerated | fallback',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_compliance_events_user_id ON compliance_events (user_id);
CREATE INDEX ix_compliance_events_created_at ON compliance_events (created_at);

ALTER TABLE users ADD COLUMN postal_address_enc TEXT NULL;

-- Layer display names without earnings promises (COMPLIANCE_PLAN A1, D5; drafts
-- for counsel). Simulations store the layer name, so existing rows are renamed
-- to match what new simulations get. Internal codes (L4, L5) are unchanged.
UPDATE simulation_layers SET layer_name = 'Recurring Income — After the Initial Build'
 WHERE layer_number = 4 AND layer_name LIKE 'Automated Residual%';
UPDATE simulation_layers SET layer_name = 'Wealth Planning — Education and Planning'
 WHERE layer_number = 5 AND layer_name LIKE 'Wealth Deployment%Money Working%';

-- The public share-ROI card is off until the owner shares it (COMPLIANCE_PLAN D6).
ALTER TABLE simulations ADD COLUMN roi_share_enabled TINYINT(1) NOT NULL DEFAULT 0;
