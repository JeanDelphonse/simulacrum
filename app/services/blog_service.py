"""
PRD-SIM-BLOG-001 - Community Blog service layer.

Everything that is not request plumbing lives here: the admin-configurable
thresholds, slug minting, the Markdown render pipeline, the calendar aggregate
and its cache, the feed query, and the anti-spam gates.

Two of the PRD's "Algorithms to Live By" lenses are implemented here rather than
merely cited:

  Caching (section 9). A blog is read-heavy and write-light, so the calendar
  aggregate and the first page of the unfiltered feed are memoized in-process
  and dropped wholesale by invalidate() on any publish, edit, or removal. A
  short TTL bounds staleness if a second Passenger worker holds the other copy.

  Optimal stopping (section 9). Pagination commits to a page size
  (blog_feed_page_size) and tells the client whether more exists, instead of an
  infinite scroll that never decides how much is enough.

Settings are stored in the existing platform_settings key/value table under a
blog_ prefix rather than in a second settings table of identical shape. Nothing
below is hardcoded: every threshold reads through SETTINGS_SPEC.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from calendar import monthrange
from datetime import datetime, timedelta

from sqlalchemy import func

from app.extensions import db
from app.models.blog import (
    BlogComment, BlogCommentLike, BlogLike, BlogPost, BlogReport,
)
from app.models.platform_settings import PlatformSetting
from app.services import blog_markdown

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin-configurable settings (section 12)
# ---------------------------------------------------------------------------

# key -> (type, default, label, help). The admin UI is generated from this, so
# adding a knob here is the only edit needed to expose it.
SETTINGS_SPEC = {
    'blog_posting_enabled': (
        'bool', True, 'Posting enabled',
        'Master switch. When off, nobody but an admin can publish a new post.'),
    'blog_min_account_age_days': (
        'int', 0, 'Minimum account age (days)',
        'How established an account must be before it may publish. 0 allows same-day posting.'),
    'blog_posts_per_day_limit': (
        'int', 3, 'Posts per day, per author',
        'Publishes allowed per author in a rolling 24 hours.'),
    'blog_comment_rate_limit': (
        'int', 5, 'Comments per minute, per user',
        'Comments allowed per user in a rolling 60 seconds.'),
    'blog_profanity_filter': (
        'bool', False, 'Profanity filter',
        'Reject posts and comments containing a word from the blocked list below.'),
    'blog_profanity_words': (
        'str', '', 'Blocked words',
        'Comma-separated. Matched whole-word and case-insensitively. Only used when the filter is on.'),
    'blog_reply_depth': (
        'int', 1, 'Reply depth',
        '0 for a flat thread, 1 to allow one level of replies.'),
    'blog_feed_page_size': (
        'int', 10, 'Feed page size',
        'Posts per Load more page.'),
    'blog_default_month': (
        'str', 'current', 'Calendar default month',
        'current opens on this month; latest_activity opens on the most recent month with posts.'),
    'blog_max_post_chars': (
        'int', 40000, 'Maximum post length',
        'Characters of Markdown source allowed in a post body.'),
    'blog_max_comment_chars': (
        'int', 2000, 'Maximum comment length',
        'Characters allowed in a comment.'),
    'blog_nav_min_posts': (
        'int', 0, 'Journal nav link threshold',
        'Published posts required before the Journal link shows in the top nav. 0 always shows it.'),
}

_TRUE = ('1', 'true', 'yes', 'on', 'True')


def setting(key):
    """Read one blog setting, coerced to the type declared in SETTINGS_SPEC."""
    kind, default = SETTINGS_SPEC[key][0], SETTINGS_SPEC[key][1]
    raw = PlatformSetting.get(key)
    if raw is None or raw == '':
        return default
    if kind == 'bool':
        return str(raw) in _TRUE
    if kind == 'int':
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return str(raw)


def all_settings():
    """Every blog setting with its current value and metadata, for the admin panel."""
    return [
        {
            'key':     key,
            'type':    spec[0],
            'value':   setting(key),
            'default': spec[1],
            'label':   spec[2],
            'help':    spec[3],
        }
        for key, spec in SETTINGS_SPEC.items()
    ]


def save_setting(key, value, updated_by=None):
    """Validate and persist one setting. Returns the coerced value."""
    if key not in SETTINGS_SPEC:
        raise ValueError('Unknown setting: %s' % key)
    kind, default = SETTINGS_SPEC[key][0], SETTINGS_SPEC[key][1]

    if kind == 'bool':
        coerced = '1' if (value is True or str(value) in _TRUE) else '0'
    elif kind == 'int':
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError('%s must be a whole number.' % SETTINGS_SPEC[key][2])
        if number < 0:
            raise ValueError('%s cannot be negative.' % SETTINGS_SPEC[key][2])
        if key == 'blog_reply_depth' and number not in (0, 1):
            raise ValueError('Reply depth must be 0 (flat) or 1 (one reply level).')
        if key == 'blog_feed_page_size' and not 1 <= number <= 50:
            raise ValueError('Feed page size must be between 1 and 50.')
        if key in ('blog_max_post_chars', 'blog_max_comment_chars') and number < 100:
            raise ValueError('%s must be at least 100.' % SETTINGS_SPEC[key][2])
        coerced = str(number)
    else:
        coerced = str(value or '').strip()
        if key == 'blog_default_month' and coerced not in ('current', 'latest_activity'):
            raise ValueError('Calendar default month must be current or latest_activity.')

    PlatformSetting.set(key, coerced, updated_by=updated_by)
    invalidate()
    return setting(key)


# ---------------------------------------------------------------------------
# Caching (section 9)
# ---------------------------------------------------------------------------

_CACHE_TTL = 300  # seconds; also bounds staleness across Passenger workers
_cache = {}


def invalidate():
    """Drop every memoized read. Called on publish, unpublish, edit, or removal."""
    _cache.clear()


def _cached(key, producer):
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[1] < _CACHE_TTL:
        return hit[0]
    value = producer()
    _cache[key] = (value, now)
    return value


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------

_SLUG_STRIP = re.compile(r'[^a-z0-9]+')


def make_slug(title, post_id=None):
    """A URL-safe slug for a title, uniquified against the table.

    post_id, when given, is the row being renamed: it is excluded from the
    collision check so re-saving a post with the same title keeps its slug.
    """
    ascii_title = unicodedata.normalize('NFKD', title or '').encode('ascii', 'ignore').decode()
    base = _SLUG_STRIP.sub('-', ascii_title.lower()).strip('-')[:190] or 'post'

    candidate, suffix = base, 2
    while True:
        query = BlogPost.query.filter_by(slug=candidate)
        if post_id:
            query = query.filter(BlogPost.id != post_id)
        if not db.session.query(query.exists()).scalar():
            return candidate
        candidate = '%s-%d' % (base, suffix)
        suffix += 1


# ---------------------------------------------------------------------------
# Content validation and rendering
# ---------------------------------------------------------------------------

_WORD = re.compile(r'[a-z0-9]+')


def profanity_hit(text):
    """The first blocked word present in `text`, or None. Whole-word, case-insensitive."""
    if not setting('blog_profanity_filter'):
        return None
    blocked = {w.strip().lower() for w in setting('blog_profanity_words').split(',') if w.strip()}
    if not blocked:
        return None
    words = set(_WORD.findall(blog_markdown.to_text(text).lower()))
    overlap = words & blocked
    return sorted(overlap)[0] if overlap else None


def validate_post(title, body):
    """Returns an error string, or None when the post is acceptable."""
    title = (title or '').strip()
    body = (body or '').strip()
    if len(title) < 3:
        return 'Give your post a title of at least 3 characters.'
    if len(title) > 200:
        return 'Titles are limited to 200 characters.'
    if len(body) < 20:
        return 'Write at least a couple of sentences before publishing.'
    limit = setting('blog_max_post_chars')
    if len(body) > limit:
        return 'Posts are limited to %s characters.' % '{:,}'.format(limit)
    hit = profanity_hit(title + ' ' + body)
    if hit:
        return 'Your post contains a word that is not allowed here ("%s").' % hit
    return None


def validate_comment(body):
    body = (body or '').strip()
    if len(body) < 2:
        return 'Write something before posting.'
    limit = setting('blog_max_comment_chars')
    if len(body) > limit:
        return 'Comments are limited to %s characters.' % '{:,}'.format(limit)
    hit = profanity_hit(body)
    if hit:
        return 'Your comment contains a word that is not allowed here ("%s").' % hit
    return None


def normalize_tags(raw):
    """Up to 5 lowercase tags from a comma-separated string, or None."""
    if not raw:
        return None
    seen = []
    for tag in str(raw).split(','):
        clean = _SLUG_STRIP.sub('-', tag.strip().lower()).strip('-')[:30]
        if clean and clean not in seen:
            seen.append(clean)
    return ','.join(seen[:5]) or None


def clean_cover_image(raw):
    """An http(s) cover image URL, or None.

    The value lands in an img src, so anything that is not plainly an absolute
    http(s) URL is dropped rather than stored and rendered.
    """
    url = (raw or '').strip()
    if not url:
        return None
    if not re.match(r'^https?://[^\s<>"\']+$', url, re.IGNORECASE):
        return None
    return url[:500]


def apply_body(post, title, body, tags=None, cover_image=None):
    """Set the authored fields on a post and refresh the derived ones."""
    post.title = (title or '').strip()
    post.body = (body or '').strip()
    post.body_html = blog_markdown.render(post.body)
    post.excerpt = blog_markdown.excerpt(post.body, 240)[:400]
    post.tags = normalize_tags(tags)
    if cover_image is not None:
        post.cover_image = clean_cover_image(cover_image)
    return post


# ---------------------------------------------------------------------------
# Posting eligibility (sections 11 and 12)
# ---------------------------------------------------------------------------

def can_publish(user):
    """(allowed, reason). Admins bypass every gate so moderation is never locked out."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False, 'Log in to publish a post.'
    if getattr(user, 'is_admin', False):
        return True, None
    if not setting('blog_posting_enabled'):
        return False, 'Posting to the Journal is paused right now. Check back soon.'

    min_age = setting('blog_min_account_age_days')
    if min_age and user.created_at:
        age_days = (datetime.utcnow() - user.created_at).days
        if age_days < min_age:
            return False, ('New accounts can publish after %d day%s. Yours is %d day%s old.'
                           % (min_age, '' if min_age == 1 else 's',
                              age_days, '' if age_days == 1 else 's'))

    limit = setting('blog_posts_per_day_limit')
    if limit:
        since = datetime.utcnow() - timedelta(days=1)
        used = db.session.query(func.count(BlogPost.id)).filter(
            BlogPost.author_user_id == user.id,
            BlogPost.published_at.isnot(None),
            BlogPost.published_at >= since,
        ).scalar() or 0
        if used >= limit:
            return False, ('You have published %d post%s today, which is the daily limit. '
                           'Save this as a draft and publish it tomorrow.'
                           % (used, '' if used == 1 else 's'))
    return True, None


def can_comment(user):
    """(allowed, reason). Rate limit is a rolling 60-second window per user."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False, 'Log in to comment.'
    if getattr(user, 'is_admin', False):
        return True, None
    if not setting('blog_posting_enabled'):
        return False, 'The Journal is read-only right now.'

    limit = setting('blog_comment_rate_limit')
    if limit:
        since = datetime.utcnow() - timedelta(seconds=60)
        used = db.session.query(func.count(BlogComment.id)).filter(
            BlogComment.user_id == user.id,
            BlogComment.created_at >= since,
        ).scalar() or 0
        if used >= limit:
            return False, 'You are commenting quickly. Give it a minute and try again.'
    return True, None


def can_edit(post, user):
    """Authors edit their own; admins edit any. Section 3 permission matrix."""
    if not post or not user or not getattr(user, 'is_authenticated', False):
        return False
    return post.author_user_id == user.id or bool(getattr(user, 'is_admin', False))


# ---------------------------------------------------------------------------
# The calendar (section 6)
# ---------------------------------------------------------------------------

_MONTH_RE = re.compile(r'^(\d{4})-(\d{2})$')

MONTH_NAMES = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
               'August', 'September', 'October', 'November', 'December')


def parse_month(raw):
    """'YYYY-MM' to (year, month), falling back to the configured default month."""
    match = _MONTH_RE.match(str(raw or ''))
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12 and 2000 <= year <= 2999:
            return year, month
    return default_month()


def default_month():
    """The month the calendar opens on, per blog_default_month."""
    today = datetime.utcnow()
    if setting('blog_default_month') == 'latest_activity':
        latest = db.session.query(func.max(BlogPost.published_at)).filter(
            BlogPost.status == BlogPost.STATUS_PUBLISHED
        ).scalar()
        if latest:
            return latest.year, latest.month
    return today.year, today.month


def parse_date(raw):
    """'YYYY-MM-DD' to a date, or None. Used by the ?date= feed filter."""
    try:
        return datetime.strptime(str(raw or '').strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def calendar_month(year, month):
    """The month's grid plus a per-day count of published posts.

    One aggregate query per month, memoized. Day buckets are UTC dates of
    published_at, so the counts are identical for every viewer.
    """
    def build():
        first = datetime(year, month, 1)
        days_in_month = monthrange(year, month)[1]
        # Exclusive upper bound, so a post published at 23:59:59.999 still counts.
        end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)

        rows = db.session.query(
            func.date(BlogPost.published_at), func.count(BlogPost.id)
        ).filter(
            BlogPost.status == BlogPost.STATUS_PUBLISHED,
            BlogPost.published_at >= first,
            BlogPost.published_at < end,
        ).group_by(func.date(BlogPost.published_at)).all()

        # SQLite returns a str from func.date(); MySQL returns a date.
        counts = {}
        for bucket, total in rows:
            key = bucket if isinstance(bucket, str) else bucket.isoformat()
            counts[key[:10]] = int(total)

        today_iso = datetime.utcnow().date().isoformat()
        days = []
        for day in range(1, days_in_month + 1):
            iso = '%04d-%02d-%02d' % (year, month, day)
            count = counts.get(iso, 0)
            days.append({
                'day':      day,
                'date':     iso,
                'count':    count,
                'is_today': iso == today_iso,
                # The ARIA label the mockup calls for: "September 5, 2 posts".
                'label':    '%s %d, %s' % (
                    MONTH_NAMES[month - 1], day,
                    'no posts' if not count else
                    ('1 post' if count == 1 else '%d posts' % count)),
            })

        prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        now = datetime.utcnow()

        return {
            'year':        year,
            'month':       month,
            'month_key':   '%04d-%02d' % (year, month),
            'month_name':  MONTH_NAMES[month - 1],
            'label':       '%s %d' % (MONTH_NAMES[month - 1], year),
            # Sunday-first, to match the S M T W T F S header in the mockup.
            'lead_blanks': (first.weekday() + 1) % 7,
            'days':        days,
            'total':       sum(d['count'] for d in days),
            'prev_month':  '%04d-%02d' % (prev_year, prev_month),
            # No next link past the current month; there is nothing to see there.
            'next_month':  (None if (year, month) >= (now.year, now.month)
                            else '%04d-%02d' % (next_year, next_month)),
        }

    return _cached('cal:%04d-%02d' % (year, month), build)


# ---------------------------------------------------------------------------
# The feed (sections 5.1 and 9)
# ---------------------------------------------------------------------------

def _published_query():
    return BlogPost.query.filter(BlogPost.status == BlogPost.STATUS_PUBLISHED)


def feed(page=1, on_date=None, author_user_id=None, page_size=None):
    """A page of published posts, newest first.

    Deliberately chronological (section 9, explore vs. exploit): v1 does not
    weight by likes, so a post's reach does not depend on how it started.
    Returns (posts, meta) where meta carries has_more for the Load more control.
    """
    size = page_size or setting('blog_feed_page_size')
    page = max(1, int(page or 1))

    query = _published_query()
    if on_date:
        start = datetime(on_date.year, on_date.month, on_date.day)
        query = query.filter(BlogPost.published_at >= start,
                             BlogPost.published_at < start + timedelta(days=1))
    if author_user_id:
        query = query.filter(BlogPost.author_user_id == author_user_id)

    # Fetch one extra row to learn whether a further page exists, without a count query.
    rows = (query.order_by(BlogPost.published_at.desc(), BlogPost.id.desc())
                 .limit(size + 1).offset((page - 1) * size).all())
    has_more = len(rows) > size
    return rows[:size], {'page': page, 'page_size': size, 'has_more': has_more}


def cached_first_page():
    """The unfiltered first page, memoized. This is the hit most readers take."""
    def build():
        posts, meta = feed(page=1)
        return [p.id for p in posts], meta

    ids, meta = _cached('feed:1', build)
    if not ids:
        return [], meta
    # Re-read the rows so counts edited since the cache was filled are current;
    # what is cached is the ordering decision, which is the expensive part.
    by_id = {p.id: p for p in BlogPost.query.filter(BlogPost.id.in_(ids)).all()}
    return [by_id[i] for i in ids if i in by_id], meta


def published_count():
    return _cached('count', lambda: db.session.query(func.count(BlogPost.id)).filter(
        BlogPost.status == BlogPost.STATUS_PUBLISHED).scalar() or 0)


def nav_visible():
    """Whether the top-nav Journal link should render (blog_nav_min_posts)."""
    threshold = setting('blog_nav_min_posts')
    return True if threshold <= 0 else published_count() >= threshold


# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------

def author_map(posts):
    """user_id -> {name, username, avatar_path, initials} for a batch of posts.

    One query for users and one for profiles, rather than a lazy load per card.
    """
    ids = {p.author_user_id for p in posts if p.author_user_id}
    return authors_by_id(ids)


def authors_by_id(user_ids):
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    from app.models.profile import UserProfile
    from app.models.user import User

    users = {u.id: u for u in User.query.filter(User.id.in_(ids)).all()}
    profiles = {p.user_id: p for p in UserProfile.query.filter(UserProfile.user_id.in_(ids)).all()}

    out = {}
    for uid in ids:
        user, profile = users.get(uid), profiles.get(uid)
        name = ((profile.display_name if profile else None)
                or (user.full_name if user else None) or 'Member')
        parts = [p for p in name.split() if p]
        out[uid] = {
            'user_id':     uid,
            'name':        name,
            'username':    profile.username if profile else None,
            'avatar_path': profile.avatar_path if profile else None,
            'initials':    ''.join(p[0].upper() for p in parts[:2]) or 'M',
        }
    return out


# ---------------------------------------------------------------------------
# Likes
# ---------------------------------------------------------------------------

def liked_post_ids(user, posts):
    """The subset of `posts` the viewer has already liked. One query, not N."""
    if not user or not getattr(user, 'is_authenticated', False):
        return set()
    ids = [p.id for p in posts if p.id]
    if not ids:
        return set()
    rows = db.session.query(BlogLike.post_id).filter(
        BlogLike.user_id == user.id, BlogLike.post_id.in_(ids)
    ).all()
    return {r[0] for r in rows}


def liked_comment_ids(user, comments):
    if not user or not getattr(user, 'is_authenticated', False):
        return set()
    ids = [c.id for c in comments if c.id]
    if not ids:
        return set()
    rows = db.session.query(BlogCommentLike.comment_id).filter(
        BlogCommentLike.user_id == user.id, BlogCommentLike.comment_id.in_(ids)
    ).all()
    return {r[0] for r in rows}


def toggle_post_like(post, user):
    """Add or remove a like, keeping post.like_count in the same transaction.

    Returns (liked, like_count). The unique constraint is the real guard: a
    duplicate insert from a double-click raises and is recovered as "already
    liked" rather than double-counting.
    """
    existing = BlogLike.query.filter_by(post_id=post.id, user_id=user.id).first()
    if existing:
        db.session.delete(existing)
        post.like_count = max(0, (post.like_count or 0) - 1)
        db.session.commit()
        return False, post.like_count

    db.session.add(BlogLike(post_id=post.id, user_id=user.id))
    post.like_count = (post.like_count or 0) + 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return True, recount_post_likes(post)
    return True, post.like_count


def toggle_comment_like(comment, user):
    existing = BlogCommentLike.query.filter_by(comment_id=comment.id, user_id=user.id).first()
    if existing:
        db.session.delete(existing)
        comment.like_count = max(0, (comment.like_count or 0) - 1)
        db.session.commit()
        return False, comment.like_count

    db.session.add(BlogCommentLike(comment_id=comment.id, user_id=user.id))
    comment.like_count = (comment.like_count or 0) + 1
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        comment.like_count = db.session.query(func.count(BlogCommentLike.id)).filter(
            BlogCommentLike.comment_id == comment.id).scalar() or 0
        db.session.commit()
    return True, comment.like_count


def recount_post_likes(post):
    post.like_count = db.session.query(func.count(BlogLike.id)).filter(
        BlogLike.post_id == post.id).scalar() or 0
    db.session.commit()
    return post.like_count


def recount_post_comments(post):
    """Removed comments still occupy the thread but are not advertised in the count."""
    post.comment_count = db.session.query(func.count(BlogComment.id)).filter(
        BlogComment.post_id == post.id,
        BlogComment.is_removed.is_(False),
    ).scalar() or 0
    db.session.commit()
    return post.comment_count


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def comment_thread(post):
    """Top-level comments in order, each with its replies attached.

    Removed comments are kept so the structure survives moderation, and a
    removed parent with no surviving replies is dropped entirely so the page is
    not littered with tombstones nobody was replying to.
    """
    rows = (BlogComment.query.filter_by(post_id=post.id)
            .order_by(BlogComment.created_at.asc()).all())
    replies = {}
    for row in rows:
        if row.parent_comment_id:
            replies.setdefault(row.parent_comment_id, []).append(row)

    thread = []
    for row in rows:
        if row.parent_comment_id:
            continue
        kids = replies.get(row.id, [])
        if row.is_removed and not any(not k.is_removed for k in kids):
            continue
        thread.append({'comment': row, 'replies': kids})
    return thread


def flat_comments(thread):
    """Every comment in a thread, as one list. For the batch viewer-liked lookup."""
    out = []
    for node in thread:
        out.append(node['comment'])
        out.extend(node['replies'])
    return out


def add_comment(post, user, body, parent_comment_id=None):
    """(comment, error). Enforces reply_depth and that the parent is on this post."""
    error = validate_comment(body)
    if error:
        return None, error

    parent = None
    if parent_comment_id:
        if setting('blog_reply_depth') < 1:
            return None, 'Replies are turned off right now. Post a comment instead.'
        parent = BlogComment.query.filter_by(id=parent_comment_id, post_id=post.id).first()
        if not parent:
            return None, 'That comment no longer exists.'
        if parent.parent_comment_id:
            # One level only. A reply to a reply is re-parented onto the top comment
            # rather than rejected, which is what the commenter meant anyway.
            parent = BlogComment.query.get(parent.parent_comment_id) or parent

    text = (body or '').strip()
    comment = BlogComment(
        post_id=post.id,
        user_id=user.id,
        parent_comment_id=parent.id if parent else None,
        body=text,
        body_html=blog_markdown.render_plain(text),
    )
    db.session.add(comment)
    post.comment_count = (post.comment_count or 0) + 1
    db.session.commit()
    return comment, None


def remove_comment(comment, actor):
    """Soft removal, so the thread and the reply structure survive. Section 11."""
    if comment.is_removed:
        return
    comment.is_removed = True
    comment.removed_by = actor.id
    db.session.commit()
    post = BlogPost.query.get(comment.post_id)
    if post:
        recount_post_comments(post)


# ---------------------------------------------------------------------------
# Reports and moderation (sections 11 and 8)
# ---------------------------------------------------------------------------

def submit_report(target_type, target_id, user, reason, detail=None):
    """(report, error). A repeat report from the same member is idempotent."""
    if target_type not in BlogReport.TARGETS:
        return None, 'Unknown report target.'
    if reason not in BlogReport.REASONS:
        return None, 'Pick a reason for the report.'

    if target_type == BlogReport.TARGET_POST:
        target = BlogPost.query.get(target_id)
    else:
        target = BlogComment.query.get(target_id)
    if not target:
        return None, 'That content no longer exists.'

    existing = BlogReport.query.filter_by(
        target_type=target_type, target_id=target_id, reporter_user_id=user.id).first()
    if existing:
        return existing, None

    report = BlogReport(
        target_type=target_type, target_id=target_id, reporter_user_id=user.id,
        reason=reason, detail=(detail or '').strip()[:1000] or None,
    )
    db.session.add(report)
    # Flagged content stays visible; the flag is a signal to the queue, not a removal.
    target.is_flagged = True
    db.session.commit()
    return report, None


def moderation_queue(status=None, limit=100):
    """Open reports newest first, hydrated with enough of the target to judge it."""
    query = BlogReport.query
    if status:
        query = query.filter(BlogReport.status == status)
    reports = query.order_by(BlogReport.created_at.desc()).limit(limit).all()
    if not reports:
        return []

    post_ids = [r.target_id for r in reports if r.target_type == BlogReport.TARGET_POST]
    comment_ids = [r.target_id for r in reports if r.target_type == BlogReport.TARGET_COMMENT]
    posts = ({p.id: p for p in BlogPost.query.filter(BlogPost.id.in_(post_ids)).all()}
             if post_ids else {})
    comments = ({c.id: c for c in BlogComment.query.filter(BlogComment.id.in_(comment_ids)).all()}
                if comment_ids else {})

    parent_ids = {c.post_id for c in comments.values()} - set(posts)
    if parent_ids:
        posts.update({p.id: p for p in BlogPost.query.filter(BlogPost.id.in_(parent_ids)).all()})

    author_ids = ({p.author_user_id for p in posts.values()}
                  | {c.user_id for c in comments.values()}
                  | {r.reporter_user_id for r in reports})
    authors = authors_by_id(author_ids)

    out = []
    for report in reports:
        item = report.to_dict()
        item['reporter'] = authors.get(report.reporter_user_id, {}).get('name', 'Member')
        if report.target_type == BlogReport.TARGET_POST:
            post = posts.get(report.target_id)
            item['target'] = {
                'exists':  post is not None,
                'title':   post.title if post else '(deleted)',
                'slug':    post.slug if post else None,
                'snippet': (post.excerpt or '') if post else '',
                'author':  authors.get(post.author_user_id, {}).get('name', 'Member') if post else '',
                'removed': post.status == BlogPost.STATUS_REMOVED if post else True,
            }
        else:
            comment = comments.get(report.target_id)
            post = posts.get(comment.post_id) if comment else None
            item['target'] = {
                'exists':  comment is not None,
                'title':   ('Comment on "%s"' % post.title) if post else 'Comment',
                'slug':    post.slug if post else None,
                'snippet': blog_markdown.to_text(comment.body)[:300] if comment else '',
                'author':  authors.get(comment.user_id, {}).get('name', 'Member') if comment else '',
                'removed': comment.is_removed if comment else True,
            }
        out.append(item)
    return out


def resolve_report(report, status, actor, note=None):
    if status not in BlogReport.STATUSES:
        raise ValueError('Unknown report status.')
    report.status = status
    report.resolved_by = actor.id
    report.resolved_at = datetime.utcnow()
    if note is not None:
        report.admin_note = note.strip()[:1000] or None
    db.session.commit()
    return report


def remove_post(post, actor):
    """Admin or author removal. Soft, so counts and threads stay coherent."""
    post.status = BlogPost.STATUS_REMOVED
    post.removed_by = actor.id
    post.removed_at = datetime.utcnow()
    db.session.commit()
    invalidate()
    return post


def restore_post(post):
    """Undo a removal. A post that had been published returns to published."""
    post.status = (BlogPost.STATUS_PUBLISHED if post.published_at
                   else BlogPost.STATUS_DRAFT)
    post.removed_by = None
    post.removed_at = None
    post.is_flagged = False
    db.session.commit()
    invalidate()
    return post


def moderation_stats():
    open_reports = db.session.query(func.count(BlogReport.id)).filter(
        BlogReport.status == BlogReport.STATUS_OPEN).scalar() or 0
    rows = dict(db.session.query(BlogPost.status, func.count(BlogPost.id))
                .group_by(BlogPost.status).all())
    return {
        'open_reports': open_reports,
        'published':    rows.get(BlogPost.STATUS_PUBLISHED, 0),
        'drafts':       rows.get(BlogPost.STATUS_DRAFT, 0),
        'removed':      rows.get(BlogPost.STATUS_REMOVED, 0),
        'comments':     db.session.query(func.count(BlogComment.id)).filter(
                            BlogComment.is_removed.is_(False)).scalar() or 0,
    }
