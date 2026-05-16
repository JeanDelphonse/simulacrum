-- GCC v2.0: action_items table for the Action Queue tab
-- Run on production MySQL before deploying the new zip.

CREATE TABLE IF NOT EXISTS action_items (
    id                  VARCHAR(9)      NOT NULL,
    simulation_id       VARCHAR(9)      NOT NULL,
    user_id             VARCHAR(9)      NOT NULL,
    item_type           VARCHAR(50)     NOT NULL,
    urgency_tier        TINYINT         NOT NULL,
    title               VARCHAR(200)    NOT NULL,
    description         VARCHAR(500)    NULL,
    layer_number        TINYINT         NULL,
    action_label        VARCHAR(50)     NOT NULL,
    action_url          VARCHAR(500)    NOT NULL,
    source_action_id    VARCHAR(9)      NULL,
    source_artifact_id  VARCHAR(9)      NULL,
    source_contact_id   VARCHAR(9)      NULL,
    source_income_id    VARCHAR(9)      NULL,
    status              VARCHAR(20)     NOT NULL DEFAULT 'active',
    resolved_at         DATETIME        NULL,
    dismissed_at        DATETIME        NULL,
    is_dismissable      TINYINT(1)      NOT NULL DEFAULT 1,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_ai_user_active (user_id, simulation_id, status, urgency_tier, created_at),
    INDEX ix_action_items_simulation_id (simulation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
