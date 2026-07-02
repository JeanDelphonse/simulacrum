-- SIM-PRD-ANALYTICS-001: Page views, user events, payment records

CREATE TABLE IF NOT EXISTS page_views (
    id          BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    path        VARCHAR(500)  NOT NULL,
    visitor_id  VARCHAR(64)   NOT NULL,
    session_id  VARCHAR(64)   NULL,
    referrer    VARCHAR(500)  NULL,
    user_agent  VARCHAR(500)  NULL,
    user_id     CHAR(9)       NULL,
    country     VARCHAR(2)    NULL,
    device_type VARCHAR(10)   NULL,
    created_at  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_pv_path_date (path(191), created_at),
    INDEX idx_pv_visitor   (visitor_id, created_at),
    INDEX idx_pv_date      (created_at)
);

CREATE TABLE IF NOT EXISTS user_events (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id     CHAR(9)      NOT NULL,
    event_type  VARCHAR(30)  NOT NULL,
    event_data  JSON         NULL,
    ip_address  VARCHAR(45)  NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_ue_user (user_id, created_at),
    INDEX idx_ue_type (event_type, created_at),
    INDEX idx_ue_date (created_at)
);

CREATE TABLE IF NOT EXISTS payment_records (
    id                BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id           CHAR(9)       NOT NULL,
    simulation_id     CHAR(9)       NULL,
    stripe_payment_id VARCHAR(100)  NOT NULL,
    amount_cents      INT           NOT NULL,
    currency          VARCHAR(3)    NOT NULL DEFAULT 'usd',
    payment_type      VARCHAR(20)   NOT NULL,
    discount_code     VARCHAR(50)   NULL,
    discount_pct      INT           NULL,
    status            VARCHAR(20)   NOT NULL DEFAULT 'succeeded',
    created_at        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_pr_date (created_at),
    INDEX idx_pr_user (user_id),
    INDEX idx_pr_type (payment_type)
);
