-- Founder Track (FOUNDER_PLAN.md section 3): public startup page tables.
-- Run once on deploy, after 072. Additive.
--
-- startup_page          one page per venture at /startup/<slug>.
-- startup_slug_history  old slugs, so old links 301 to the current one.
-- startup_page_visit    startup page views (separate from site-wide page_views).
--
-- PKs are CHAR(9) uppercase from utils.id_gen.generate_pk().

CREATE TABLE startup_page (
    id                   CHAR(9)       PRIMARY KEY,
    venture_id           VARCHAR(9)    NOT NULL,
    slug                 VARCHAR(80)   NOT NULL,
    status               VARCHAR(12)   NOT NULL DEFAULT 'draft' COMMENT 'draft | held | published | unpublished',
    name                 VARCHAR(200)  NOT NULL,
    one_liner            VARCHAR(300)  NULL,
    logo_path            VARCHAR(500)  NULL,
    stage                VARCHAR(12)   NULL,
    problem              TEXT          NULL,
    customer             TEXT          NULL,
    solution             TEXT          NULL,
    demo_url             VARCHAR(500)  NULL,
    market               TEXT          NULL,
    market_method        TEXT          NULL,
    business_model       TEXT          NULL,
    traction             JSON          NULL,
    ask_round            VARCHAR(60)   NULL,
    ask_amount           BIGINT        NULL COMMENT 'whole USD',
    use_of_funds         TEXT          NULL,
    founder_stated       JSON          NULL COMMENT 'fields shown as founder-stated (no evidence yet)',
    name_check           VARCHAR(8)    NOT NULL DEFAULT 'pending' COMMENT 'pending | clear | held',
    published_at         DATETIME      NULL,
    publish_approved_by  VARCHAR(9)    NULL,
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_startup_page_venture (venture_id),
    UNIQUE KEY uq_startup_page_slug (slug),
    KEY ix_startup_page_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE startup_slug_history (
    id               CHAR(9)      PRIMARY KEY,
    startup_page_id  VARCHAR(9)   NOT NULL,
    old_slug         VARCHAR(80)  NOT NULL,
    created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_startup_slug_history_old (old_slug),
    KEY ix_startup_slug_history_page (startup_page_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE startup_page_visit (
    id                CHAR(9)       PRIMARY KEY,
    startup_page_id   VARCHAR(9)    NOT NULL,
    visitor_hash      VARCHAR(64)   NULL,
    investor_link_id  VARCHAR(9)    NULL,
    `view`            VARCHAR(10)   NOT NULL DEFAULT 'public' COMMENT 'public | investor',
    referrer          VARCHAR(500)  NULL,
    created_at        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_startup_page_visit_page (startup_page_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
