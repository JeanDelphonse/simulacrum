-- ============================================================
-- SIM-PRD-PUB-001 · Content Publishing Pipeline — DB Migration
-- Run once against production database.
-- ============================================================

-- Public URL on artifact versions (FR-PUB-01, FR-PUB-04)
ALTER TABLE artifact_versions
    ADD COLUMN IF NOT EXISTS public_url VARCHAR(500) NULL;

-- Published sales pages hosted at /p/<slug> (FR-PUB-03)
CREATE TABLE IF NOT EXISTS published_pages (
    id                  VARCHAR(9)      NOT NULL,
    slug                VARCHAR(200)    NOT NULL,
    user_id             VARCHAR(9)      NOT NULL,
    simulation_id       VARCHAR(9)      NOT NULL,
    action_id           VARCHAR(9)      NULL,
    action_type         VARCHAR(100)    NOT NULL,
    artifact_version_id VARCHAR(9)      NULL,
    layer_number        SMALLINT        NOT NULL DEFAULT 3,
    title               VARCHAR(500)    NULL,
    html_content        LONGTEXT        NOT NULL,
    status              VARCHAR(20)     NOT NULL DEFAULT 'live',
    -- 'live' | 'archived'
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uk_pp_slug (slug),
    KEY idx_pp_user       (user_id),
    KEY idx_pp_simulation (simulation_id),
    KEY idx_pp_action     (action_id)
) ENGINE=InnoDB;
