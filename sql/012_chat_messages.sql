-- ============================================================
-- SIM-PRD-CHAT-001 v1.1 · Simulation Chat Co-pilot — DB Migration
-- Run once against production database.
-- No FK constraints (charset/collation mismatch prevention).
-- ============================================================

CREATE TABLE IF NOT EXISTS simulation_chat_messages (
    id              CHAR(9)         NOT NULL,
    session_id      CHAR(9)         NOT NULL DEFAULT 'LEGACY000',
    simulation_id   CHAR(9)         NOT NULL,
    user_id         CHAR(9)         NOT NULL,
    role            VARCHAR(20)     NOT NULL,
    content         TEXT            NOT NULL,
    intent          VARCHAR(50)     NULL,
    action_type     VARCHAR(100)    NULL,
    action_params   JSON            NULL,
    action_status   VARCHAR(20)     NULL,
    action_result   JSON            NULL,
    model_used      VARCHAR(50)     NULL,
    tokens_input    INT             NULL,
    tokens_output   INT             NULL,
    is_archived     TINYINT(1)      NOT NULL DEFAULT 0,
    created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_scm_session (session_id),
    KEY idx_scm_sim     (simulation_id),
    KEY idx_scm_user    (user_id),
    KEY idx_scm_created (simulation_id, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ── Upgrade: run this block if the table already exists without session_id ──
-- ALTER TABLE simulation_chat_messages
--   ADD COLUMN session_id CHAR(9) NOT NULL DEFAULT 'LEGACY000' AFTER id,
--   ADD KEY idx_scm_session (session_id);
