-- PRD-SIM-BLOG-001 — The Community Blog ("The Simulacrum Journal").
-- Run once on deploy.
--
-- Five tables. The PRD also specified a blog_settings key/value table, which is
-- an exact duplicate of the existing platform_settings table, so the thresholds
-- live there instead under a blog_ prefix (blog_posting_enabled,
-- blog_posts_per_day_limit, ...). No migration is needed for them: they are
-- written on first save by the admin panel and read with their documented
-- defaults until then. See SETTINGS_SPEC in app/services/blog_service.py.
--
-- Counts are denormalized (like_count, comment_count) and written in the same
-- transaction as the child row, so the feed and the calendar never run an
-- aggregate per card. Deletion is soft everywhere so threads and counts stay
-- coherent and removed items can render as a tombstone.
--
-- Calendar day buckets are UTC dates of published_at, with no conversion, so
-- every viewer sees the same per-day counts.

CREATE TABLE blog_posts (
    id             CHAR(9)      PRIMARY KEY,
    author_user_id CHAR(9)      NOT NULL,

    title       VARCHAR(200) NOT NULL,
    slug        VARCHAR(230) NOT NULL,
    body        TEXT         NOT NULL                  COMMENT 'Markdown source, as typed',
    body_html   TEXT         NULL                       COMMENT 'rendered on write, never on read',
    excerpt     VARCHAR(400) NULL                       COMMENT 'derived from body on save',
    cover_image VARCHAR(500) NULL,
    tags        VARCHAR(255) NULL                       COMMENT 'comma-separated, lowercase, max 5',

    status       VARCHAR(20) NOT NULL DEFAULT 'draft'   COMMENT 'draft | published | removed',
    published_at DATETIME    NULL                       COMMENT 'the calendar and feed key off this',

    like_count    INT NOT NULL DEFAULT 0,
    comment_count INT NOT NULL DEFAULT 0,

    is_flagged  TINYINT(1) NOT NULL DEFAULT 0           COMMENT 'has an open report; stays visible',
    was_edited  TINYINT(1) NOT NULL DEFAULT 0           COMMENT 'drives the "edited" marker',
    removed_by  CHAR(9)    NULL,
    removed_at  DATETIME   NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT uq_bp_slug UNIQUE (slug),
    CONSTRAINT fk_bp_author  FOREIGN KEY (author_user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_bp_remover FOREIGN KEY (removed_by)     REFERENCES users (id) ON DELETE SET NULL
);

-- The feed and the calendar aggregate both read exactly (status, published_at).
CREATE INDEX idx_bp_status_pub ON blog_posts (status, published_at);
CREATE INDEX idx_bp_author     ON blog_posts (author_user_id, created_at);


CREATE TABLE blog_likes (
    id         CHAR(9)  PRIMARY KEY,
    post_id    CHAR(9)  NOT NULL,
    user_id    CHAR(9)  NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- One like per user per post. This constraint is the enforcement, not the
    -- application check: a double-clicked heart is rejected here.
    CONSTRAINT uq_bl_post_user UNIQUE (post_id, user_id),
    CONSTRAINT fk_bl_post FOREIGN KEY (post_id) REFERENCES blog_posts (id) ON DELETE CASCADE,
    CONSTRAINT fk_bl_user FOREIGN KEY (user_id) REFERENCES users (id)      ON DELETE CASCADE
);

CREATE INDEX idx_bl_user ON blog_likes (user_id, created_at);


CREATE TABLE blog_comments (
    id                CHAR(9) PRIMARY KEY,
    post_id           CHAR(9) NOT NULL,
    user_id           CHAR(9) NOT NULL,
    parent_comment_id CHAR(9) NULL COMMENT 'one reply level only in v1; NULL is top-level',

    body      TEXT NOT NULL,
    body_html TEXT NULL,

    like_count INT        NOT NULL DEFAULT 0,
    is_removed TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'soft delete; renders as a tombstone',
    is_flagged TINYINT(1) NOT NULL DEFAULT 0,
    removed_by CHAR(9)    NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_bc_post    FOREIGN KEY (post_id)           REFERENCES blog_posts (id)    ON DELETE CASCADE,
    CONSTRAINT fk_bc_user    FOREIGN KEY (user_id)           REFERENCES users (id)         ON DELETE CASCADE,
    CONSTRAINT fk_bc_parent  FOREIGN KEY (parent_comment_id) REFERENCES blog_comments (id) ON DELETE CASCADE,
    CONSTRAINT fk_bc_remover FOREIGN KEY (removed_by)        REFERENCES users (id)         ON DELETE SET NULL
);

CREATE INDEX idx_bc_post_created ON blog_comments (post_id, created_at);
CREATE INDEX idx_bc_parent       ON blog_comments (parent_comment_id);


CREATE TABLE blog_comment_likes (
    id         CHAR(9)  PRIMARY KEY,
    comment_id CHAR(9)  NOT NULL,
    user_id    CHAR(9)  NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_bcl_comment_user UNIQUE (comment_id, user_id),
    CONSTRAINT fk_bcl_comment FOREIGN KEY (comment_id) REFERENCES blog_comments (id) ON DELETE CASCADE,
    CONSTRAINT fk_bcl_user    FOREIGN KEY (user_id)    REFERENCES users (id)         ON DELETE CASCADE
);


CREATE TABLE blog_reports (
    id               CHAR(9)     PRIMARY KEY,
    target_type      VARCHAR(10) NOT NULL COMMENT 'post | comment',
    -- Intentionally not a foreign key: one column points at either table.
    target_id        CHAR(9)     NOT NULL,
    reporter_user_id CHAR(9)     NOT NULL,

    reason VARCHAR(30)   NOT NULL COMMENT 'spam | harassment | misinformation | off_topic | other',
    detail VARCHAR(1000) NULL,

    status      VARCHAR(20)   NOT NULL DEFAULT 'open' COMMENT 'open | actioned | dismissed',
    admin_note  VARCHAR(1000) NULL,
    resolved_by CHAR(9)       NULL,
    resolved_at DATETIME      NULL,
    created_at  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- One report per member per item, so re-reporting the same thing is a no-op
    -- rather than a way to pile onto the moderation queue.
    CONSTRAINT uq_br_target_reporter UNIQUE (target_type, target_id, reporter_user_id),
    CONSTRAINT fk_br_reporter FOREIGN KEY (reporter_user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_br_resolver FOREIGN KEY (resolved_by)      REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX idx_br_status_created ON blog_reports (status, created_at);
CREATE INDEX idx_br_target         ON blog_reports (target_type, target_id);
