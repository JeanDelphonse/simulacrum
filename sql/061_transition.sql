-- PRD-SIM-TRANS-001 phase 1: Employer Transition Package. Run once on deploy. Additive.
--
-- An employer is an existing organization (corporate_accounts) with
-- org_type = 'transition'. These tables hold its layoff cohorts, the seats it
-- bought (tier, lifecycle, entitlements, the employee's consent choices),
-- coaching sessions with private encrypted notes, and the outcomes employees
-- choose to report. Coaches are SMEs (simi_smes) marked is_coach.
--
-- Privacy: no table here references an employee's simulations, contacts or
-- artifacts; the employer report reads only counts, with small groups hidden.
--
-- Tiers, prices and the other knobs are platform_settings (transition_*),
-- edited at /admin/transition. No price is hardcoded.
--
-- All PKs are CHAR(9), from utils.id_gen.generate_id().

CREATE TABLE transition_cohorts (
    id               CHAR(9)      PRIMARY KEY,
    org_id           CHAR(9)      NOT NULL COMMENT 'corporate_accounts.id (org_type transition)',
    name             VARCHAR(200) NOT NULL,
    last_working_day DATE         NULL,
    invite_date      DATE         NULL COMMENT 'invites go out on or after this day',
    employer_domains TEXT         NULL COMMENT 'JSON list; email import blocks these',
    min_report_size  INT          NULL COMMENT 'NULL = platform default',
    created_by       CHAR(9)      NULL,
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_transition_cohorts_org_id ON transition_cohorts (org_id);

CREATE TABLE transition_seats (
    id                     CHAR(9)      PRIMARY KEY,
    cohort_id              CHAR(9)      NOT NULL,
    org_id                 CHAR(9)      NOT NULL,
    tier                   VARCHAR(12)  NOT NULL COMMENT 'essentials | plus | executive',
    status                 VARCHAR(12)  NOT NULL DEFAULT 'unassigned' COMMENT 'unassigned | invited | activated | expired | revoked',
    full_name              VARCHAR(200) NULL,
    invite_email_enc       TEXT         NULL COMMENT 'personal email, encrypted',
    invite_email_hash      CHAR(64)     NULL,
    invite_token           VARCHAR(64)  NULL UNIQUE,
    invite_sent_at         DATETIME     NULL,
    reminder_count         INT          NOT NULL DEFAULT 0,
    last_reminded_at       DATETIME     NULL,
    user_id                CHAR(9)      NULL,
    invited_at             DATETIME     NULL,
    activated_at           DATETIME     NULL,
    expires_at             DATETIME     NULL,
    revoked_at             DATETIME     NULL,
    access_months          INT          NULL,
    sims_allowed           INT          NOT NULL DEFAULT 0,
    sims_used              INT          NOT NULL DEFAULT 0,
    sessions_total         INT          NOT NULL DEFAULT 0,
    sessions_used          INT          NOT NULL DEFAULT 0,
    no_shows               INT          NOT NULL DEFAULT 0,
    consent_at             DATETIME     NULL,
    share_outcomes         TINYINT(1)   NOT NULL DEFAULT 0,
    confidentiality_ack_at DATETIME     NULL,
    coach_id               CHAR(9)      NULL COMMENT 'simi_smes.id',
    coach_changes          INT          NOT NULL DEFAULT 0,
    coach_access           TINYINT(1)   NOT NULL DEFAULT 0 COMMENT 'coach may see sim summary + bio',
    created_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_seats_cohort ON transition_seats (cohort_id, status);
CREATE INDEX ix_seats_user ON transition_seats (user_id, status);
CREATE INDEX ix_transition_seats_org_id ON transition_seats (org_id);
CREATE INDEX ix_transition_seats_invite_email_hash ON transition_seats (invite_email_hash);

CREATE TABLE coaching_sessions (
    id           CHAR(9)     PRIMARY KEY,
    seat_id      CHAR(9)     NOT NULL,
    coach_id     CHAR(9)     NOT NULL,
    scheduled_at DATETIME    NULL,
    status       VARCHAR(10) NOT NULL DEFAULT 'booked' COMMENT 'booked | held | no_show | cancelled',
    used_credit  TINYINT(1)  NOT NULL DEFAULT 0,
    rating       INT         NULL,
    pay_cents    INT         NULL,
    created_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_coaching_sessions_seat_id ON coaching_sessions (seat_id);
CREATE INDEX ix_coaching_sessions_coach_id ON coaching_sessions (coach_id);

CREATE TABLE coaching_notes (
    id         CHAR(9)     PRIMARY KEY,
    session_id CHAR(9)     NOT NULL,
    author     VARCHAR(10) NOT NULL COMMENT 'employee | coach',
    body_enc   TEXT        NOT NULL COMMENT 'encrypted; employee and coach only',
    created_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_coaching_notes_session_id ON coaching_notes (session_id);

CREATE TABLE transition_outcomes (
    id                  CHAR(9)     PRIMARY KEY,
    seat_id             CHAR(9)     NOT NULL,
    outcome             VARCHAR(12) NOT NULL COMMENT 'new_role | consulting | venture | education | searching | other',
    share_with_employer TINYINT(1)  NOT NULL DEFAULT 0,
    reported_at         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_transition_outcomes_seat_id ON transition_outcomes (seat_id);

ALTER TABLE simi_smes
    ADD COLUMN is_coach          TINYINT(1)   NOT NULL DEFAULT 0,
    ADD COLUMN coach_credentials VARCHAR(300) NULL,
    ADD COLUMN coach_specialties TEXT         NULL COMMENT 'JSON list',
    ADD COLUMN coach_seniority   VARCHAR(10)  NOT NULL DEFAULT 'standard' COMMENT 'standard | senior',
    ADD COLUMN coach_booking_url VARCHAR(500) NULL COMMENT 'Cal.com link',
    ADD COLUMN coach_status      VARCHAR(10)  NOT NULL DEFAULT 'active' COMMENT 'active | review',
    ADD COLUMN coach_avg_rating  FLOAT        NULL;
