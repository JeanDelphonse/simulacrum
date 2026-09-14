"""Community Blog routes - PRD-SIM-BLOG-001, section 8.

Public (read is open to guests, per the product decision on section 15):
  GET    /api/blog/posts                       feed, newest first; ?date= ?page= ?author=
  GET    /api/blog/calendar                    per-day counts; ?month=YYYY-MM
  GET    /api/blog/posts/<slug>                one post with its comment thread

Authenticated:
  POST   /api/blog/posts                       create (draft or publish)
  PUT    /api/blog/posts/<id>                  edit (author or admin)
  DELETE /api/blog/posts/<id>                  soft delete (author or admin)
  POST   /api/blog/posts/<id>/like             toggle
  POST   /api/blog/posts/<id>/comments         comment or one-level reply
  POST   /api/blog/comments/<id>/like          toggle
  DELETE /api/blog/comments/<id>               soft delete (comment author, post author, admin)
  POST   /api/blog/reports                     report a post or comment

Admin:
  GET    /api/admin/blog/reports               moderation queue
  PATCH  /api/admin/blog/reports/<id>          action or dismiss
  POST   /api/admin/blog/posts/<id>/remove     remove or restore a post
  POST   /api/admin/blog/comments/<id>/remove  remove a comment
  GET    /api/admin/blog/settings              every threshold with metadata
  PUT    /api/admin/blog/settings              save one or many
"""
from __future__ import annotations

import logging
from datetime import datetime
from functools import wraps

from flask import jsonify, request
from flask_login import current_user, login_required

from app.blueprints.blog import blog_bp
from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.blog import BlogComment, BlogPost, BlogReport
from app.services import blog_service as svc

logger = logging.getLogger(__name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


def _visible_post(slug_or_id, by_slug=True):
    """A post a viewer is allowed to see, or None.

    Drafts and removed posts are visible only to their author and to admins, so
    one lookup covers both the public read and the author's own preview.
    """
    query = BlogPost.query
    post = (query.filter_by(slug=slug_or_id).first() if by_slug
            else query.filter_by(id=slug_or_id).first())
    if not post:
        return None
    if post.status == BlogPost.STATUS_PUBLISHED:
        return post
    return post if svc.can_edit(post, current_user) else None


def _post_payload(post, authors, liked_ids):
    author = authors.get(post.author_user_id, {})
    data = post.to_dict(viewer_liked=post.id in liked_ids)
    data['author'] = author
    data['url'] = '/blog/%s' % post.slug
    return data


# --- Public reads -----------------------------------------------------------

@blog_bp.route('/api/blog/posts', methods=['GET'])
def list_posts():
    on_date = svc.parse_date(request.args.get('date'))
    if request.args.get('date') and not on_date:
        return jsonify({'error': 'date must be YYYY-MM-DD'}), 400

    page = request.args.get('page', 1, type=int) or 1
    author = (request.args.get('author') or '').strip() or None

    if page == 1 and not on_date and not author:
        posts, meta = svc.cached_first_page()
    else:
        posts, meta = svc.feed(page=page, on_date=on_date, author_user_id=author)

    authors = svc.author_map(posts)
    liked = svc.liked_post_ids(current_user, posts)
    return jsonify({
        'posts': [_post_payload(p, authors, liked) for p in posts],
        'page': meta['page'],
        'page_size': meta['page_size'],
        'has_more': meta['has_more'],
        'date': on_date.isoformat() if on_date else None,
    }), 200


@blog_bp.route('/api/blog/calendar', methods=['GET'])
def calendar():
    year, month = svc.parse_month(request.args.get('month'))
    return jsonify(svc.calendar_month(year, month)), 200


@blog_bp.route('/api/blog/posts/<slug>', methods=['GET'])
def get_post(slug):
    post = _visible_post(slug)
    if not post:
        return jsonify({'error': 'Post not found'}), 404

    thread = svc.comment_thread(post)
    comments = svc.flat_comments(thread)
    liked_comments = svc.liked_comment_ids(current_user, comments)
    authors = svc.authors_by_id(
        {post.author_user_id} | {c.user_id for c in comments}
    )

    def node(comment):
        item = comment.to_dict(viewer_liked=comment.id in liked_comments)
        item['author'] = ({} if comment.is_removed
                          else authors.get(comment.user_id, {}))
        return item

    payload = post.to_dict(include_body=True,
                           viewer_liked=post.id in svc.liked_post_ids(current_user, [post]))
    payload['author'] = authors.get(post.author_user_id, {})
    payload['comments'] = [
        dict(node(n['comment']), replies=[node(r) for r in n['replies']])
        for n in thread
    ]
    payload['can_edit'] = svc.can_edit(post, current_user)
    payload['reply_depth'] = svc.setting('blog_reply_depth')
    return jsonify(payload), 200


@blog_bp.route('/api/blog/preview', methods=['POST'])
@login_required
def preview():
    """Render Markdown for the editor's Preview tab.

    The preview goes through the same renderer as a saved post rather than a
    second client-side implementation, so what an author sees here cannot drift
    from what readers get.
    """
    from app.services import blog_markdown
    body = (request.get_json(silent=True) or {}).get('body') or ''
    if len(body) > svc.setting('blog_max_post_chars'):
        return jsonify({'error': 'Post is too long to preview.'}), 400
    return jsonify({'html': blog_markdown.render(body)}), 200


# --- Authoring --------------------------------------------------------------

@blog_bp.route('/api/blog/posts', methods=['POST'])
@login_required
def create_post():
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip()
    body = (data.get('body') or '').strip()
    publish = bool(data.get('publish'))

    error = svc.validate_post(title, body)
    if error:
        return jsonify({'error': error}), 400
    if publish:
        allowed, reason = svc.can_publish(current_user)
        if not allowed:
            return jsonify({'error': reason}), 403

    post = BlogPost(author_user_id=current_user.id, slug=svc.make_slug(title))
    svc.apply_body(post, title, body, data.get('tags'), data.get('cover_image'))
    if publish:
        post.status = BlogPost.STATUS_PUBLISHED
        post.published_at = datetime.utcnow()
    db.session.add(post)
    db.session.commit()

    if publish:
        svc.invalidate()
        _announce(post)

    return jsonify({'post': post.to_dict(include_body=True), 'url': '/blog/%s' % post.slug}), 201


@blog_bp.route('/api/blog/posts/<post_id>', methods=['PUT'])
@login_required
def update_post(post_id):
    post = BlogPost.query.get(post_id)
    if not post:
        return jsonify({'error': 'Post not found'}), 404
    if not svc.can_edit(post, current_user):
        return jsonify({'error': 'You can only edit your own posts'}), 403

    data = request.get_json(silent=True) or {}
    title = (data.get('title') or post.title).strip()
    body = (data.get('body') or post.body).strip()
    error = svc.validate_post(title, body)
    if error:
        return jsonify({'error': error}), 400

    was_published = post.is_published
    publish = data.get('publish')

    if title != post.title:
        post.slug = svc.make_slug(title, post_id=post.id)
    svc.apply_body(post, title, body,
                   data.get('tags', post.tags), data.get('cover_image'))
    # The "edited" marker only means something for a post readers have already seen.
    if was_published:
        post.was_edited = True

    if publish is True and not was_published:
        allowed, reason = svc.can_publish(current_user)
        if not allowed:
            return jsonify({'error': reason}), 403
        post.status = BlogPost.STATUS_PUBLISHED
        post.published_at = post.published_at or datetime.utcnow()
    elif publish is False and was_published:
        # Unpublishing keeps published_at, so restoring later returns it to the
        # same calendar day rather than jumping it to today.
        post.status = BlogPost.STATUS_DRAFT

    db.session.commit()
    svc.invalidate()
    if publish is True and not was_published:
        _announce(post)
    return jsonify({'post': post.to_dict(include_body=True), 'url': '/blog/%s' % post.slug}), 200


@blog_bp.route('/api/blog/posts/<post_id>', methods=['DELETE'])
@login_required
def delete_post(post_id):
    post = BlogPost.query.get(post_id)
    if not post:
        return jsonify({'error': 'Post not found'}), 404
    if not svc.can_edit(post, current_user):
        return jsonify({'error': 'You can only delete your own posts'}), 403

    svc.remove_post(post, current_user)
    if current_user.id != post.author_user_id:
        AuditLog.log('blog_post_removed', user_id=current_user.id, resource_id=post.id,
                     metadata={'title': post.title, 'author_user_id': post.author_user_id})
        db.session.commit()
    return jsonify({'ok': True}), 200


# --- Engagement -------------------------------------------------------------

@blog_bp.route('/api/blog/posts/<post_id>/like', methods=['POST'])
@login_required
def like_post(post_id):
    post = BlogPost.query.filter_by(id=post_id, status=BlogPost.STATUS_PUBLISHED).first()
    if not post:
        return jsonify({'error': 'Post not found'}), 404
    liked, count = svc.toggle_post_like(post, current_user)
    return jsonify({'liked': liked, 'like_count': count}), 200


@blog_bp.route('/api/blog/posts/<post_id>/comments', methods=['GET'])
def list_comments(post_id):
    post = _visible_post(post_id, by_slug=False)
    if not post:
        return jsonify({'error': 'Post not found'}), 404

    thread = svc.comment_thread(post)
    comments = svc.flat_comments(thread)
    liked = svc.liked_comment_ids(current_user, comments)
    authors = svc.authors_by_id({c.user_id for c in comments})

    def node(comment):
        item = comment.to_dict(viewer_liked=comment.id in liked)
        item['author'] = {} if comment.is_removed else authors.get(comment.user_id, {})
        return item

    return jsonify({
        'comments': [dict(node(n['comment']), replies=[node(r) for r in n['replies']])
                     for n in thread],
        'comment_count': post.comment_count,
    }), 200


@blog_bp.route('/api/blog/posts/<post_id>/comments', methods=['POST'])
@login_required
def create_comment(post_id):
    post = BlogPost.query.filter_by(id=post_id, status=BlogPost.STATUS_PUBLISHED).first()
    if not post:
        return jsonify({'error': 'Post not found'}), 404

    allowed, reason = svc.can_comment(current_user)
    if not allowed:
        return jsonify({'error': reason}), 429 if 'quickly' in reason else 403

    data = request.get_json(silent=True) or {}
    comment, error = svc.add_comment(
        post, current_user, data.get('body'), data.get('parent_comment_id'))
    if error:
        return jsonify({'error': error}), 400

    authors = svc.authors_by_id({comment.user_id})
    payload = comment.to_dict(viewer_liked=False)
    payload['author'] = authors.get(comment.user_id, {})
    payload['replies'] = []
    return jsonify({'comment': payload, 'comment_count': post.comment_count}), 201


@blog_bp.route('/api/blog/comments/<comment_id>/like', methods=['POST'])
@login_required
def like_comment(comment_id):
    comment = BlogComment.query.get(comment_id)
    if not comment or comment.is_removed:
        return jsonify({'error': 'Comment not found'}), 404
    liked, count = svc.toggle_comment_like(comment, current_user)
    return jsonify({'liked': liked, 'like_count': count}), 200


@blog_bp.route('/api/blog/comments/<comment_id>', methods=['DELETE'])
@login_required
def delete_comment(comment_id):
    comment = BlogComment.query.get(comment_id)
    if not comment:
        return jsonify({'error': 'Comment not found'}), 404

    post = BlogPost.query.get(comment.post_id)
    # The commenter, the post's author, and any admin can take a comment down.
    allowed = (comment.user_id == current_user.id
               or current_user.is_admin
               or (post and post.author_user_id == current_user.id))
    if not allowed:
        return jsonify({'error': 'You cannot remove that comment'}), 403

    svc.remove_comment(comment, current_user)
    return jsonify({'ok': True, 'comment_count': post.comment_count if post else 0}), 200


@blog_bp.route('/api/blog/reports', methods=['POST'])
@login_required
def create_report():
    data = request.get_json(silent=True) or {}
    report, error = svc.submit_report(
        (data.get('target_type') or '').strip(),
        (data.get('target_id') or '').strip(),
        current_user,
        (data.get('reason') or '').strip(),
        data.get('detail'),
    )
    if error:
        return jsonify({'error': error}), 400
    return jsonify({'ok': True, 'report_id': report.id}), 201


# --- Admin: moderation ------------------------------------------------------

@blog_bp.route('/api/admin/blog/reports', methods=['GET'])
@login_required
@admin_required
def admin_reports():
    status = (request.args.get('status') or '').strip() or None
    if status and status not in BlogReport.STATUSES:
        return jsonify({'error': 'Unknown status'}), 400
    return jsonify({
        'reports': svc.moderation_queue(status=status),
        'stats': svc.moderation_stats(),
    }), 200


@blog_bp.route('/api/admin/blog/reports/<report_id>', methods=['PATCH'])
@login_required
@admin_required
def admin_resolve_report(report_id):
    report = BlogReport.query.get(report_id)
    if not report:
        return jsonify({'error': 'Report not found'}), 404

    data = request.get_json(silent=True) or {}
    status = (data.get('status') or '').strip()
    try:
        svc.resolve_report(report, status, current_user, data.get('admin_note'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    AuditLog.log('blog_report_%s' % status, user_id=current_user.id, resource_id=report.id,
                 metadata={'target_type': report.target_type, 'target_id': report.target_id})
    db.session.commit()
    return jsonify({'report': report.to_dict(), 'stats': svc.moderation_stats()}), 200


@blog_bp.route('/api/admin/blog/posts/<post_id>/remove', methods=['POST'])
@login_required
@admin_required
def admin_remove_post(post_id):
    post = BlogPost.query.get(post_id)
    if not post:
        return jsonify({'error': 'Post not found'}), 404

    restore = bool((request.get_json(silent=True) or {}).get('restore'))
    if restore:
        svc.restore_post(post)
    else:
        svc.remove_post(post, current_user)
    AuditLog.log('blog_post_restored' if restore else 'blog_post_removed',
                 user_id=current_user.id, resource_id=post.id,
                 metadata={'title': post.title})
    db.session.commit()
    return jsonify({'status': post.status, 'stats': svc.moderation_stats()}), 200


@blog_bp.route('/api/admin/blog/comments/<comment_id>/remove', methods=['POST'])
@login_required
@admin_required
def admin_remove_comment(comment_id):
    comment = BlogComment.query.get(comment_id)
    if not comment:
        return jsonify({'error': 'Comment not found'}), 404
    svc.remove_comment(comment, current_user)
    AuditLog.log('blog_comment_removed', user_id=current_user.id, resource_id=comment.id,
                 metadata={'post_id': comment.post_id})
    db.session.commit()
    return jsonify({'ok': True, 'stats': svc.moderation_stats()}), 200


@blog_bp.route('/api/admin/blog/posts', methods=['GET'])
@login_required
@admin_required
def admin_list_posts():
    """Every post regardless of status, so an admin can find and act on one."""
    status = (request.args.get('status') or '').strip() or None
    search = (request.args.get('q') or '').strip()

    query = BlogPost.query
    if status:
        query = query.filter(BlogPost.status == status)
    if search:
        query = query.filter(BlogPost.title.ilike('%%%s%%' % search))
    posts = query.order_by(BlogPost.created_at.desc()).limit(200).all()

    authors = svc.author_map(posts)
    return jsonify({'posts': [
        dict(p.to_dict(),
             author=authors.get(p.author_user_id, {}).get('name', 'Member'),
             is_flagged=p.is_flagged)
        for p in posts
    ]}), 200


# --- Admin: settings --------------------------------------------------------

@blog_bp.route('/api/admin/blog/settings', methods=['GET'])
@login_required
@admin_required
def admin_get_settings():
    return jsonify({'settings': svc.all_settings()}), 200


@blog_bp.route('/api/admin/blog/settings', methods=['PUT'])
@login_required
@admin_required
def admin_put_settings():
    data = (request.get_json(silent=True) or {}).get('settings')
    if not isinstance(data, dict) or not data:
        return jsonify({'error': 'Send a settings object'}), 400

    saved, errors = {}, {}
    for key, value in data.items():
        try:
            saved[key] = svc.save_setting(key, value, updated_by=current_user.id)
        except ValueError as exc:
            errors[key] = str(exc)
    if errors:
        return jsonify({'error': '; '.join(errors.values()), 'errors': errors}), 400

    AuditLog.log('blog_settings_updated', user_id=current_user.id,
                 metadata={'keys': sorted(saved)})
    db.session.commit()
    return jsonify({'settings': svc.all_settings()}), 200


# --- Helpers ----------------------------------------------------------------

def _announce(post):
    """Drop a feed event for the author's connections. Best effort, never fatal.

    Reuses the SOCIAL-001 activity feed rather than inventing a second one, so a
    published post shows up where members already look for their network's news.
    """
    try:
        from app.models.social import ActivityEvent
        event = ActivityEvent(user_id=post.author_user_id, event_type='blog_post_published')
        event.event_data = {'post_id': post.id, 'slug': post.slug, 'title': post.title}
        db.session.add(event)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.warning('blog: could not record activity event for post %s', post.id,
                       exc_info=True)
