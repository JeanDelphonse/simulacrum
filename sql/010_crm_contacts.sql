-- ============================================================
-- SIM-PRD-CRM-001 · Contact & Lead Management — DB Migration
-- Run once against production database.
-- ============================================================

CREATE TABLE IF NOT EXISTS contacts (
    id                  VARCHAR(9)      NOT NULL,
    user_id             VARCHAR(9)      NOT NULL,
    first_name          VARCHAR(100)    NOT NULL,
    last_name           VARCHAR(100)    NOT NULL,
    email               VARCHAR(255)    NOT NULL,
    phone               VARCHAR(50)     NULL,
    job_title           VARCHAR(200)    NULL,
    company_name        VARCHAR(200)    NULL,
    company_size        VARCHAR(50)     NULL,
    industry            VARCHAR(100)    NULL,
    department          VARCHAR(100)    NULL,
    seniority           VARCHAR(50)     NULL,
    linkedin_url        VARCHAR(500)    NULL,
    linkedin_headline   VARCHAR(500)    NULL,
    website_url         VARCHAR(500)    NULL,
    company_website     VARCHAR(500)    NULL,
    twitter_url         VARCHAR(500)    NULL,
    other_url           VARCHAR(500)    NULL,
    city                VARCHAR(100)    NULL,
    state_region        VARCHAR(100)    NULL,
    country             VARCHAR(100)    NULL DEFAULT 'United States',
    timezone            VARCHAR(100)    NULL,
    source              VARCHAR(50)     NOT NULL DEFAULT 'manual_entry',
    source_action_id    VARCHAR(9)      NULL,
    source_artifact_id  VARCHAR(9)      NULL,
    source_notes        VARCHAR(500)    NULL,
    qualifying_score    DECIMAL(4,3)    NULL DEFAULT NULL,
    pipeline_stage      ENUM('prospect','active','client','closed_lost') NOT NULL DEFAULT 'prospect',
    last_contacted_at   DATETIME        NULL,
    is_archived         TINYINT(1)      NOT NULL DEFAULT 0,
    do_not_contact      TINYINT(1)      NOT NULL DEFAULT 0,
    notes               TEXT            NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uq_contact_email (user_id, email),
    KEY idx_c_user (user_id),
    KEY idx_c_company (company_name),
    KEY idx_c_score (qualifying_score),
    KEY idx_c_stage (pipeline_stage),
    CONSTRAINT fk_c_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


CREATE TABLE IF NOT EXISTS contact_activities (
    id                  VARCHAR(9)      NOT NULL,
    contact_id          VARCHAR(9)      NOT NULL,
    simulation_id       VARCHAR(9)      NULL,
    action_id           VARCHAR(9)      NULL,
    activity_type       VARCHAR(50)     NOT NULL,
    activity_date       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes               TEXT            NULL,
    pipeline_stage_from VARCHAR(30)     NULL,
    pipeline_stage_to   VARCHAR(30)     NULL,
    created_by          VARCHAR(20)     NOT NULL DEFAULT 'agent',

    PRIMARY KEY (id),
    KEY idx_ca_contact (contact_id),
    KEY idx_ca_simulation (simulation_id),
    KEY idx_ca_action (action_id),
    KEY idx_ca_type (activity_type),
    CONSTRAINT fk_ca_contact FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
