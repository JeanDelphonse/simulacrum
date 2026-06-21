-- SIM-PRD-BIO-003 — Enhanced bio page sections
-- Adds 9 JSON columns to user_profiles for career history, notable work,
-- ventures, education, certifications, press references, publications,
-- projects, and per-section visibility toggles.

ALTER TABLE user_profiles
  ADD COLUMN career_history       TEXT        NULL,
  ADD COLUMN notable_work         TEXT        NULL,
  ADD COLUMN ventures             TEXT        NULL,
  ADD COLUMN education            TEXT        NULL,
  ADD COLUMN certifications       TEXT        NULL,
  ADD COLUMN references_press     TEXT        NULL,
  ADD COLUMN publications         TEXT        NULL,
  ADD COLUMN projects             TEXT        NULL,
  ADD COLUMN bio_sections_visible TEXT        NULL;
