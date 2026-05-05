-- ============================================================
-- SIM-PRD-SIGN-001 · Document Signing Integration — DB Migration
-- Run once against production database.
-- ============================================================

CREATE TABLE IF NOT EXISTS signing_documents (
    id                      VARCHAR(9)      NOT NULL,
    user_id                 VARCHAR(9)      NOT NULL,
    simulation_id           VARCHAR(9)      NOT NULL,
    action_id               VARCHAR(9)      NULL,
    action_type             VARCHAR(100)    NOT NULL,
    artifact_version_id     VARCHAR(9)      NULL,
    layer_number            SMALLINT        NOT NULL DEFAULT 1,
    pandadoc_document_id    VARCHAR(200)    NOT NULL,
    recipient_email         VARCHAR(255)    NOT NULL,
    recipient_name          VARCHAR(200)    NULL,
    document_title          VARCHAR(500)    NULL,
    status                  VARCHAR(20)     NOT NULL DEFAULT 'sent',
    -- 'sent' | 'viewed' | 'signed' | 'declined' | 'expired'
    sent_at                 DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    viewed_at               DATETIME        NULL,
    signed_at               DATETIME        NULL,
    declined_at             DATETIME        NULL,
    created_at              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_sd_user       (user_id),
    KEY idx_sd_simulation (simulation_id),
    KEY idx_sd_action     (action_id),
    KEY idx_sd_pandadoc   (pandadoc_document_id)
) ENGINE=InnoDB;
