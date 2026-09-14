"""The Community Blog — PRD-SIM-BLOG-001.

Covers the boundaries that matter: reading is open to strangers while every
write needs a login; the calendar's per-day counts match what the feed returns
for that day; the admin thresholds actually bind (posting switch, account age,
posts/day, comments/minute, reply depth, profanity, page size); a like is one
per user per post; removals are soft and keep the thread and the counts honest;
and an author can edit only their own post.

Against in-memory SQLite. No network, no real data.

    python test_blog.py
"""
import os
import re
import sys
from datetime import datetime, timedelta

os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label,
                              ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def section(title):
    print('\n' + title)


def main():
    from app import create_app
    from app.extensions import db, bcrypt
    from utils.id_gen import generate_id

    app = create_app('testing')
    app.config.update(
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        WTF_CSRF_ENABLED=False,
    )

    with app.app_context():
        from app.models.blog import (
            BlogComment, BlogCommentLike, BlogLike, BlogPost, BlogReport,
        )
        from app.models.profile import UserProfile
        from app.models.user import User
        from app.services import blog_markdown, blog_service as svc

        db.create_all()

        # onboarding_completed_at is set because the app-wide onboarding gate
        # redirects any half-onboarded login away from HTML pages.
        now = datetime.utcnow()

        def make_user(email, name, admin=False, age_days=30):
            user = User(id=generate_id(), email=email, full_name=name,
                        password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                        is_admin=admin, email_verified=True, onboarding_completed_at=now,
                        created_at=now - timedelta(days=age_days))
            db.session.add(user)
            db.session.commit()
            db.session.add(UserProfile(id=generate_id(), user_id=user.id,
                                       username=name.lower().replace(' ', ''),
                                       display_name=name))
            db.session.commit()
            return user

        author = make_user('author@t.local', 'Elena V')
        reader = make_user('reader@t.local', 'Marcus B')
        admin = make_user('admin@t.local', 'Root Admin', admin=True)
        rookie = make_user('rookie@t.local', 'New Joiner', age_days=0)

        client = app.test_client()

        def login(user):
            client.post('/api/auth/logout')
            res = client.post('/api/auth/login',
                              json={'email': user.email, 'password': 'pw12345678'})
            assert res.status_code == 200, (user.email, res.status_code, res.get_json())

        # ── The renderer: escape first, then a fixed tag set ────────────────
        section('Markdown subset (blog_markdown)')
        check('headings render one level down',
              blog_markdown.render('# Hi') == '<h2>Hi</h2>')
        check('bold, italic and lists render',
              '<strong>a</strong>' in blog_markdown.render('**a**')
              and '<li>x</li>' in blog_markdown.render('- x'))
        check('raw HTML is inert, not stripped',
              blog_markdown.render('<script>alert(1)</script>')
              == '<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>',
              blog_markdown.render('<script>alert(1)</script>'))
        check('javascript: links stay literal text',
              'href' not in blog_markdown.render('[x](javascript:alert(1))'),
              blog_markdown.render('[x](javascript:alert(1))'))
        check('data: links stay literal text',
              'href' not in blog_markdown.render('[x](data:text/html,<b>)'))
        check('http links become anchors with nofollow',
              'rel="nofollow noopener noreferrer"' in blog_markdown.render('[x](https://a.b)'))
        check('relative links are allowed without target',
              'href="/blog"' in blog_markdown.render('[x](/blog)')
              and 'target' not in blog_markdown.render('[x](/blog)'))
        check('code spans are not re-marked up',
              blog_markdown.render('`**a**`') == '<p><code>**a**</code></p>',
              blog_markdown.render('`**a**`'))
        check('snake_case is not italicised',
              '<em>' not in blog_markdown.render('snake_case_word'))
        check('excerpt strips markup and truncates',
              blog_markdown.excerpt('# T\n\n' + 'word ' * 100, 40).endswith('…'))
        check('a protocol-relative URL is not treated as an internal link',
              'href' not in blog_markdown.render('[a](//evil.com)'),
              blog_markdown.render('[a](//evil.com)'))

        # Escaping input before emitting a fixed tag set means the allowlist is
        # structural, not a blocklist to maintain. Assert that property directly:
        # no payload may yield a tag off the list, an event-handler attribute, or
        # a dangerous scheme in an href.
        allowed_tags = {'p', 'br', 'strong', 'em', 'a', 'code', 'pre', 'ul', 'ol',
                        'li', 'blockquote', 'hr', 'h2', 'h3', 'h4'}
        payloads = [
            '<img src=x onerror=alert(1)>',
            '<svg/onload=alert(1)>',
            '<SCRIPT>alert(1)</SCRIPT>',
            '<iframe src=javascript:alert(1)>',
            '<style>body{background:url(javascript:alert(1))}</style>',
            '<a href="#" onclick="alert(1)">x</a>',
            '[a](javascript:alert(1))',
            '[a](JaVaScRiPt:alert(1))',
            '[a](data:text/html;base64,PHNjcmlwdD4=)',
            '[a](vbscript:msgbox(1))',
            '[a](https://x.com" onmouseover="alert(1))',
            "[a](https://x.com'onmouseover='alert(1))",
            '[x](/blog" onmouseover="alert(1))',
            '`<img src=x onerror=alert(1)>`',
            '```\n<img src=x onerror=alert(1)>\n```',
            '# <img src=x onerror=alert(1)>',
            '> <img src=x onerror=alert(1)>',
            '- <img src=x onerror=alert(1)>',
            '**<img src=x onerror=alert(1)>**',
            '&lt;script&gt;alert(1)&lt;/script&gt;',
            '&#60;script&#62;alert(1)&#60;/script&#62;',
            '<!--[if IE]><script>alert(1)</script><![endif]-->',
        ]
        tag_re = re.compile(r'<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)([^>]*)>')
        handler_re = re.compile(r'\son[a-z]+\s*=', re.I)
        danger_re = re.compile(r'(?:href|src)\s*=\s*"\s*(?:javascript|vbscript|data)\s*:', re.I)

        escapes = []
        for payload in payloads:
            for renderer in (blog_markdown.render, blog_markdown.render_plain):
                html = renderer(payload)
                for match in tag_re.finditer(html):
                    if match.group(1).lower() not in allowed_tags:
                        escapes.append((payload, 'tag <' + match.group(1) + '>'))
                    if handler_re.search(match.group(2)):
                        escapes.append((payload, 'event handler attribute'))
                if danger_re.search(html):
                    escapes.append((payload, 'dangerous scheme in href/src'))
        check('{} XSS payloads yield no tag off the allowlist, no event handler '
              'and no dangerous scheme'.format(len(payloads)), not escapes, escapes[:3])

        # ── Settings: defaults, coercion, validation ────────────────────────
        section('Admin settings (§12)')
        check('posting is on by default', svc.setting('blog_posting_enabled') is True)
        check('page size defaults to 10', svc.setting('blog_feed_page_size') == 10)
        check('every documented knob is exposed',
              {s['key'] for s in svc.all_settings()} == set(svc.SETTINGS_SPEC))
        svc.save_setting('blog_feed_page_size', '2')
        check('page size saves and coerces to int', svc.setting('blog_feed_page_size') == 2)

        for key, bad in (('blog_reply_depth', 5), ('blog_feed_page_size', 999),
                         ('blog_default_month', 'whenever'), ('blog_posts_per_day_limit', -1)):
            try:
                svc.save_setting(key, bad)
                check('{} rejects {!r}'.format(key, bad), False)
            except ValueError:
                check('{} rejects {!r}'.format(key, bad), True)
        try:
            svc.save_setting('not_a_blog_setting', 1)
            check('unknown keys are rejected', False)
        except ValueError:
            check('unknown keys are rejected', True)

        # ── Slugs ───────────────────────────────────────────────────────────
        section('Slugs')
        check('slug is lowercased and hyphenated',
              svc.make_slug('How I Set My Rate Card!') == 'how-i-set-my-rate-card')
        check('tags are normalised, deduped and capped at five',
              svc.normalize_tags('Pricing, Rate Card, pricing, a, b, c, d')
              == 'pricing,rate-card,a,b,c', svc.normalize_tags('Pricing, Rate Card, pricing, a, b, c, d'))

        # ── Authoring ───────────────────────────────────────────────────────
        section('Authoring (§5.1)')
        svc.save_setting('blog_posts_per_day_limit', 0)
        login(author)
        res = client.post('/api/blog/posts', json={
            'title': 'How I set my rate card and doubled my income',
            'body': ('For years I priced from what my old employer paid me.\n\n'
                     '**The max, not the average.** That is the whole lesson.\n\n'
                     '- quote higher\n- expect fewer tire kickers'),
            'tags': 'Pricing, Rate Card, pricing',
            'publish': True,
        })
        check('author can publish', res.status_code == 201, res.get_json())
        post_id = res.get_json()['post']['id']
        post = db.session.get(BlogPost, post_id)
        check('excerpt is derived from the body', bool(post.excerpt), post.excerpt)
        check('body_html is rendered on write', '<strong>' in (post.body_html or ''))
        check('tags are stored normalised', post.tags == 'pricing,rate-card', post.tags)
        check('published_at is stamped', post.published_at is not None)
        check('slug comes from the title',
              post.slug == 'how-i-set-my-rate-card-and-doubled-my-income', post.slug)

        for n in (2, 3):
            client.post('/api/blog/posts', json={
                'title': 'Post number {} about cold email numbers'.format(n),
                'body': 'A body long enough to pass validation for post {}.'.format(n),
                'publish': True})

        res = client.post('/api/blog/posts', json={
            'title': 'A post that carries a cover image',
            'body': 'The cover image is optional and must be an absolute http(s) URL.',
            'cover_image': 'https://cdn.example.com/a.png'})
        check('a valid cover image URL is kept',
              db.session.get(BlogPost, res.get_json()['post']['id']).cover_image
              == 'https://cdn.example.com/a.png')
        res = client.post('/api/blog/posts', json={
            'title': 'A post with a hostile cover image',
            'body': 'A cover image that is not an http(s) URL must be dropped.',
            'cover_image': 'javascript:alert(1)'})
        check('a non-http cover image is dropped, not stored',
              db.session.get(BlogPost, res.get_json()['post']['id']).cover_image is None)

        res = client.post('/api/blog/posts', json={'title': 'x', 'body': 'short'})
        check('a two-character title is refused', res.status_code == 400, res.get_json())
        res = client.post('/api/blog/posts', json={
            'title': 'A title that is fine', 'body': 'too short'})
        check('a one-line body is refused', res.status_code == 400)

        # A duplicate title must not collide on the unique slug.
        res = client.post('/api/blog/posts', json={
            'title': 'Post number 2 about cold email numbers',
            'body': 'Same title as an existing post, so the slug must differ.',
            'publish': True})
        check('a duplicate title gets a suffixed slug',
              res.status_code == 201 and res.get_json()['url'].endswith('-2'),
              res.get_json().get('url'))
        dupe_id = res.get_json()['post']['id']

        # ── Posting gates ───────────────────────────────────────────────────
        section('Posting gates (§§11-12)')
        svc.save_setting('blog_posts_per_day_limit', 4)
        res = client.post('/api/blog/posts', json={
            'title': 'One post past the daily limit',
            'body': 'This should be refused by the publishes-per-day limit.',
            'publish': True})
        check('posts/day limit blocks a publish', res.status_code == 403, res.get_json())

        res = client.post('/api/blog/posts', json={
            'title': 'A draft that is still allowed',
            'body': 'Drafts are not subject to the publish-per-day limit.',
            'publish': False})
        check('a draft still saves past the limit', res.status_code == 201, res.get_json())
        draft_id = res.get_json()['post']['id']
        svc.save_setting('blog_posts_per_day_limit', 0)

        svc.save_setting('blog_min_account_age_days', 7)
        login(rookie)
        res = client.post('/api/blog/posts', json={
            'title': 'A brand new account tries to post',
            'body': 'The minimum account age should stop this one.',
            'publish': True})
        check('min account age blocks a new account', res.status_code == 403, res.get_json())
        svc.save_setting('blog_min_account_age_days', 0)

        svc.save_setting('blog_posting_enabled', False)
        res = client.post('/api/blog/posts', json={
            'title': 'Posting is switched off right now',
            'body': 'The master switch should stop this publish.',
            'publish': True})
        check('the master switch stops publishing', res.status_code == 403, res.get_json())
        login(admin)
        res = client.post('/api/blog/posts', json={
            'title': 'An admin bypasses the master switch',
            'body': 'Moderators must never be locked out of their own product.',
            'publish': True})
        check('an admin bypasses the master switch', res.status_code == 201, res.get_json())
        admin_post_id = res.get_json()['post']['id']
        svc.save_setting('blog_posting_enabled', True)

        svc.save_setting('blog_profanity_filter', True)
        svc.save_setting('blog_profanity_words', 'badword, worse')
        login(author)
        res = client.post('/api/blog/posts', json={
            'title': 'A perfectly reasonable title',
            'body': 'This body contains a BADWORD, so it should be refused.'})
        check('the profanity filter blocks a post', res.status_code == 400, res.get_json())
        res = client.post('/api/blog/posts', json={
            'title': 'A perfectly reasonable title',
            'body': 'This body says badwordy, which is a different word entirely.'})
        check('the filter matches whole words only', res.status_code == 201, res.get_json())
        svc.save_setting('blog_profanity_filter', False)

        # ── The calendar ────────────────────────────────────────────────────
        section('The calendar (§6)')
        month = '{:04d}-{:02d}'.format(now.year, now.month)
        res = client.get('/api/blog/calendar?month=' + month)
        cal = res.get_json()
        check('calendar renders for the month', res.status_code == 200)
        published_today = db.session.query(BlogPost).filter(
            BlogPost.status == 'published').count()
        check('per-day counts sum to the published total',
              cal['total'] == published_today, (cal['total'], published_today))
        today_cell = [d for d in cal['days'] if d['is_today']][0]
        check('today carries a count badge', today_cell['count'] == published_today, today_cell)
        check('each day has an ARIA label like "September 5, 2 posts"',
              today_cell['label'].endswith('posts') or today_cell['label'].endswith('post'),
              today_cell['label'])
        check('lead blanks put the 1st under the right weekday',
              cal['lead_blanks'] == (datetime(now.year, now.month, 1).weekday() + 1) % 7)
        check('there is no next-month link past the current month',
              cal['next_month'] is None, cal['next_month'])
        check('previous month is navigable', bool(cal['prev_month']))
        check('a bad month falls back instead of erroring',
              client.get('/api/blog/calendar?month=garbage').status_code == 200)

        # The count on a day and the feed for that day must agree.
        day_iso = today_cell['date']
        feed_for_day = client.get('/api/blog/posts?date=' + day_iso).get_json()
        svc.save_setting('blog_feed_page_size', 50)
        feed_for_day = client.get('/api/blog/posts?date=' + day_iso).get_json()
        check('the calendar badge matches the day filter',
              len(feed_for_day['posts']) == today_cell['count'],
              (len(feed_for_day['posts']), today_cell['count']))
        svc.save_setting('blog_feed_page_size', 2)

        # ── The feed ────────────────────────────────────────────────────────
        section('The feed (§§5.1, 9)')
        res = client.get('/api/blog/posts')
        page1 = res.get_json()
        check('the first page honours blog_feed_page_size', len(page1['posts']) == 2,
              len(page1['posts']))
        check('has_more tells the client to offer Load more', page1['has_more'] is True)
        page2 = client.get('/api/blog/posts?page=2').get_json()
        check('page 2 continues without repeating',
              not ({p['id'] for p in page1['posts']} & {p['id'] for p in page2['posts']}))
        check('ordering is newest first',
              page1['posts'][0]['published_at'] >= page1['posts'][1]['published_at'])
        check('the author is hydrated in one batch',
              page1['posts'][0]['author'].get('name') in ('Elena V', 'Root Admin'),
              page1['posts'][0]['author'])
        check('a malformed date is a 400, not a silent full feed',
              client.get('/api/blog/posts?date=not-a-date').status_code == 400)

        svc.save_setting('blog_feed_page_size', 50)
        titles = [p['title'] for p in client.get('/api/blog/posts').get_json()['posts']]
        check('drafts never reach the public feed',
              'A draft that is still allowed' not in titles)

        # ── Likes ───────────────────────────────────────────────────────────
        section('Likes (§5.2)')
        login(reader)
        res = client.post('/api/blog/posts/{}/like'.format(post_id))
        check('a like registers', res.get_json() == {'liked': True, 'like_count': 1},
              res.get_json())
        res = client.post('/api/blog/posts/{}/like'.format(post_id))
        check('liking again removes it', res.get_json() == {'liked': False, 'like_count': 0},
              res.get_json())
        client.post('/api/blog/posts/{}/like'.format(post_id))
        check('only one like row per user per post',
              db.session.query(BlogLike).filter_by(post_id=post_id).count() == 1)
        check('the denormalised count matches the rows',
              db.session.get(BlogPost, post_id).like_count == 1)
        check('a like on a missing post is a 404',
              client.post('/api/blog/posts/nope00000/like').status_code == 404)
        check('a like on a draft is a 404',
              client.post('/api/blog/posts/{}/like'.format(draft_id)).status_code == 404)

        # ── Comments ────────────────────────────────────────────────────────
        section('Comments (§5.3)')
        res = client.post('/api/blog/posts/{}/comments'.format(post_id),
                          json={'body': 'The *max, not the average* line reframed my pricing.'})
        check('a comment posts', res.status_code == 201, res.get_json())
        comment_id = res.get_json()['comment']['id']
        check('the post comment count is maintained', res.get_json()['comment_count'] == 1)
        check('comment markup is rendered server-side',
              '<em>' in res.get_json()['comment']['body_html'])

        res = client.post('/api/blog/posts/{}/comments'.format(post_id),
                          json={'body': 'Did the tire-kickers leave?',
                                'parent_comment_id': comment_id})
        reply_id = res.get_json()['comment']['id']
        check('a reply attaches to its parent',
              db.session.get(BlogComment, reply_id).parent_comment_id == comment_id)

        res = client.post('/api/blog/posts/{}/comments'.format(post_id),
                          json={'body': 'Replying to the reply.',
                                'parent_comment_id': reply_id})
        check('a reply to a reply is re-parented, not rejected (§2 N2)',
              res.status_code == 201
              and db.session.get(BlogComment, res.get_json()['comment']['id']
                                 ).parent_comment_id == comment_id,
              res.get_json())

        svc.save_setting('blog_reply_depth', 0)
        res = client.post('/api/blog/posts/{}/comments'.format(post_id),
                          json={'body': 'Should be refused now.',
                                'parent_comment_id': comment_id})
        check('reply_depth 0 turns replies off', res.status_code == 400, res.get_json())
        svc.save_setting('blog_reply_depth', 1)

        svc.save_setting('blog_comment_rate_limit', 1)
        res = client.post('/api/blog/posts/{}/comments'.format(post_id),
                          json={'body': 'One comment too many.'})
        check('the comment rate limit returns 429', res.status_code == 429,
              (res.status_code, res.get_json()))
        svc.save_setting('blog_comment_rate_limit', 0)

        res = client.post('/api/blog/comments/{}/like'.format(comment_id))
        check('a comment can be liked',
              res.get_json() == {'liked': True, 'like_count': 1}, res.get_json())
        check('one comment-like row per user',
              db.session.query(BlogCommentLike).filter_by(comment_id=comment_id).count() == 1)

        res = client.post('/api/blog/posts/{}/comments'.format(post_id), json={'body': 'x'})
        check('an empty comment is refused', res.status_code == 400)

        # ── Reports ─────────────────────────────────────────────────────────
        section('Reports (§11)')
        res = client.post('/api/blog/reports', json={
            'target_type': 'post', 'target_id': post_id, 'reason': 'spam'})
        check('a report is accepted', res.status_code == 201, res.get_json())
        client.post('/api/blog/reports', json={
            'target_type': 'post', 'target_id': post_id, 'reason': 'spam'})
        check('re-reporting the same item is a no-op',
              db.session.query(BlogReport).count() == 1, db.session.query(BlogReport).count())
        check('the reported post is flagged but still visible',
              db.session.get(BlogPost, post_id).is_flagged is True
              and db.session.get(BlogPost, post_id).status == 'published')
        check('an unknown reason is refused', client.post('/api/blog/reports', json={
            'target_type': 'post', 'target_id': post_id, 'reason': 'nonsense'}).status_code == 400)
        check('a report against nothing is refused', client.post('/api/blog/reports', json={
            'target_type': 'post', 'target_id': 'gone00000', 'reason': 'spam'}).status_code == 400)

        # ── Guests read, guests do not write ────────────────────────────────
        section('Guest access (§3 permission matrix)')
        client.post('/api/auth/logout')
        check('a guest can read the feed', client.get('/api/blog/posts').status_code == 200)
        check('a guest can read a post',
              client.get('/api/blog/posts/{}'.format(post.slug)).status_code == 200)
        check('a guest can read the calendar', client.get('/api/blog/calendar').status_code == 200)
        check('a guest cannot see a draft',
              client.get('/api/blog/posts/{}'.format(
                  db.session.get(BlogPost, draft_id).slug)).status_code == 404)
        for label, response in (
            ('like', client.post('/api/blog/posts/{}/like'.format(post_id))),
            ('comment', client.post('/api/blog/posts/{}/comments'.format(post_id),
                                    json={'body': 'guest comment'})),
            ('publish', client.post('/api/blog/posts',
                                    json={'title': 'Guest post attempt', 'body': 'x' * 40})),
            ('report', client.post('/api/blog/reports', json={
                'target_type': 'post', 'target_id': post_id, 'reason': 'spam'})),
        ):
            check('a guest cannot {}'.format(label),
                  response.status_code in (302, 401), (label, response.status_code))

        # ── Authorship boundaries ───────────────────────────────────────────
        section('Authorship (§3)')
        login(reader)
        check('a stranger cannot edit a post',
              client.put('/api/blog/posts/{}'.format(post_id),
                         json={'title': 'Hijacked title here'}).status_code == 403)
        check('a stranger cannot delete a post',
              client.delete('/api/blog/posts/{}'.format(post_id)).status_code == 403)
        check('a stranger is not offered admin moderation',
              client.get('/api/admin/blog/reports').status_code == 403)
        check('a stranger cannot read blog settings',
              client.get('/api/admin/blog/settings').status_code == 403)

        login(author)
        res = client.put('/api/blog/posts/{}'.format(post_id), json={
            'title': 'How I set my rate card (revised)',
            'body': 'A revised body that is comfortably long enough to validate.'})
        check('an author can edit their own post', res.status_code == 200, res.get_json())
        post = db.session.get(BlogPost, post_id)
        check('editing sets the "edited" marker', post.was_edited is True)
        check('a retitled post is re-slugged',
              post.slug == 'how-i-set-my-rate-card-revised', post.slug)
        check('editing does not unpublish', post.status == 'published')
        check('editing does not move the calendar day',
              post.published_at.date() == now.date())

        res = client.put('/api/blog/posts/{}'.format(post_id), json={'publish': False})
        check('an author can unpublish', db.session.get(BlogPost, post_id).status == 'draft')
        check('unpublishing keeps published_at for a later restore',
              db.session.get(BlogPost, post_id).published_at is not None)
        client.put('/api/blog/posts/{}'.format(post_id), json={'publish': True})
        check('republishing works', db.session.get(BlogPost, post_id).status == 'published')

        res = client.delete('/api/blog/posts/{}'.format(dupe_id))
        check('an author can delete their own post', res.status_code == 200)
        check('deletion is soft, not a row drop',
              db.session.get(BlogPost, dupe_id).status == 'removed')

        # ── Admin moderation ────────────────────────────────────────────────
        section('Admin moderation (§§8, 11)')
        login(admin)
        res = client.get('/api/admin/blog/reports?status=open')
        queue = res.get_json()
        check('the queue lists open reports', len(queue['reports']) == 1, queue)
        check('the queue hydrates the reported content',
              queue['reports'][0]['target']['title'].startswith('How I set'),
              queue['reports'][0]['target'])
        check('the queue names the reporter',
              queue['reports'][0]['reporter'] == 'Marcus B', queue['reports'][0]['reporter'])
        check('stats count the open reports', queue['stats']['open_reports'] == 1,
              queue['stats'])
        report_id = queue['reports'][0]['id']

        res = client.post('/api/admin/blog/posts/{}/remove'.format(post_id), json={})
        check('an admin can remove any post', res.get_json()['status'] == 'removed',
              res.get_json())
        feed_ids = [p['id'] for p in client.get('/api/blog/posts').get_json()['posts']]
        check('a removed post leaves the feed', post_id not in feed_ids)
        cal_after = client.get('/api/blog/calendar?month=' + month).get_json()
        check('a removed post leaves the calendar count',
              cal_after['total'] < cal['total'], (cal_after['total'], cal['total']))

        res = client.post('/api/admin/blog/posts/{}/remove'.format(post_id),
                          json={'restore': True})
        check('an admin can restore a post', res.get_json()['status'] == 'published',
              res.get_json())
        check('restoring clears the flag', db.session.get(BlogPost, post_id).is_flagged is False)

        res = client.patch('/api/admin/blog/reports/{}'.format(report_id),
                           json={'status': 'dismissed', 'admin_note': 'Reads fine to me.'})
        check('a report can be dismissed',
              res.get_json()['report']['status'] == 'dismissed', res.get_json())
        check('dismissing clears it from the open count',
              res.get_json()['stats']['open_reports'] == 0)
        check('an unknown status is refused',
              client.patch('/api/admin/blog/reports/{}'.format(report_id),
                           json={'status': 'whatever'}).status_code == 400)

        before = db.session.get(BlogPost, post_id).comment_count
        res = client.post('/api/admin/blog/comments/{}/remove'.format(comment_id), json={})
        check('an admin can remove a comment', res.status_code == 200, res.get_json())
        removed = db.session.get(BlogComment, comment_id)
        check('comment removal is soft', removed.is_removed is True and removed.body is not None)
        check('the comment count drops by one',
              db.session.get(BlogPost, post_id).comment_count == before - 1,
              (before, db.session.get(BlogPost, post_id).comment_count))
        thread = svc.comment_thread(db.session.get(BlogPost, post_id))
        check('the tombstone keeps its place in the thread',
              any(node['comment'].id == comment_id for node in thread))
        check('the replies under a tombstone survive',
              any(r.id == reply_id for node in thread for r in node['replies']))

        res = client.get('/api/admin/blog/posts?status=removed')
        check('an admin can list removed posts',
              all(p['status'] == 'removed' for p in res.get_json()['posts'])
              and res.get_json()['posts'], res.get_json())
        res = client.get('/api/admin/blog/posts?q=rate card')
        check('an admin can search titles', len(res.get_json()['posts']) >= 1, res.get_json())

        res = client.put('/api/admin/blog/settings',
                         json={'settings': {'blog_feed_page_size': 7,
                                            'blog_posting_enabled': False}})
        check('an admin can save settings over the API',
              res.status_code == 200 and svc.setting('blog_feed_page_size') == 7
              and svc.setting('blog_posting_enabled') is False, res.get_json())
        svc.save_setting('blog_posting_enabled', True)
        res = client.put('/api/admin/blog/settings',
                         json={'settings': {'blog_feed_page_size': 999}})
        check('a bad setting is refused with a readable message',
              res.status_code == 400 and 'between 1 and 50' in res.get_json()['error'],
              res.get_json())

        # ── Caching ─────────────────────────────────────────────────────────
        section('Caching (§9)')
        svc.save_setting('blog_feed_page_size', 50)
        before_count = len(client.get('/api/blog/posts').get_json()['posts'])
        login(author)
        client.post('/api/blog/posts', json={
            'title': 'A post published after the cache was warm',
            'body': 'Publishing must invalidate the memoized feed and calendar.',
            'publish': True})
        after = client.get('/api/blog/posts').get_json()['posts']
        check('publishing invalidates the cached feed',
              len(after) == before_count + 1, (before_count, len(after)))
        check('the new post is at the top of the feed',
              after[0]['title'] == 'A post published after the cache was warm',
              after[0]['title'])
        cal_now = client.get('/api/blog/calendar?month=' + month).get_json()
        check('publishing invalidates the cached calendar',
              cal_now['total'] == len(after), (cal_now['total'], len(after)))

        # ── Preview ─────────────────────────────────────────────────────────
        section('Editor preview')
        res = client.post('/api/blog/preview', json={'body': '# Hi\n\n<script>x</script>'})
        check('preview uses the same renderer as a saved post',
              '<h2>Hi</h2>' in res.get_json()['html']
              and '&lt;script&gt;' in res.get_json()['html'], res.get_json())
        client.post('/api/auth/logout')
        check('preview needs a login',
              client.post('/api/blog/preview', json={'body': 'x'}).status_code in (302, 401))

        # ── HTML pages ──────────────────────────────────────────────────────
        section('Pages')
        live = db.session.get(BlogPost, post_id)
        for url, expect in (
            ('/blog', 200),
            ('/blog?date=' + now.date().isoformat(), 200),
            ('/blog?month=' + month, 200),
            ('/blog?month=garbage', 200),
            ('/blog/' + live.slug, 200),
            ('/blog/no-such-post-exists', 404),
        ):
            res = client.get(url)
            check('guest GET {} -> {}'.format(url, expect), res.status_code == expect,
                  res.status_code)

        body = client.get('/blog').get_data(as_text=True)
        check('the calendar renders day badges', 'bl-badge' in body)
        check('a guest sees the log-in gate rather than the composer',
              'Log in to post' in body, body[:0])
        check('the calendar grid is keyboard reachable', 'aria-pressed' in body)

        post_body = client.get('/blog/' + live.slug).get_data(as_text=True)
        check('the post page renders the rendered body', 'bp-body' in post_body)
        check('a guest is told to log in to like or comment',
              'log in</a>' in post_body, )
        check('a tombstone renders for a removed comment',
              'comment removed' in post_body)

        login(author)
        check('the author reaches the editor', client.get('/blog/new').status_code == 200)
        check('the author reaches their own edit page',
              client.get('/blog/{}/edit'.format(live.slug)).status_code == 200)
        login(reader)
        check('a stranger cannot open the edit page',
              client.get('/blog/{}/edit'.format(live.slug)).status_code == 403)
        check('a stranger cannot open admin moderation',
              client.get('/admin/blog').status_code == 403)
        login(admin)
        check('an admin reaches the moderation page',
              client.get('/admin/blog').status_code == 200)
        admin_body = client.get('/admin/blog').get_data(as_text=True)
        check('every threshold is editable in the admin panel',
              all(k in admin_body for k in svc.SETTINGS_SPEC))

        # ── Navigation and the footer link ──────────────────────────────────
        section('Navigation')
        svc.save_setting('blog_nav_min_posts', 0)
        check('the nav link shows when the threshold is 0', svc.nav_visible() is True)
        svc.save_setting('blog_nav_min_posts', 9999)
        check('the nav link hides below the threshold', svc.nav_visible() is False)
        client.post('/api/auth/logout')
        home = client.get('/').get_data(as_text=True)
        check('the home page footer links to the blog regardless of the threshold',
              'href="/blog"' in home)
        svc.save_setting('blog_nav_min_posts', 0)
        home = client.get('/').get_data(as_text=True)
        check('the home page top nav links to the blog above the threshold',
              home.count('href="/blog"') >= 2, home.count('href="/blog"'))

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        print('failed: ' + ', '.join(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
