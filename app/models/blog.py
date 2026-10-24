"""
PRD-SIM-BLOG-001 - The Community Blog ("The SimiSI Journal").

Models: BlogPost, BlogLike, BlogComment, BlogCommentLike, BlogReport.

Counts (like_count, comment_count) are denormalized onto the parent row and
maintained in the same transaction as the child row, so the feed and the
calendar render without an aggregate per card. Deletion is soft everywhere
(post status 'removed', comment is_removed) so threads and counts stay
consistent and a removed item can render as a neutral tombstone.

Day bucketing for the calendar is UTC: published_at is stored naive UTC and
grouped by its date with no conversion, so every viewer sees the same counts.
"""
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


class BlogPost(db.Model):
    """A member-authored journal post. FR 5.1."""
    __tablename__ = 'blog_posts'

    STATUS_DRAFT     = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_REMOVED   = 'removed'
    STATUSES = (STATUS_DRAFT, STATUS_PUBLISHED, STATUS_REMOVED)

    id             = db.Column(db.String(9), primary_key=True, default=generate_id)
    author_user_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'),
                               nullable=False, index=True)

    title       = db.Column(db.String(200), nullable=False)
    slug        = db.Column(db.String(230), nullable=False, unique=True, index=True)
    body        = db.Column(db.Text, nullable=False)         # Markdown source, as typed
    body_html   = db.Column(db.Text, nullable=True)          # rendered on write, not on read
    excerpt     = db.Column(db.String(400), nullable=True)
    cover_image = db.Column(db.String(500), nullable=True)
    tags        = db.Column(db.String(255), nullable=True)   # comma-separated, lowercase

    status       = db.Column(db.String(20), nullable=False, default=STATUS_DRAFT, index=True)
    published_at = db.Column(db.DateTime, nullable=True, index=True)

    like_count    = db.Column(db.Integer, nullable=False, default=0)
    comment_count = db.Column(db.Integer, nullable=False, default=0)

    is_flagged = db.Column(db.Boolean, nullable=False, default=False)
    was_edited = db.Column(db.Boolean, nullable=False, default=False)  # drives the "edited" marker
    removed_by = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    removed_at = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    __table_args__ = (
        # The feed and the calendar both read (status, published_at) and nothing else.
        db.Index('idx_bp_status_pub', 'status', 'published_at'),
        db.Index('idx_bp_author', 'author_user_id', 'created_at'),
    )

    @property
    def is_published(self):
        return self.status == self.STATUS_PUBLISHED

    @property
    def tag_list(self):
        return [t.strip() for t in (self.tags or '').split(',') if t.strip()]

    def to_dict(self, include_body=False, viewer_liked=None):
        data = {
            'id':            self.id,
            'slug':          self.slug,
            'title':         self.title,
            'excerpt':       self.excerpt or '',
            'cover_image':   self.cover_image,
            'tags':          self.tag_list,
            'status':        self.status,
            'published_at':  self.published_at.isoformat() if self.published_at else None,
            'like_count':    self.like_count,
            'comment_count': self.comment_count,
            'was_edited':    self.was_edited,
            'updated_at':    self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_body:
            data['body']      = self.body
            data['body_html'] = self.body_html or ''
        if viewer_liked is not None:
            data['viewer_liked'] = bool(viewer_liked)
        return data

    def __repr__(self):
        return '<BlogPost %s %r (%s)>' % (self.id, self.title[:40], self.status)


class BlogLike(db.Model):
    """One like per user per post. FR 5.2 - the unique constraint is the enforcement."""
    __tablename__ = 'blog_likes'

    id         = db.Column(db.String(9), primary_key=True, default=generate_id)
    post_id    = db.Column(db.String(9), db.ForeignKey('blog_posts.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    user_id    = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('post_id', 'user_id', name='uq_bl_post_user'),
    )


class BlogComment(db.Model):
    """A comment, or a single-level reply to one (parent_comment_id). FR 5.3."""
    __tablename__ = 'blog_comments'

    id                = db.Column(db.String(9), primary_key=True, default=generate_id)
    post_id           = db.Column(db.String(9), db.ForeignKey('blog_posts.id', ondelete='CASCADE'),
                                  nullable=False, index=True)
    user_id           = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'),
                                  nullable=False, index=True)
    parent_comment_id = db.Column(db.String(9), db.ForeignKey('blog_comments.id', ondelete='CASCADE'),
                                  nullable=True, index=True)

    body      = db.Column(db.Text, nullable=False)
    body_html = db.Column(db.Text, nullable=True)

    like_count = db.Column(db.Integer, nullable=False, default=0)
    is_removed = db.Column(db.Boolean, nullable=False, default=False)
    is_flagged = db.Column(db.Boolean, nullable=False, default=False)
    removed_by = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_bc_post_created', 'post_id', 'created_at'),
    )

    def to_dict(self, viewer_liked=None):
        data = {
            'id':                self.id,
            'post_id':           self.post_id,
            'parent_comment_id': self.parent_comment_id,
            'is_removed':        self.is_removed,
            # A removed comment keeps its slot in the thread but gives up its content.
            'body_html':         '' if self.is_removed else (self.body_html or ''),
            'like_count':        self.like_count,
            'created_at':        self.created_at.isoformat() if self.created_at else None,
        }
        if viewer_liked is not None:
            data['viewer_liked'] = bool(viewer_liked)
        return data


class BlogCommentLike(db.Model):
    """One like per user per comment. FR 5.3."""
    __tablename__ = 'blog_comment_likes'

    id         = db.Column(db.String(9), primary_key=True, default=generate_id)
    comment_id = db.Column(db.String(9), db.ForeignKey('blog_comments.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    user_id    = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('comment_id', 'user_id', name='uq_bcl_comment_user'),
    )


class BlogReport(db.Model):
    """A member flagging a post or comment. Lands in the admin moderation queue. Section 11."""
    __tablename__ = 'blog_reports'

    TARGET_POST    = 'post'
    TARGET_COMMENT = 'comment'
    TARGETS = (TARGET_POST, TARGET_COMMENT)

    STATUS_OPEN      = 'open'
    STATUS_ACTIONED  = 'actioned'
    STATUS_DISMISSED = 'dismissed'
    STATUSES = (STATUS_OPEN, STATUS_ACTIONED, STATUS_DISMISSED)

    REASONS = ('spam', 'harassment', 'misinformation', 'off_topic', 'other')
    REASON_LABELS = {
        'spam':           'Spam or promotion',
        'harassment':     'Harassment or abuse',
        'misinformation': 'Misleading claims',
        'off_topic':      'Off topic',
        'other':          'Something else',
    }

    id               = db.Column(db.String(9), primary_key=True, default=generate_id)
    target_type      = db.Column(db.String(10), nullable=False)
    target_id        = db.Column(db.String(9), nullable=False, index=True)
    reporter_user_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='CASCADE'),
                                 nullable=False, index=True)

    reason = db.Column(db.String(30), nullable=False)
    detail = db.Column(db.String(1000), nullable=True)

    status      = db.Column(db.String(20), nullable=False, default=STATUS_OPEN, index=True)
    admin_note  = db.Column(db.String(1000), nullable=True)
    resolved_by = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        # One report per member per item, so re-reporting the same thing is a no-op.
        db.UniqueConstraint('target_type', 'target_id', 'reporter_user_id',
                            name='uq_br_target_reporter'),
        db.Index('idx_br_status_created', 'status', 'created_at'),
    )

    @property
    def reason_label(self):
        return self.REASON_LABELS.get(self.reason, self.reason)

    def to_dict(self):
        return {
            'id':           self.id,
            'target_type':  self.target_type,
            'target_id':    self.target_id,
            'reason':       self.reason,
            'reason_label': self.reason_label,
            'detail':       self.detail,
            'status':       self.status,
            'admin_note':   self.admin_note,
            'created_at':   self.created_at.isoformat() if self.created_at else None,
            'resolved_at':  self.resolved_at.isoformat() if self.resolved_at else None,
        }
