-- SIM-PRD-SME-002 — SME Simulation Visibility & Advisory
-- Adds the SME authenticated role, structured recommendations, an access audit log,
-- and the user opt-out flag. Run once on deploy (after 037_sme.sql).
--
-- Four parts:
--   simi_smes (ALTER)     — auth_user_id (login identity) + last_login_at
--   sme_recommendations   — typed advice the USER applies with one click
--   sme_access_log        — every SME view / denial (privacy audit)
--   user_profiles (ALTER) — sme_opted_out (blocks auto re-match)
--
-- Note: SME↔User auth is via auth_user_id → users.id. An SME logs in through the
-- normal login form; the /sme console recognises them by the simi_smes row that
-- points at their user id. can_view_l5 is derived in Python (SimiSME.can_view_l5)
-- from the JSON zones list, so no generated column is needed (works on SQLite + MySQL).

-- SME login identity + last login
ALTER TABLE simi_smes
    ADD COLUMN auth_user_id  CHAR(9)  NULL,   -- links to users.id for console login
    ADD COLUMN last_login_at DATETIME NULL;

-- Structured recommendations (the advisory channel)
CREATE TABLE sme_recommendations (
    id            CHAR(9)      NOT NULL,
    sme_id        CHAR(9)      NOT NULL,
    user_id       CHAR(9)      NOT NULL,
    simulation_id CHAR(9)      NULL,
    type          VARCHAR(30)  NOT NULL,   -- swap_agent, add_agent, remove_agent, adjust_rate, revise_artifact, note
    payload       TEXT         NULL,       -- JSON {layer, from, to, action_type, value, ...}
    rationale     TEXT         NOT NULL,
    status        VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- pending | applied | dismissed | expired
    dismiss_reason TEXT        NULL,
    seen_at       DATETIME     NULL,       -- when the user first saw it (unread triage)
    expires_at    DATETIME     NULL,       -- auto-expire window (default +30 days)
    created_at    DATETIME     NULL,
    resolved_at   DATETIME     NULL,
    resolved_by   CHAR(9)      NULL,       -- the USER who applied / dismissed
    PRIMARY KEY (id),
    KEY idx_rec_user (user_id, status),
    KEY idx_rec_sme (sme_id, created_at)
);

-- SME access audit log (high volume)
CREATE TABLE sme_access_log (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    sme_id     CHAR(9)     NOT NULL,
    user_id    CHAR(9)     NULL,
    action     VARCHAR(40) NOT NULL,   -- view_caseload, view_user, view_agents, view_artifact, denied, ...
    detail     TEXT        NULL,       -- JSON
    created_at DATETIME    NULL,
    KEY idx_sal_sme (sme_id, created_at),
    KEY idx_sal_user (user_id, created_at)
);

-- User opt-out state (blocks auto re-match; request-different is a softer flag reusing needs_reassignment)
ALTER TABLE user_profiles
    ADD COLUMN sme_opted_out TINYINT(1) NOT NULL DEFAULT 0;
