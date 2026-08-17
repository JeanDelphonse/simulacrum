-- SIM-PRD-CAL-001 — The Calibration Layer (schema)
-- Run once on deploy. Reference DATA is seeded separately by 047_calibration_seed_l1.sql
-- so the schema and the (reviewable, replaceable) numbers deploy independently.
--
-- The Calibration Layer sits between an agent's raw numeric estimate and what the
-- user sees: it replaces a bare model number with an empirically anchored range
-- carrying a confidence tier and a cited source (PRD §1).
--
-- Every threshold lives here in calibration_configs, never in code (PRD §7).

-- ── Reference datasets — the external ground truth (PRD §7, §8) ───────────────
CREATE TABLE reference_datasets (
    id                   CHAR(9)      PRIMARY KEY,
    layer                VARCHAR(2)   NOT NULL                        COMMENT 'L1..L5',
    name                 VARCHAR(200) NOT NULL,
    source               VARCHAR(200) NOT NULL                        COMMENT 'publisher, e.g. "U.S. Bureau of Labor Statistics"',
    source_url           VARCHAR(500) NULL,
    unit                 VARCHAR(20)  NOT NULL DEFAULT 'usd'          COMMENT 'usd | usd_hour | usd_year | pct | ratio',
    geography_scope      VARCHAR(40)  NOT NULL DEFAULT 'national'     COMMENT 'national | metro | global',
    credibility_tier     CHAR(1)      NOT NULL DEFAULT 'C'            COMMENT 'A/B/C — caps the confidence tier (PRD §8 rule)',
    -- Seeded or newly-uploaded rows start unverified. An unverified dataset can
    -- never render "High confidence" no matter how dense the data, so a starter
    -- extract is useful on day one without ever overstating its own authority.
    needs_review         TINYINT(1)   NOT NULL DEFAULT 1,
    derivation_note      TEXT         NULL                            COMMENT 'if this is a derived/proxy dataset, the transform used',
    as_of_label          VARCHAR(40)  NULL                            COMMENT 'human label shown in the card, e.g. "May 2024"',
    last_refreshed       DATE         NULL,
    refresh_cadence_days INT          NOT NULL DEFAULT 365,
    is_active            TINYINT(1)   NOT NULL DEFAULT 1,
    created_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_rds_layer_active ON reference_datasets (layer, is_active);

-- ── Reference data points — one distribution per cohort ──────────────────────
CREATE TABLE reference_data_points (
    id            CHAR(9)        PRIMARY KEY,
    dataset_id    CHAR(9)        NOT NULL,
    cohort_json   JSON           NULL                 COMMENT '{"soc_group":"13-1111","metro":"national","seniority":"any"}',
    -- md5 of the canonical cohort JSON. The engine walks a specificity ladder of
    -- candidate cohorts and looks each up by hash, so matching is one indexed
    -- equality probe per rung instead of a JSON scan.
    cohort_hash   CHAR(32)       NOT NULL,
    p10           DECIMAL(16,4)  NULL,
    p50           DECIMAL(16,4)  NOT NULL,
    p90           DECIMAL(16,4)  NULL,
    mean          DECIMAL(16,4)  NULL,
    sample_size   INT            NOT NULL DEFAULT 0,
    as_of_date    DATE           NULL,
    created_at    TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rdp_dataset FOREIGN KEY (dataset_id)
        REFERENCES reference_datasets (id) ON DELETE CASCADE,
    CONSTRAINT uq_rdp_dataset_cohort UNIQUE (dataset_id, cohort_hash)
);
CREATE INDEX idx_rdp_hash ON reference_data_points (cohort_hash);

-- ── Per-agent calibration config — all thresholds, admin-editable (PRD §7) ───
CREATE TABLE calibration_configs (
    id                      CHAR(9)       PRIMARY KEY,
    agent_key               VARCHAR(50)   NOT NULL     COMMENT 'canonical action_type',
    layer                   VARCHAR(2)    NOT NULL,
    dataset_id              CHAR(9)       NULL         COMMENT 'NULL = configured but unbound; field renders raw',
    output_field            VARCHAR(60)   NOT NULL     COMMENT 'key the agent emits in its METRICS block',
    field_label             VARCHAR(120)  NOT NULL     COMMENT 'headline label on the calibrated card',
    unit                    VARCHAR(20)   NOT NULL DEFAULT 'usd',
    method                  VARCHAR(30)   NOT NULL DEFAULT 'bayes_precision',
    percentiles_json        JSON          NULL         COMMENT 'default [10,50,90]',
    -- Model-uncertainty spread for the prior, as a % of the raw value. Wider =
    -- trust the agent less = reference data pulls the posterior further.
    sigma_model_pct         DECIMAL(6,2)  NOT NULL DEFAULT 25.00,
    min_sample_high         INT           NOT NULL DEFAULT 300  COMMENT 'n at/above this may render High',
    min_sample_moderate     INT           NOT NULL DEFAULT 40   COMMENT 'below this the thin-data guard fires (PRD §9)',
    band_floor_pct          DECIMAL(6,2)  NOT NULL DEFAULT 8.00 COMMENT 'minimum half-width as % of mid — no false precision',
    drift_threshold_pct     DECIMAL(6,2)  NOT NULL DEFAULT 15.00,
    min_reports_to_reweight INT           NOT NULL DEFAULT 30   COMMENT 'optimal-stopping gate (PRD §10)',
    is_enabled              TINYINT(1)    NOT NULL DEFAULT 1,
    created_at              TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_cc_agent_field UNIQUE (agent_key, output_field)
);
CREATE INDEX idx_cc_enabled ON calibration_configs (is_enabled, agent_key);

-- ── Calibration runs — one row per calibrated field per artifact ─────────────
CREATE TABLE calibration_runs (
    id               CHAR(9)        PRIMARY KEY,
    simulation_id    CHAR(9)        NOT NULL,
    -- The PRD data model lists only simulation_id, but the card renders per
    -- artifact and a simulation has many artifacts emitting the same field over
    -- successive re-runs, so the run must be anchored to the action + version.
    action_id        CHAR(9)        NULL,
    version_number   INT            NULL,
    agent_key        VARCHAR(50)    NOT NULL,
    output_field     VARCHAR(60)    NOT NULL,
    field_label      VARCHAR(120)   NOT NULL,
    unit             VARCHAR(20)    NOT NULL DEFAULT 'usd',
    raw_value        DECIMAL(16,4)  NOT NULL             COMMENT 'the prior — agent estimate',
    cal_low          DECIMAL(16,4)  NOT NULL,
    cal_mid          DECIMAL(16,4)  NOT NULL,
    cal_high         DECIMAL(16,4)  NOT NULL,
    confidence_tier  VARCHAR(12)    NOT NULL             COMMENT 'high | moderate | directional',
    dataset_id       CHAR(9)        NULL,
    method           VARCHAR(30)    NOT NULL DEFAULT 'bayes_precision',
    cohort_json      JSON           NULL                 COMMENT 'the cohort actually matched',
    cohort_hash      CHAR(32)       NULL,
    ref_p50          DECIMAL(16,4)  NULL                 COMMENT 'the likelihood, for the drawer',
    ref_sample_size  INT            NULL,
    ref_as_of        DATE           NULL,
    match_level      VARCHAR(20)    NULL                 COMMENT 'exact | relaxed | proxy | none',
    thin_data_guard  TINYINT(1)     NOT NULL DEFAULT 0   COMMENT '1 = sparse reference refused; raw returned widened',
    rationale        VARCHAR(400)   NULL                 COMMENT 'one line: how far calibration moved the estimate',
    created_at       TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_cr_action  ON calibration_runs (action_id);
CREATE INDEX idx_cr_sim     ON calibration_runs (simulation_id);
CREATE INDEX idx_cr_cohort  ON calibration_runs (agent_key, cohort_hash);

-- ── Outcome reports — the flywheel (PRD §10) ─────────────────────────────────
-- Private by default: only aggregated, de-identified distributions ever feed
-- back into reference data.
CREATE TABLE outcome_reports (
    id              CHAR(9)        PRIMARY KEY,
    user_id         CHAR(9)        NOT NULL,
    simulation_id   CHAR(9)        NULL,
    run_id          CHAR(9)        NULL          COMMENT 'the calibration_runs row this reports against',
    agent_key       VARCHAR(50)    NOT NULL,
    output_field    VARCHAR(60)    NOT NULL,
    reported_value  DECIMAL(16,4)  NOT NULL,
    months_elapsed  INT            NULL          COMMENT 'artifact age at report time',
    cohort_json     JSON           NULL,
    cohort_hash     CHAR(32)       NULL,
    is_verified     TINYINT(1)     NOT NULL DEFAULT 0 COMMENT 'only verified reports reach the reweight gate',
    is_excluded     TINYINT(1)     NOT NULL DEFAULT 0 COMMENT 'admin-flagged outlier',
    note            VARCHAR(500)   NULL,
    reported_at     TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- One report per user per calibration run; re-submitting updates in place.
    CONSTRAINT uq_or_user_run UNIQUE (user_id, run_id)
);
CREATE INDEX idx_or_cohort ON outcome_reports (agent_key, output_field, cohort_hash);
CREATE INDEX idx_or_user   ON outcome_reports (user_id);

-- ── Calibration audit — drift review trail (PRD §7, §10) ─────────────────────
CREATE TABLE calibration_audit (
    id              CHAR(9)        PRIMARY KEY,
    dataset_id      CHAR(9)        NULL,
    agent_key       VARCHAR(50)    NULL,
    output_field    VARCHAR(60)    NULL,
    cohort_hash     CHAR(32)       NULL,
    cohort_json     JSON           NULL,
    drift_pct       DECIMAL(8,2)   NULL          COMMENT 'reported median vs reference p50',
    n_reports       INT            NOT NULL DEFAULT 0,
    ref_p50         DECIMAL(16,4)  NULL,
    reported_p50    DECIMAL(16,4)  NULL,
    action          VARCHAR(20)    NOT NULL DEFAULT 'none' COMMENT 'none | flagged | reweighted | gated',
    notes           VARCHAR(500)   NULL,
    created_at      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_ca_dataset ON calibration_audit (dataset_id, created_at);

-- ── Cohort keys cached on the simulation (PRD §5) ────────────────────────────
-- Resolved once per simulation by calibration_cohort.py and reused for every
-- field, so one classification call serves the whole run.
ALTER TABLE simulations
    ADD COLUMN cohort_json      JSON       NULL COMMENT 'resolved calibration cohort {soc_group, metro, seniority}',
    ADD COLUMN cohort_resolved_at DATETIME NULL;

-- ── Admin toggles (PRD §12.8, §14 Phase 4) ──────────────────────────────────
-- calibration_enabled       — master switch for the whole layer
-- calibration_gated         — reserved: gate behind the paid simulation (default off;
--                             when enabled the gate reads Admin → Pricing, never a
--                             hardcoded price)
-- calibration_reweight_live — Phase 4. OFF means the nightly job computes drift and
--                             writes calibration_audit but never mutates reference
--                             data. Turn on only once cohorts clear the gate.
-- IGNORE so re-running this block alone (e.g. to add a switch on a database that
-- already has the tables) is harmless rather than a duplicate-key failure.
INSERT IGNORE INTO platform_settings (id, `key`, value) VALUES
    ('cALenbL01', 'calibration_enabled',       'true'),
    ('cALgAte01', 'calibration_gated',         'false'),
    ('cALrewT01', 'calibration_reweight_live', 'false'),
    ('cALoutR01', 'calibration_outcomes_open', 'true');
