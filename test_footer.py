"""The public footer — one copy, rendered on every external page.

Checks the thing that actually breaks: that each external URL really emits the
shared footer's markup, that the landing page and the external pages emit the
same thing, and that signed-in app pages still show the compact bar instead.

Against in-memory SQLite; no network.

    python test_footer.py
"""
import io
import os
import sys
from datetime import datetime

os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []

MARKER = 'class="pf"'          # the shared footer's own root class
COMPACT = 'class="footer"'     # base.html's signed-in bar


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
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)

    with app.app_context():
        from app.models.user import User
        from app.models.simulation import Simulation
        db.create_all()
        now = datetime.utcnow()
        user = User(id=generate_id(), email='owner@t.local', full_name='Owner',
                    password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                    email_verified=True, onboarding_completed_at=now)
        db.session.add(user)
        db.session.commit()
        sim = Simulation(id=generate_id(), user_id=user.id, name='S',
                         expertise_zone='Consulting', status='complete')
        db.session.add(sim)
        db.session.commit()
        sim_id = sim.id

    client = app.test_client()

    # ── The partial is the single source ────────────────────────────────────
    section('One footer, one file')
    partial = io.open('app/templates/partials/_footer_public.html',
                      encoding='utf-8').read()
    check('the partial exists and defines the shared root class', MARKER in partial)

    landing_tpl = io.open('app/templates/landing.html', encoding='utf-8').read()
    check('landing.html includes the partial rather than its own copy',
          "partials/_footer_public.html" in landing_tpl
          and '<footer>' not in landing_tpl)
    check('landing.html no longer carries the dead footer CSS',
          '.footer-grid' not in landing_tpl and '.footer-copy' not in landing_tpl)

    base_tpl = io.open('app/templates/base.html', encoding='utf-8').read()
    check('base.html exposes the footer as an overridable block',
          '{% block footer %}' in base_tpl)

    # ── Every external page renders it ──────────────────────────────────────
    section('External pages')
    external = [
        ('/',                          'landing'),
        ('/legal/terms',               'terms'),
        ('/legal/privacy',             'privacy'),
        ('/careers',                   'careers index'),
        ('/blog',                      'journal index'),
        ('/login',                     'sign in'),
        ('/register',                  'register'),
        ('/verify-sent',               'verify sent'),
        ('/forgot-password',           'forgot password'),
        ('/reset-password/no-such-token', 'choose new password'),
        ('/partners/apply',            'partner apply'),
        ('/samples/maya',              'use case: Maya'),
        ('/samples/marcus',            'use case: Marcus'),
        ('/explore',                   'explore'),
        ('/help/simi',                 'about Simi'),
        ('/simulations/{}/share-roi'.format(sim_id), 'share ROI'),
    ]
    bodies = {}
    for url, name in external:
        res = client.get(url)
        body = res.get_data(as_text=True)
        bodies[url] = body
        ok = res.status_code == 200 and MARKER in body
        check('{:<18} {}'.format(name, url), ok,
              'status {}{}'.format(res.status_code,
                                   '' if MARKER in body else ', footer missing'))

    # ── It is byte-identical to the landing page's ──────────────────────────
    section('Parity with the landing page')

    def footer_of(body):
        i = body.rfind('<footer class="pf">')
        j = body.find('</footer>', i)
        return body[i:j] if i != -1 and j != -1 else ''

    landing_footer = footer_of(bodies['/'])
    check('the landing footer is non-trivial', len(landing_footer) > 400,
          len(landing_footer))
    for url in ('/legal/terms', '/careers', '/blog', '/login', '/explore',
                '/help/simi', '/samples/maya'):
        check('identical footer on {}'.format(url),
              footer_of(bodies[url]) == landing_footer)

    check('every footer link resolves (no url_for left unrendered)',
          'url_for' not in landing_footer and '{{' not in landing_footer)
    # playbook_url used to be passed by the landing route alone; a context
    # processor now supplies it wherever the footer renders.
    check('the Playbook link renders off the landing route too',
          'amazon.com' in footer_of(bodies['/careers']))
    for label in ('Product', 'Company', 'Legal', 'The Journal', 'Careers',
                  'Partners', 'Privacy', 'Terms', 'Explore'):
        check('footer carries "{}"'.format(label), label in landing_footer)

    section('Every external template is wired up')
    expected = [
        'samples/simulacrum_usecase_marcus.html',
        'samples/simulacrum_usecase_maya.html',
        'auth/login.html',
        'auth/register.html',
        'auth/verify_sent.html',
        'auth/verify_email.html',
        'auth/reset_password.html',
        'auth/reset_password_confirm.html',
        'public/unsubscribe.html',
        'public/explore.html',
        'public/simi_help.html',
        'public/bio_teaser.html',
        'public/profile.html',
        'public/profile_unpublished.html',
        'partners/apply.html',
        'legal/terms.html',
        'legal/privacy.html',
        'careers/index.html',
        'careers/detail.html',
        'blog/index.html',
        'blog/post.html',
        'corporate/invite.html',
        'simulations/share_roi.html',
        'simulations/layer6_share.html',
        'landing.html',
    ]
    missing = []
    for rel in expected:
        body = io.open('app/templates/' + rel, encoding='utf-8').read()
        if 'partials/_footer_public.html' not in body:
            missing.append(rel)
    check('all {} external templates reference the shared footer'.format(len(expected)),
          not missing, 'missing: {}'.format(missing))

    # And nothing internal picked it up by accident.
    import os as _os
    leaked = []
    for root, dirs, files in _os.walk('app/templates'):
        dirs[:] = [d for d in dirs if d != 'partials']
        for f in files:
            if not f.endswith('.html'):
                continue
            rel = _os.path.relpath(_os.path.join(root, f), 'app/templates')
            rel = rel.replace('\\', '/')
            if rel in expected:
                continue
            if 'partials/_footer_public.html' in io.open(
                    _os.path.join(root, f), encoding='utf-8').read():
                leaked.append(rel)
    check('no signed-in template picked up the public footer', not leaked,
          'leaked: {}'.format(leaked))

    # ── Signed-in app pages keep the compact bar ───────────────────────────
    section('Signed-in app pages are untouched')
    client.post('/api/auth/login', json={'email': 'owner@t.local',
                                         'password': 'pw12345678'})
    for url, name in [('/dashboard', 'dashboard'), ('/settings/profile', 'settings')]:
        res = client.get(url)
        body = res.get_data(as_text=True)
        check('{:<10} keeps the compact footer'.format(name),
              res.status_code == 200 and COMPACT in body and MARKER not in body,
              'status {}, compact={}, public={}'.format(
                  res.status_code, COMPACT in body, MARKER in body))

    # A signed-in visitor on an external page still gets the public footer.
    res = client.get('/blog')
    check('an external page shows the public footer even when signed in',
          MARKER in res.get_data(as_text=True))

    # ── The private-bio access-request teaser ───────────────────────────────
    section('Footer on the "Request access with LinkedIn" page')
    from app.models.bio_page import BioPage
    from app.models.profile import UserProfile
    from app.models.user import User as _User
    with app.app_context():
        owner = _User(id=generate_id(), email='private@t.local', full_name='Private Owner',
                      password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                      email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add(owner)
        db.session.commit()
        db.session.add(UserProfile(id=generate_id(), user_id=owner.id, username='privateowner',
                                   display_name='Private Owner', privacy_mode='private'))
        db.session.add(BioPage(id=generate_id(), user_id=owner.id, slug='privateowner',
                               status='published', published_at=datetime.utcnow()))
        db.session.commit()

    client.post('/api/auth/logout')
    res = client.get('/u/privateowner')
    teaser = res.get_data(as_text=True)
    check('a stranger on a private bio gets the access-request teaser',
          res.status_code == 200 and 'Request access with LinkedIn' in teaser,
          res.status_code)
    def footer_of(html):
        start = html.index('<footer class="pf">')
        return html[start:html.index('</footer>', start)]

    landing = client.get('/').get_data(as_text=True)
    check('the teaser footer is the landing page footer, item for item',
          footer_of(teaser) == footer_of(landing))
    # The body is a flex container; in a row the footer sat beside the card.
    check('the teaser lays the page out as a column, so the footer sits below the card',
          'flex-direction:column' in teaser
          and teaser.index('class="card"') < teaser.index(MARKER))
    check('the teaser footer spans the full width', 'body > .pf{align-self:stretch;}' in teaser)

    # ── The user-owned bio footer is deliberately left alone ───────────────
    section('User-owned surfaces left alone')
    bio_tpl = io.open('app/templates/public/bio_page.html', encoding='utf-8').read()
    check('the bio page keeps its own footer (member links + PLG badge)',
          '<footer id="footer">' in bio_tpl
          and 'partials/_footer_public.html' not in bio_tpl)
    check('and its co-branding opt-out still governs the badge',
          'bio_page.show_badge' in bio_tpl)

    help_tpl = io.open('app/templates/public/simi_help.html', encoding='utf-8').read()
    check('the Simi help page keeps its AI disclaimer above the footer',
          'sh-footer' in help_tpl and 'partials/_footer_public.html' in help_tpl)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
