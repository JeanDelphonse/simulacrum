-- Transition Package marketing page (/transition-package): inbound enterprise
-- inquiries. Run once on deploy. Additive.
--
-- Rows come from POST /enterprise/inquiry (validated server-side, CSRF token,
-- honeypot, per-IP rate limit from platform setting enterprise_inquiry_rate_limit).
-- ip_hash is a SHA-256 of the client IP, used only for the rate limit. Staff work
-- inquiries at /admin/inquiries (new | contacted | closed) and can promote one
-- into admin_prospects. Settings: transition_package_mode (design_partner |
-- general), enterprise_inquiry_notify_email (default simi@simulacrumai.io).
--
-- PKs are CHAR(9) from utils.id_gen.generate_id().

CREATE TABLE enterprise_inquiries (
    id                CHAR(9)      PRIMARY KEY,
    name              VARCHAR(160) NOT NULL,
    work_email        VARCHAR(255) NOT NULL,
    company           VARCHAR(200) NOT NULL,
    role              VARCHAR(160) NULL,
    headcount_band    VARCHAR(20)  NULL,
    timing            VARCHAR(30)  NULL,
    notes             TEXT         NULL,
    source            VARCHAR(40)  NOT NULL DEFAULT 'transition-package',
    mode              VARCHAR(20)  NULL,
    status            VARCHAR(10)  NOT NULL DEFAULT 'new' COMMENT 'new | contacted | closed',
    ip_hash           CHAR(64)     NULL,
    prospect_id       CHAR(9)      NULL COMMENT 'admin_prospects.id once promoted',
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status_changed_at DATETIME     NULL
);
CREATE INDEX ix_enterprise_inquiries_status ON enterprise_inquiries (status, created_at);
CREATE INDEX ix_enterprise_inquiries_ip ON enterprise_inquiries (ip_hash, created_at);

-- Anonymous analytics events (no user, no personal data), e.g.
-- transition_package_inquiry.
ALTER TABLE user_events MODIFY user_id CHAR(9) NULL;
