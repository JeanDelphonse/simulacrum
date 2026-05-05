-- SIM-PRD-NOTIF-001: Notification System
-- Migration 017

CREATE TABLE IF NOT EXISTS notifications (
    id                  VARCHAR(9)      NOT NULL,
    user_id             VARCHAR(9)      NOT NULL,
    simulation_id       VARCHAR(9)      NULL,
    notification_type   VARCHAR(50)     NOT NULL,
    title               VARCHAR(200)    NOT NULL,
    body                TEXT            NOT NULL,
    cta_url             VARCHAR(500)    NULL,
    cta_label           VARCHAR(100)    NULL,
    priority            VARCHAR(10)     NOT NULL DEFAULT 'normal',
    email_sent          TINYINT(1)      NOT NULL DEFAULT 0,
    email_sent_at       DATETIME        NULL,
    read_at             DATETIME        NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_n_user (user_id),
    KEY idx_n_unread (user_id, read_at),
    KEY idx_n_type (notification_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS notification_preferences (
    id                  VARCHAR(9)      NOT NULL,
    user_id             VARCHAR(9)      NOT NULL,
    notification_type   VARCHAR(50)     NOT NULL,
    email_enabled       TINYINT(1)      NOT NULL DEFAULT 1,
    digest_mode         TINYINT(1)      NOT NULL DEFAULT 0,
    updated_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_np_user_type (user_id, notification_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
