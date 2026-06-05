-- SIM-PRD-BIO-002 v1.1 — Bio Page PLG Distribution
-- Adds show_badge, show_on_explore, share_prompt_shown to bio_pages
-- Creates bio_page_visits for first-party visitor analytics

ALTER TABLE bio_pages
  ADD COLUMN show_badge         TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN show_on_explore    TINYINT(1) NOT NULL DEFAULT 1,
  ADD COLUMN share_prompt_shown TINYINT(1) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS bio_page_visits (
  id           VARCHAR(9)   NOT NULL PRIMARY KEY,
  bio_page_id  VARCHAR(9)   NOT NULL,
  visitor_hash VARCHAR(64)  NOT NULL COMMENT 'SHA-256(ip+ua)[:32] for unique-visitor dedup',
  referrer     VARCHAR(255) NOT NULL DEFAULT '',
  utm_source   VARCHAR(100) NOT NULL DEFAULT '',
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_bpv_page_date (bio_page_id, created_at),
  INDEX idx_bpv_hash      (bio_page_id, visitor_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
