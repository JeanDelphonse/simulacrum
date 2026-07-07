-- SIM-PRD-QR-001 — Bio Page QR Code
-- Adds QR storage columns to user_profiles.
--   qr_code_url points to the stored PNG: /static/qr/{slug}.png
--   Regenerated on: publish, slug change, photo add/change/remove.
--   Served statically on page load (no runtime generation).

ALTER TABLE user_profiles
  ADD COLUMN qr_code_url      VARCHAR(500) NULL,
  ADD COLUMN qr_generated_at  DATETIME     NULL;
