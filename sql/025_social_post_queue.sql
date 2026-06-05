-- SIM-PRD: Social post approval queue — all platforms must queue before publishing
-- Run on production MySQL before deploying.

CREATE TABLE IF NOT EXISTS social_post_queue (
    id               VARCHAR(9)      NOT NULL,
    user_id          VARCHAR(9)      NOT NULL,
    platform         VARCHAR(30)     NOT NULL,
    simulation_id    VARCHAR(9)      NULL,
    artifact_id      VARCHAR(9)      NULL,
    post_text        TEXT            NOT NULL,
    status           VARCHAR(20)     NOT NULL DEFAULT 'pending',
    action_item_id   VARCHAR(9)      NULL,
    platform_post_id VARCHAR(200)    NULL,
    reviewed_at      DATETIME        NULL,
    reviewed_by      VARCHAR(9)      NULL,
    created_at       DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_spq_user_status (user_id, status, created_at),
    INDEX idx_spq_platform    (platform, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
