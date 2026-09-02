-- Careers — public job board + application intake.
-- Run once on deploy.
--
-- The postings themselves are static content in config/jobs.json (transcribed from
-- docs/Simulacrum_Team_Job_Specs.docx), so only applications need a table. job_slug
-- points at the config entry; job_title is a snapshot so an application still reads
-- correctly after a posting is reworded or pulled.
--
-- Resume files are stored on disk under UPLOAD_FOLDER/careers/ and referenced by
-- resume_path. resume_text is the extracted text, used for the admin preview when a
-- .docx cannot be rendered in the browser.

CREATE TABLE job_applications (
    id              CHAR(9)      PRIMARY KEY,
    job_slug        VARCHAR(80)  NOT NULL                    COMMENT 'key into config/jobs.json',
    job_title       VARCHAR(160) NOT NULL                    COMMENT 'title snapshot at submit time',

    full_name       VARCHAR(160) NOT NULL,
    email           VARCHAR(160) NOT NULL,
    phone           VARCHAR(40)  NOT NULL,

    resume_filename VARCHAR(255) NOT NULL                    COMMENT 'original upload name, for display/download',
    resume_path     VARCHAR(500) NOT NULL                    COMMENT 'absolute path under UPLOAD_FOLDER/careers/',
    resume_type     VARCHAR(10)  NOT NULL                    COMMENT 'pdf | docx | doc',
    resume_size     INT          NULL                        COMMENT 'bytes',
    resume_text     TEXT         NULL                        COMMENT 'extracted text for preview (pdf/docx only)',

    status          VARCHAR(20)  NOT NULL DEFAULT 'new'      COMMENT 'new | reviewing | shortlisted | rejected | hired',
    admin_note      VARCHAR(1000) NULL,

    source_ip       VARCHAR(45)  NULL,
    user_agent      VARCHAR(500) NULL,

    submitted_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at     TIMESTAMP    NULL,
    reviewed_by     CHAR(9)      NULL,

    CONSTRAINT fk_jobapp_reviewer FOREIGN KEY (reviewed_by) REFERENCES users (id) ON DELETE SET NULL,
    -- One application per person per role; a second attempt is rejected with 409.
    CONSTRAINT uq_jobapp_email_slug UNIQUE (email, job_slug)
);

CREATE INDEX idx_jobapp_slug_status ON job_applications (job_slug, status);
CREATE INDEX idx_jobapp_submitted   ON job_applications (submitted_at);
