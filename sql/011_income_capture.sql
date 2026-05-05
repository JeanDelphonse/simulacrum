-- ============================================================
-- SIM-PRD-INC-001 · Income Capture — DB Migration
-- Run once against production database.
-- ============================================================

CREATE TABLE IF NOT EXISTS layer_income_records (
    id              VARCHAR(9)      NOT NULL,
    simulation_id   VARCHAR(9)      NOT NULL,
    layer_number    INT             NOT NULL,
    action_id       VARCHAR(9)      NULL,
    action_type     VARCHAR(100)    NULL,
    amount          DECIMAL(12,2)   NOT NULL,
    currency        CHAR(3)         NOT NULL DEFAULT 'USD',
    income_date     DATE            NOT NULL,
    source          VARCHAR(50)     NOT NULL DEFAULT 'manual_entry',
    source_ref      VARCHAR(255)    NULL,
    description     TEXT            NULL,
    is_void         TINYINT(1)      NOT NULL DEFAULT 0,
    voided_by_id    VARCHAR(9)      NULL,
    recorded_by     VARCHAR(9)      NOT NULL,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_lir_sim   (simulation_id),
    KEY idx_lir_layer (simulation_id, layer_number),
    KEY idx_lir_date  (income_date)
) ENGINE=InnoDB;


-- Add confirmed-income columns to layer6_outcomes
-- (run once; skip if columns already exist)
ALTER TABLE layer6_outcomes
    ADD COLUMN actual_income_confirmed DECIMAL(12,2) NOT NULL DEFAULT 0 AFTER actual_income,
    ADD COLUMN income_record_count     INT           NOT NULL DEFAULT 0 AFTER actual_income_confirmed,
    ADD COLUMN last_income_date        DATE          NULL     AFTER income_record_count;
