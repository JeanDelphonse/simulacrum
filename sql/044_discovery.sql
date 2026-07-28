-- SIM-PRD-CRM-002 — Prospect Discovery Agent. Run once on deploy.
--
-- Feeds the CRM-001 pipeline: Apollo finds firms matching firmographic filters,
-- Claude scores them for fit, high-fit unflagged firms auto-save into
-- admin_prospects at stage 'researched', everything else waits in a review queue.
--
-- House convention as in 043: CHAR(9) ids, plain indexed columns, no
-- database-level FOREIGN KEY (relationships live on the SQLAlchemy models).
--
-- Safe to re-run: CREATE TABLE IF NOT EXISTS plus INSERT IGNORE.

CREATE TABLE IF NOT EXISTS discovery_profiles (
    id                  CHAR(9) PRIMARY KEY,
    name                VARCHAR(120) NOT NULL,
    categories          JSON NULL              COMMENT 'Apollo industry tags + category labels',
    headcount_min       INT NOT NULL DEFAULT 5  COMMENT 'below 5 is usually a solo shop with no team to onboard',
    headcount_max       INT NOT NULL DEFAULT 25 COMMENT 'above 25 drifts out of the boutique band the pitch targets',
    geography           VARCHAR(160) NULL       COMMENT 'NULL = US-wide',
    keywords_pos        JSON NULL               COMMENT 'steer Apollo toward the motion',
    keywords_neg        JSON NULL               COMMENT 'pre-filter noise: staffing, recruiting, franchise',
    auto_save_threshold VARCHAR(10) NOT NULL DEFAULT 'high' COMMENT 'high|none - none reviews everything',
    batch_cap           INT NOT NULL DEFAULT 50 COMMENT 'max companies per run, bounds Apollo credits',
    schedule            VARCHAR(20) NULL        COMMENT 'NULL|weekly|monthly - scheduled runs via the briefing job',
    last_run_at         DATETIME NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_dp_schedule (schedule)
);

CREATE TABLE IF NOT EXISTS discovery_candidates (
    id            CHAR(9) PRIMARY KEY,
    profile_id    CHAR(9) NULL           COMMENT 'discovery_profiles.id',
    company       VARCHAR(200) NOT NULL,
    domain        VARCHAR(200) NOT NULL,
    headcount     INT NULL,
    industry      VARCHAR(120) NULL,
    location      VARCHAR(160) NULL,
    leader_name   VARCHAR(160) NULL      COMMENT 'from Apollo or the enrichment pass',
    leader_linkedin VARCHAR(300) NULL,
    -- 'signal' is a MySQL reserved word (SIGNAL/RESIGNAL), and 'fit' is renamed
    -- alongside it for consistency. The SQLAlchemy model maps its .signal and
    -- .fit attributes onto these column names, so application code is unchanged.
    recent_signal TEXT NULL              COMMENT 'recent funding/growth/press, sharpens the first touch',
    fit_score     VARCHAR(10) NULL       COMMENT 'high|medium|low',
    rationale     TEXT NULL              COMMENT 'one-line reason, retained and shown with the prospect',
    flags         JSON NULL              COMMENT 'possibly_acquired|too_large|off_category|too_small',
    route         VARCHAR(20) NULL       COMMENT 'auto_save|review_queue',
    status        VARCHAR(20) NOT NULL DEFAULT 'queued' COMMENT 'queued|saved|dismissed',
    prospect_id   CHAR(9) NULL           COMMENT 'admin_prospects.id, set when saved (CRM-001 link)',
    discovered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Global dedup memory: a domain is only ever discovered once, whether it was
    -- saved, queued or dismissed, so re-running a profile never re-surfaces it.
    UNIQUE KEY uq_domain (domain),
    INDEX idx_dc_status (status),
    INDEX idx_dc_profile (profile_id),
    INDEX idx_dc_route (route),
    INDEX idx_dc_prospect (prospect_id)
);

-- A starter profile matching the motion described in the PRD: boutique
-- consultancies where the consultants are the product. INSERT IGNORE so a
-- re-run does not clobber tuning.
INSERT IGNORE INTO discovery_profiles
    (id, name, categories, headcount_min, headcount_max, keywords_pos, keywords_neg)
VALUES (
    'dscprof01',
    'Boutique consultancies (default)',
    '["management consulting","hr consulting","fractional executive","advisory","boutique strategy"]',
    5, 25,
    '["fractional","boutique","advisory","consultancy"]',
    '["staffing","recruiting","franchise"]'
);
