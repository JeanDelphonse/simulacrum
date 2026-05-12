-- ============================================================
-- SIM-PRD-VIEW-001 v1.0 · Artifact Viewer — DB Migration
-- Run once against production database.
-- No FK constraints (charset/collation mismatch prevention).
-- ============================================================

ALTER TABLE artifact_versions
    ADD COLUMN edited_by         VARCHAR(20)  NOT NULL DEFAULT 'agent'    AFTER content,
    ADD COLUMN edit_summary      VARCHAR(255) NULL                         AFTER edited_by,
    ADD COLUMN parent_version_id CHAR(9)      NULL                         AFTER edit_summary,
    ADD COLUMN draft_content     LONGTEXT     NULL                         AFTER parent_version_id,
    ADD COLUMN edited_at         DATETIME     NULL                         AFTER draft_content,
    ADD COLUMN draft_updated_at  DATETIME     NULL                         AFTER edited_at;

-- Backfill edited_by from existing created_by where present.
-- created_by stored 'user' or 'orchestrator'; we map orchestrator -> agent.
UPDATE artifact_versions
   SET edited_by = CASE
       WHEN created_by = 'user' THEN 'user'
       WHEN created_by = 'co-pilot' THEN 'co-pilot'
       ELSE 'agent'
   END
 WHERE edited_by = 'agent';
