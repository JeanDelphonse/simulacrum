-- Rebrand Simulacrum -> SimiSI (REBRAND_PLAN.md, ruling D11) — stored outreach copy.
--
-- The drip templates are seeded into outreach_templates once, so the code change to
-- DEFAULT_TEMPLATES never reaches rows that already exist. Queued drip/broadcast sends
-- also carry a rendered copy (body_snapshot) taken when they were materialized.
-- This rewrites the brand text in both, in place, so admin edits and approvals survive.
--
-- What changes (REPLACE is case-sensitive in MySQL and SQLite, applied in this order):
--   SimulacrumAI.io -> SimiSI      Simulacrum AI -> SimiSI      SimulacrumAI -> SimiSI
--   Simulacrum      -> SimiSI      SIMULACRUM    -> SIMISI
--   https://simulacrumai.io -> https://simisi.io      http://simulacrumai.io -> https://simisi.io
--
-- What never changes: simi@simulacrumai.io and any other @simulacrumai.io address
-- (no rule matches lowercase "simulacrumai.io" unless it follows "://"), sent/skipped/
-- failed sends (the historical log), and updated_at.
--
-- Idempotent: once applied, no rule matches, so a second run updates nothing.
-- Runs unchanged on MySQL (prod) and SQLite (dev).

UPDATE outreach_templates
SET subject = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(subject,
                'SimulacrumAI.io', 'SimiSI'), 'Simulacrum AI', 'SimiSI'), 'SimulacrumAI', 'SimiSI'),
                'Simulacrum', 'SimiSI'), 'SIMULACRUM', 'SIMISI'),
                'https://simulacrumai.io', 'https://simisi.io'), 'http://simulacrumai.io', 'https://simisi.io'),
    preview_text = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(preview_text,
                'SimulacrumAI.io', 'SimiSI'), 'Simulacrum AI', 'SimiSI'), 'SimulacrumAI', 'SimiSI'),
                'Simulacrum', 'SimiSI'), 'SIMULACRUM', 'SIMISI'),
                'https://simulacrumai.io', 'https://simisi.io'), 'http://simulacrumai.io', 'https://simisi.io'),
    body = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(body,
                'SimulacrumAI.io', 'SimiSI'), 'Simulacrum AI', 'SimiSI'), 'SimulacrumAI', 'SimiSI'),
                'Simulacrum', 'SimiSI'), 'SIMULACRUM', 'SIMISI'),
                'https://simulacrumai.io', 'https://simisi.io'), 'http://simulacrumai.io', 'https://simisi.io'),
    name = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(name,
                'SimulacrumAI.io', 'SimiSI'), 'Simulacrum AI', 'SimiSI'), 'SimulacrumAI', 'SimiSI'),
                'Simulacrum', 'SimiSI'), 'SIMULACRUM', 'SIMISI')
WHERE subject LIKE '%imulacrum%' OR subject LIKE '%IMULACRUM%'
   OR preview_text LIKE '%imulacrum%' OR preview_text LIKE '%IMULACRUM%'
   OR body LIKE '%imulacrum%' OR body LIKE '%IMULACRUM%'
   OR name LIKE '%imulacrum%' OR name LIKE '%IMULACRUM%';

UPDATE outreach_sends
SET subject = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(subject,
                'SimulacrumAI.io', 'SimiSI'), 'Simulacrum AI', 'SimiSI'), 'SimulacrumAI', 'SimiSI'),
                'Simulacrum', 'SimiSI'), 'SIMULACRUM', 'SIMISI'),
                'https://simulacrumai.io', 'https://simisi.io'), 'http://simulacrumai.io', 'https://simisi.io'),
    preview_text = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(preview_text,
                'SimulacrumAI.io', 'SimiSI'), 'Simulacrum AI', 'SimiSI'), 'SimulacrumAI', 'SimiSI'),
                'Simulacrum', 'SimiSI'), 'SIMULACRUM', 'SIMISI'),
                'https://simulacrumai.io', 'https://simisi.io'), 'http://simulacrumai.io', 'https://simisi.io'),
    body_snapshot = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(body_snapshot,
                'SimulacrumAI.io', 'SimiSI'), 'Simulacrum AI', 'SimiSI'), 'SimulacrumAI', 'SimiSI'),
                'Simulacrum', 'SimiSI'), 'SIMULACRUM', 'SIMISI'),
                'https://simulacrumai.io', 'https://simisi.io'), 'http://simulacrumai.io', 'https://simisi.io')
WHERE status IN ('queued', 'awaiting_approval', 'paused', 'scheduled')
  AND (subject LIKE '%imulacrum%' OR subject LIKE '%IMULACRUM%'
       OR preview_text LIKE '%imulacrum%' OR preview_text LIKE '%IMULACRUM%'
       OR body_snapshot LIKE '%imulacrum%' OR body_snapshot LIKE '%IMULACRUM%');
