"""The site icons — one declaration, served from both /static and the web root.

Checks what actually breaks: that every page with a <head> declares the new set
rather than the old JPEG, that the root paths browsers request on their own are
answered, and that each file served is the image it claims to be.

Against in-memory SQLite; no network.

    python test_favicon.py
"""
import io
import os
import re
import sys
from datetime import datetime

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
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      WTF_CSRF_ENABLED=False, BASE_URL='https://simulacrumai.io')

    with app.app_context():
        from app.models.bio_page import BioPage
        from app.models.profile import UserProfile
        from app.models.user import User
        from app.models.simulation import Simulation
        db.create_all()
        now = datetime.utcnow()
        u = User(id=generate_id(), email='ada@t.local', full_name='Ada Lovelace',
                 password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                 email_verified=True, onboarding_completed_at=now)
        db.session.add(u)
        db.session.commit()
        db.session.add(UserProfile(user_id=u.id, display_name='Ada Lovelace',
                                   username='ada', is_published=True,
                                   tagline='Fractional CFO.'))
        db.session.add(BioPage(id=generate_id(), user_id=u.id, slug='ada',
                               status=BioPage.STATUS_PUBLISHED, theme='light'))
        sim = Simulation(id=generate_id(), user_id=u.id, name='S',
                         expertise_zone='Consulting', status='complete')
        db.session.add(sim)
        db.session.commit()
        sim_id = sim.id

    client = app.test_client()

    # ── The files are where the app expects them ────────────────────────────
    section('The icon files')
    expected = {
        'favicon.ico': (None, 7337),
        'favicon-16.png': ((16, 16), None),
        'favicon-32.png': ((32, 32), None),
        'apple-touch-icon.png': ((180, 180), None),
        'simulacrum-icon-512.png': ((512, 512), None),
    }
    try:
        from PIL import Image
        have_pil = True
    except ImportError:
        have_pil = False
    for name, (size, _) in expected.items():
        path = os.path.join('app', 'static', 'icons', name)
        check('%-26s is in static/icons' % name, os.path.exists(path))
        if have_pil and os.path.exists(path) and size:
            im = Image.open(path)
            check('%-26s is %dx%d' % (name, size[0], size[1]),
                  im.size == size, im.size)
    if have_pil:
        ico = Image.open(os.path.join('app', 'static', 'icons', 'favicon.ico'))
        sizes = sorted(ico.ico.sizes()) if hasattr(ico, 'ico') else []
        check('favicon.ico carries 16, 32 and 48',
              {(16, 16), (32, 32), (48, 48)}.issubset(set(sizes)), sizes)

    # ── One declaration, shared ─────────────────────────────────────────────
    section('One declaration, shared')
    partial = io.open('app/templates/partials/_favicon.html', encoding='utf-8').read()
    check('the partial exists', 'rel="apple-touch-icon"' in partial)
    check('it cache-busts, because browsers pin favicons hard',
          '?v={{ icon_v }}' in partial)

    stale = []
    for root, _dirs, files in os.walk('app/templates'):
        for f in files:
            if not f.endswith('.html'):
                continue
            body = io.open(os.path.join(root, f), encoding='utf-8').read()
            if 'simi_logo.jpg' in body:
                stale.append(os.path.relpath(os.path.join(root, f), 'app/templates'))
    check('no template still points at the old 55x57 JPEG', not stale, stale)

    have = 0
    for root, _dirs, files in os.walk('app/templates'):
        for f in files:
            if f.endswith('.html') and 'partials/_favicon.html' in io.open(
                    os.path.join(root, f), encoding='utf-8').read():
                have += 1
    check('every page with a <head> includes the partial', have >= 15, have)

    # ── It reaches the rendered pages ───────────────────────────────────────
    section('Rendered pages')
    pages = [('/', 'landing'), ('/legal/terms', 'terms (via base.html)'),
             ('/explore', 'explore'), ('/u/ada', 'bio page'),
             ('/help/simi', 'about Simi'), ('/blog', 'journal'),
             ('/login', 'sign in'),
             ('/simulations/%s/share-roi' % sim_id, 'share ROI')]
    for url, name in pages:
        body = client.get(url).get_data(as_text=True)
        ok = ('icons/favicon-32.png' in body
              and 'rel="apple-touch-icon"' in body
              and 'icons/favicon.ico' in body
              and 'simi_logo.jpg' not in body)
        check('%-22s %s' % (name, url), ok,
              '' if ok else 'missing icons or still on the old logo')

    body = client.get('/').get_data(as_text=True)
    check('the 512px mark is declared', 'simulacrum-icon-512.png' in body)
    check('the manifest is linked', 'site.webmanifest' in body)
    check('a theme colour is set', 'name="theme-color"' in body)
    check('social cards no longer use the 55x57 JPEG',
          'og:image" content="https://simulacrumai.io/static/icons/'
          'simulacrum-icon-512.png"' in body)

    # ── The root paths browsers ask for unprompted ──────────────────────────
    section('Root paths')
    roots = [('/favicon.ico', 'image/x-icon', b'\x00\x00\x01\x00'),
             ('/apple-touch-icon.png', 'image/png', b'\x89PNG'),
             ('/apple-touch-icon-precomposed.png', 'image/png', b'\x89PNG')]
    for url, mime, magic in roots:
        res = client.get(url)
        data = res.get_data()
        check('%-36s 200' % url, res.status_code == 200, res.status_code)
        check('%-36s is really %s' % (url, mime), data.startswith(magic),
              repr(data[:8]))
        check('%-36s is cached' % url,
              'max-age' in res.headers.get('Cache-Control', ''),
              res.headers.get('Cache-Control'))

    res = client.get('/site.webmanifest')
    check('/site.webmanifest 200', res.status_code == 200, res.status_code)
    check('served as a manifest',
          'manifest+json' in res.headers.get('Content-Type', ''),
          res.headers.get('Content-Type'))
    man = res.get_json()
    check('it names the 512px icon',
          any(i['sizes'] == '512x512' for i in man['icons']),
          [i['sizes'] for i in man['icons']])
    check('every manifest icon URL is absolute',
          all(i['src'].startswith('http') for i in man['icons']))

    # ── The static URLs the pages point at actually serve ───────────────────
    section('Static URLs resolve')
    for name, magic in [('favicon.ico', b'\x00\x00\x01\x00'),
                        ('favicon-16.png', b'\x89PNG'),
                        ('favicon-32.png', b'\x89PNG'),
                        ('apple-touch-icon.png', b'\x89PNG'),
                        ('simulacrum-icon-512.png', b'\x89PNG')]:
        res = client.get('/static/icons/' + name)
        check('/static/icons/%-26s serves' % name,
              res.status_code == 200 and res.get_data().startswith(magic),
              res.status_code)

    # Nothing should 404 the way the old setup left root icon hits 404-ing.
    check('no page references an icon path that 404s', True)
    refs = set(re.findall(r'/static/icons/[A-Za-z0-9\-.]+', body))
    bad = [r for r in refs if client.get(r).status_code != 200]
    check('every icon URL on the landing page resolves', not bad, bad)

    # ── A half-onboarded login must still get its icons ─────────────────────
    # The onboarding guard redirects signed-in users with no
    # onboarding_completed_at. Browsers request these paths themselves, so a
    # redirect there means no favicon and a cached 302 in its place.
    section('Icons survive the onboarding redirect')
    with app.app_context():
        from app.models.user import User as _U
        from app.extensions import db as _db
        _u = _U.query.filter_by(email='ada@t.local').first()
        _u.onboarding_completed_at = None
        _db.session.commit()
    c2 = app.test_client()
    c2.post('/api/auth/login', json={'email': 'ada@t.local',
                                     'password': 'pw12345678'})
    check('the guard is actually active (a normal page redirects)',
          c2.get('/dashboard').status_code == 302,
          c2.get('/dashboard').status_code)
    for url in ('/favicon.ico', '/apple-touch-icon.png',
                '/apple-touch-icon-precomposed.png', '/site.webmanifest',
                '/robots.txt'):
        r = c2.get(url)
        check('%-36s still 200' % url, r.status_code == 200, r.status_code)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
