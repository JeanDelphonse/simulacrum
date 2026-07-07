-- SIM-PRD-SME-001 — Simi SME Assignment
-- Subject-Matter Experts, expertise-zone taxonomy, auto zone assignment, user-to-SME matching.
-- Run once on deploy.
--
-- Three parts:
--   simi_smes             — the human experts (name, contact, covered zones, capacity, status)
--   user_profiles (ALTER) — canonical_zones + sme assignment columns on the existing profile row
--   expertise_categories  — extensible canonical taxonomy shared with the Explore directory

CREATE TABLE simi_smes (
    id             CHAR(9)      NOT NULL,
    first_name     VARCHAR(80)  NOT NULL,
    last_name      VARCHAR(80)  NOT NULL,
    email          VARCHAR(160) NOT NULL,
    bio_url        VARCHAR(500) NULL,
    phone          VARCHAR(40)  NULL,
    zones          TEXT         NOT NULL,             -- JSON array e.g. ["technology","finance"]
    capacity       INT          NOT NULL DEFAULT 50,
    assigned_count INT          NOT NULL DEFAULT 0,
    status         VARCHAR(20)  NOT NULL DEFAULT 'active',   -- active | inactive
    needs_review   TINYINT(1)   NOT NULL DEFAULT 0,   -- set when zones edited while users assigned
    created_at     DATETIME     NULL,
    updated_at     DATETIME     NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sme_email (email),
    KEY idx_sme_status (status)
);

-- Canonical zones + SME assignment on the user profile
ALTER TABLE user_profiles
    ADD COLUMN canonical_zones     TEXT        NULL,   -- JSON [{category, confidence, is_primary}]
    ADD COLUMN sme_id              CHAR(9)     NULL,
    ADD COLUMN sme_assignment_type VARCHAR(10) NULL,   -- 'auto' | 'manual'
    ADD COLUMN needs_reassignment  TINYINT(1)  NOT NULL DEFAULT 0,
    ADD COLUMN zones_computed_at   DATETIME    NULL;

-- Extensible category taxonomy (slugs match /explore?category=)
CREATE TABLE expertise_categories (
    id          CHAR(9)     NOT NULL,
    name        VARCHAR(80) NOT NULL,
    slug        VARCHAR(80) NOT NULL,
    is_active   TINYINT(1)  NOT NULL DEFAULT 1,
    sort_order  INT         NOT NULL DEFAULT 0,
    created_at  DATETIME    NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_cat_slug (slug)
);

-- Seed the eight canonical categories shared with Explore (SIM-PRD-EXPLORE-001).
-- IDs are deterministic 9-char slugs padded so re-running is a no-op via the unique slug key.
INSERT INTO expertise_categories (id, name, slug, is_active, sort_order, created_at) VALUES
    ('cat_tech0', 'Technology', 'technology', 1, 10, CURRENT_TIMESTAMP),
    ('cat_fin00', 'Finance',    'finance',    1, 20, CURRENT_TIMESTAMP),
    ('cat_mkt00', 'Marketing',  'marketing',  1, 30, CURRENT_TIMESTAMP),
    ('cat_dsgn0', 'Design',     'design',     1, 40, CURRENT_TIMESTAMP),
    ('cat_cons0', 'Consulting', 'consulting', 1, 50, CURRENT_TIMESTAMP),
    ('cat_heal0', 'Healthcare', 'healthcare', 1, 60, CURRENT_TIMESTAMP),
    ('cat_legl0', 'Legal',      'legal',      1, 70, CURRENT_TIMESTAMP),
    ('cat_educ0', 'Education',  'education',  1, 80, CURRENT_TIMESTAMP);
