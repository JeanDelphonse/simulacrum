-- Contact invites: a member invites one of their contacts to try SimiSI from
-- /contacts. Run once on deploy. Additive.
--
-- One row per member per address. status 'invited' once the email is sent,
-- 'joined' when that person creates an account (invite link or same email).
-- PKs are CHAR(9) uppercase from utils.id_gen.generate_pk(). The daily cap is
-- the platform setting contact_invite_daily_cap (Admin -> Compliance), created
-- on first save; the default (20) lives in app/services/send_limits.py.

CREATE TABLE contact_invites (
    id                   CHAR(9)       PRIMARY KEY,
    user_id              VARCHAR(9)    NOT NULL,
    contact_id           VARCHAR(9)    NOT NULL,
    email                VARCHAR(255)  NOT NULL,
    token                VARCHAR(64)   NOT NULL,
    status               VARCHAR(10)   NOT NULL DEFAULT 'invited' COMMENT 'invited | joined',
    provider_message_id  VARCHAR(200)  NULL,
    sent_at              DATETIME      NOT NULL,
    joined_at            DATETIME      NULL,
    joined_user_id       VARCHAR(9)    NULL,
    UNIQUE KEY uq_contact_invite_user_email (user_id, email),
    UNIQUE KEY uq_contact_invite_token (token),
    KEY ix_contact_invites_user (user_id),
    KEY ix_contact_invites_contact (contact_id),
    KEY ix_contact_invites_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
