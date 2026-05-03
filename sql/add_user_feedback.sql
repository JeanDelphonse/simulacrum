-- SIM-PRD-FBK-001 — Add user_feedback table
-- Run against MySQL production database.

CREATE TABLE IF NOT EXISTS user_feedback (
    id          VARCHAR(9)   NOT NULL PRIMARY KEY,
    user_id     VARCHAR(9)   NOT NULL,
    simulation_id VARCHAR(9) NULL,

    star_rating TINYINT      NOT NULL,
    layers_attributed JSON   NULL,
    outcome_text VARCHAR(300) NOT NULL,
    quote_text   VARCHAR(200) NOT NULL,

    name_display ENUM('full','first_last_initial','first_only','anonymous')
                 NOT NULL DEFAULT 'first_last_initial',
    status       ENUM('pending','approved','rejected')
                 NOT NULL DEFAULT 'pending',

    admin_note   VARCHAR(500) NULL,
    approved_by  VARCHAR(9)   NULL,
    approved_at  DATETIME     NULL,

    is_featured  TINYINT(1)   NOT NULL DEFAULT 0,
    display_order INT         NULL,

    expertise_zone_snapshot VARCHAR(500) NULL,
    withdrawn_requested_at  DATETIME     NULL,

    submitted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME NULL ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_uf_user   FOREIGN KEY (user_id)      REFERENCES users(id)       ON DELETE CASCADE,
    CONSTRAINT fk_uf_sim    FOREIGN KEY (simulation_id) REFERENCES simulations(id) ON DELETE SET NULL,
    CONSTRAINT fk_uf_admin  FOREIGN KEY (approved_by)   REFERENCES users(id)       ON DELETE SET NULL,

    INDEX ix_uf_user_id     (user_id),
    INDEX ix_uf_status      (status),
    INDEX ix_uf_featured    (is_featured),
    INDEX ix_uf_disp_order  (display_order)
);
