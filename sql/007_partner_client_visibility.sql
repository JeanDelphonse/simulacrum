-- ============================================================
-- SIM-PRD-PART-002 · Partner Client Visibility v1.0
-- Run once against production database.
-- ============================================================

-- 1. New table: advisor_flags
-- FK omitted: column charset must exactly match advisor_access.id — use app-level cascade instead.
CREATE TABLE IF NOT EXISTS advisor_flags (
    id                  CHAR(9)         NOT NULL,
    advisor_access_id   CHAR(9)         NOT NULL,
    simulation_id       CHAR(9)         NOT NULL,
    action_type         VARCHAR(100)    NOT NULL,
    action_id           CHAR(9)         NULL,
    message             VARCHAR(300)    NULL,
    dismissed_at        DATETIME        NULL,
    created_at          DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_flags_access     (advisor_access_id),
    KEY idx_flags_simulation (simulation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. Update advisor_notes: add is_shared, suggestion_type, is_urgent columns
ALTER TABLE advisor_notes
    ADD COLUMN is_shared        TINYINT(1)  NOT NULL DEFAULT 0   AFTER note_text,
    ADD COLUMN suggestion_type  VARCHAR(50) NULL                  AFTER is_shared,
    ADD COLUMN is_urgent        TINYINT(1)  NOT NULL DEFAULT 0   AFTER suggestion_type;

ALTER TABLE advisor_notes
    ADD INDEX idx_notes_shared (simulation_id, is_shared, layer_number);

-- 3. Update advisor_access: add attribution_opt_out column
ALTER TABLE advisor_access
    ADD COLUMN attribution_opt_out TINYINT(1) NOT NULL DEFAULT 0 AFTER last_viewed_at;
