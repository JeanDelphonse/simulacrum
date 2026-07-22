-- SIM-PRD-PRIVACY-001 — Bio Page Private Mode
-- Run once on deploy.
--
-- Adds an OPT-IN private mode to bio pages. Default stays public (OFF): SEO, QR,
-- PLG, and open Simi chat all continue to work. When a user turns Private Mode on,
-- strangers see only a minimal teaser; full content is gated behind a
-- LinkedIn-verified access request the owner approves. Approved viewers become
-- verified warm leads in the CRM.
--
-- Enforcement is server-side: gated content is never assembled for an un-approved
-- session (see bio_privacy_service.py), so the gate is a retrieval boundary, not a
-- client-side hide.

-- ── Per-user mode flags on the profile ────────────────────────────────────────
ALTER TABLE user_profiles
    ADD COLUMN privacy_mode       VARCHAR(10)  NOT NULL DEFAULT 'public'   COMMENT 'public | private (FR-PRV-01, default OFF)',
    ADD COLUMN accepting_requests TINYINT(1)   NOT NULL DEFAULT 1          COMMENT 'pause switch: keeps grants live, hides the request form (FR-PRV-07)',
    ADD COLUMN request_notify     VARCHAR(10)  NOT NULL DEFAULT 'realtime' COMMENT 'realtime | digest — access-request notification cadence';

-- ── Access requests (verified identity from LinkedIn OAuth) ───────────────────
CREATE TABLE bio_access_requests (
    id                 CHAR(9)      PRIMARY KEY,
    owner_user_id      CHAR(9)      NOT NULL,
    requester_name     VARCHAR(160) NOT NULL,                   -- from LinkedIn (verified)
    requester_linkedin VARCHAR(300) NOT NULL,                   -- identity anchor (LinkedIn sub / profile URL)
    requester_company  VARCHAR(200) NULL,                       -- from LinkedIn when exposed
    requester_industry VARCHAR(120) NULL,                       -- from LinkedIn when exposed
    requester_email    VARCHAR(160) NULL,                       -- if LinkedIn permits
    requester_avatar   VARCHAR(500) NULL,                       -- LinkedIn picture URL
    message            TEXT         NULL,                        -- optional note (the only free-text field)
    status             VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- pending | approved | revoked | expired | auto_approved | blocked
    created_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at        TIMESTAMP    NULL,
    CONSTRAINT uq_owner_requester UNIQUE (owner_user_id, requester_linkedin)
);
CREATE INDEX idx_bar_owner_status ON bio_access_requests (owner_user_id, status);

-- ── Access grants (person-to-page, persists until revoked) ────────────────────
CREATE TABLE bio_access_grants (
    id                 CHAR(9)      PRIMARY KEY,
    owner_user_id      CHAR(9)      NOT NULL,
    requester_linkedin VARCHAR(300) NOT NULL,
    requester_name     VARCHAR(160) NULL,
    granted_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at         TIMESTAMP    NULL,
    last_viewed_at     TIMESTAMP    NULL,
    view_count         INT          NOT NULL DEFAULT 0,
    CONSTRAINT uq_grant UNIQUE (owner_user_id, requester_linkedin)
);
CREATE INDEX idx_bag_owner_active ON bio_access_grants (owner_user_id, revoked_at);

-- ── Allow / block rules (auto-approve / auto-decline) ─────────────────────────
CREATE TABLE bio_access_rules (
    id            CHAR(9)      PRIMARY KEY,
    owner_user_id CHAR(9)      NOT NULL,
    rule_type     VARCHAR(10)  NOT NULL,   -- allow | block
    match_type    VARCHAR(20)  NOT NULL,   -- domain | company | linkedin
    match_value   VARCHAR(200) NOT NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_bru_owner ON bio_access_rules (owner_user_id);

-- Approved grants create/update a contacts row with
-- source='private_page_request' (pipeline_stage stays 'prospect' — the CRM
-- pipeline enum has no 'warm_lead' value; the private-page source marks it as a
-- verified warm lead). See bio_privacy_service._create_warm_lead().
