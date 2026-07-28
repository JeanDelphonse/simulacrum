-- SIM-PRD-CRM-002 FR-DSC-03 — promote the discovery fit rationale to a
-- first-class column on admin_prospects. Run once on deploy.
--
-- The rationale was previously folded into admin_prospects.notes, which meant it
-- only surfaced in the prospect detail modal and was mixed in with the founder's
-- own notes. FR-DSC-03 asks for it wherever the prospect appears, including the
-- Due-today queue cards, so it needs its own field.
--
-- MySQL has no ADD COLUMN IF NOT EXISTS. If these already exist the statement
-- errors with #1060 Duplicate column name — safe to ignore, it means the database
-- is already up to date.

ALTER TABLE admin_prospects
    ADD COLUMN discovery_rationale TEXT NULL
        COMMENT 'one-line Claude fit rationale from CRM-002 discovery',
    ADD COLUMN discovery_fit VARCHAR(10) NULL
        COMMENT 'fit at discovery time: high|medium|low - kept even if fit is later edited';
