"""Search optimisation for the public bio URLs at /u/<slug>.

The claim worth testing is that one gate decides indexability: the page's robots
tag and the sitemap's contents are read from the same function, so a URL is
never advertised in the sitemap while telling the crawler to go away — and a
private profile is never advertised at all.

Also checks the title pattern, the document structure (one h1, a main landmark,
a breadcrumb) and that a bio cross-links back to the directory that links in.

Against in-memory SQLite; no network.

    python test_bio_seo.py
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
    # Canonicals and sitemap entries come from BASE_URL, not the request host,
    # so they stay stable however a visitor arrived. Pinned here so the
    # assertions below describe production shapes rather than test-client ones.
    ORIGIN = 'https://simisi.io'
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      WTF_CSRF_ENABLED=False, BASE_URL=ORIGIN)

    with app.app_context():
        from app.models.bio_page import BioPage
        from app.models.profile import UserProfile
        from app.models.user import User
        from app.services import bio_seo

        db.create_all()
        now = datetime.utcnow()

        def make(email, name, username, status=BioPage.STATUS_PUBLISHED,
                 privacy='public', noindex=False, page_noindex=False):
            u = User(id=generate_id(), email=email, full_name=name,
                     password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                     email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            prof = UserProfile(user_id=u.id, display_name=name, username=username,
                               is_published=True, noindex=noindex,
                               tagline='Fractional CFO for seed-stage teams.')
            prof.privacy_mode = privacy
            db.session.add(prof)
            bp = BioPage(id=generate_id(), user_id=u.id, slug=username,
                         status=status, theme='light')
            if page_noindex:
                bp.sections = dict(bp.sections, visibility={'noindex': True})
            db.session.add(bp)
            db.session.commit()
            return u, prof, bp

        public_u, public_p, public_bp = make(
            'ada@t.local', 'Ada Kingsley Lovelace', 'ada-lovelace')
        private_u, private_p, private_bp = make(
            'grace@t.local', 'Grace Hopper', 'grace-hopper', privacy='private')
        draft_u, draft_p, draft_bp = make(
            'alan@t.local', 'Alan Turing', 'alan-turing',
            status=BioPage.STATUS_DRAFT)
        noidx_u, noidx_p, noidx_bp = make(
            'edsger@t.local', 'Edsger Dijkstra', 'edsger-dijkstra', noindex=True)
        pgnoidx_u, pgnoidx_p, pgnoidx_bp = make(
            'barbara@t.local', 'Barbara Liskov', 'barbara-liskov',
            page_noindex=True)

        # ── The title pattern ───────────────────────────────────────────────
        section('Title pattern')
        title = bio_seo.page_title('Ada Kingsley Lovelace')
        check('first and last name only, middle names dropped',
              title.startswith('Ada Lovelace |'), title)
        check('carries the bio qualifier',
              'Professional AI Agent Simulation Bio' in title)
        check('carries the product qualifier',
              'AI agent career builder' in title)
        check('exactly the requested shape',
              title == 'Ada Lovelace | Professional AI Agent Simulation Bio '
                       '| AI agent career builder', title)
        check('a one-word name renders without a dangling separator',
              bio_seo.page_title('Prince') ==
              'Prince | Professional AI Agent Simulation Bio '
              '| AI agent career builder')
        check('an empty name still yields a usable title',
              bio_seo.page_title('').startswith('Professional |'),
              bio_seo.page_title(''))

        check('the member\'s own words become the description',
              bio_seo.meta_description('Ada Lovelace', 'CFO',
                                       'Fractional CFO for seed-stage teams.')
              == 'Fractional CFO for seed-stage teams.')
        check('a blank positioning falls back to a composed sentence',
              'Ada Lovelace is a CFO.' in
              bio_seo.meta_description('Ada Lovelace', 'CFO', '', ''))
        check('the description is capped for the SERP',
              len(bio_seo.meta_description('Ada Lovelace', '', 'x' * 400)) <= 160)

        # ── One indexability gate ───────────────────────────────────────────
        section('One indexability gate')
        check('a published public bio is indexable',
              bio_seo.is_indexable(public_p, public_bp))
        check('a private profile is not', not bio_seo.is_indexable(private_p, private_bp))
        check('a draft is not', not bio_seo.is_indexable(draft_p, draft_bp))
        check('a profile-level noindex is honoured',
              not bio_seo.is_indexable(noidx_p, noidx_bp))
        check('a page-level noindex is honoured',
              not bio_seo.is_indexable(pgnoidx_p, pgnoidx_bp))
        check('no bio page at all is not indexable',
              not bio_seo.is_indexable(public_p, None))

        listed = {b['slug'] for b in bio_seo.indexable_bios()}
        check('only the public bio is offered to the sitemap',
              listed == {'ada-lovelace'}, sorted(listed))

        # A deleted owner drops out without any other change.
        public_u.deleted_at = now
        db.session.commit()
        check('a deleted owner drops out of the sitemap',
              not bio_seo.indexable_bios())
        public_u.deleted_at = None
        db.session.commit()

    client = app.test_client()

    # ── The rendered page ───────────────────────────────────────────────────
    section('The rendered bio page')
    res = client.get('/u/ada-lovelace')
    body = res.get_data(as_text=True)
    check('/u/<slug> renders', res.status_code == 200, res.status_code)

    m = re.search(r'<title>(.*?)</title>', body, re.S)
    check('the page title uses the pattern',
          m and m.group(1).strip() ==
          'Ada Lovelace | Professional AI Agent Simulation Bio '
          '| AI agent career builder',
          m.group(1).strip() if m else 'no title')

    check('it declares itself indexable',
          'name="robots" content="index, follow, max-image-preview:large"' in body)
    check('it carries a self-referential canonical on the configured origin',
          ('rel="canonical" href="%s/u/ada-lovelace"' % ORIGIN) in body,
          re.search(r'rel="canonical" href="([^"]+)"', body).group(1))
    check('og:title matches the page title',
          'property="og:title" content="Ada Lovelace | Professional' in body)
    check('og:site_name is set', 'property="og:site_name"' in body)
    check('the profile name is split for og',
          'property="profile:first_name" content="Ada"' in body
          and 'property="profile:last_name" content="Lovelace"' in body)
    check('the description is not the raw 160-char slice',
          'content="Fractional CFO for seed-stage teams."' in body)

    # ── Document structure ──────────────────────────────────────────────────
    section('Clean HTML structure')
    check('exactly one h1', body.count('<h1>') == 1, body.count('<h1>'))
    check('the h1 is the member, not the product',
          re.search(r'<h1>\s*Ada Kingsley Lovelace\s*</h1>', body) is not None)
    check('a main landmark wraps the content',
          '<main class="bio-wrap" id="main">' in body and '</main>' in body)
    check('sections sit under h2s, not more h1s',
          body.count('<h2') >= 1 and body.count('<h1') == 1)
    check('the breadcrumb is a nav landmark',
          '<nav class="bc" aria-label="Breadcrumb">' in body)
    check('the current page is marked in the breadcrumb',
          'aria-current="page"' in body)

    ld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
    check('two JSON-LD blocks: the person and the breadcrumb', len(ld) == 2, len(ld))
    import json
    parsed = []
    for blob in ld:
        try:
            parsed.append(json.loads(blob))
        except ValueError as exc:
            check('JSON-LD parses', False, str(exc))
    types = {d.get('@type') for d in parsed}
    check('both JSON-LD blocks are valid JSON', len(parsed) == len(ld))
    check('Person and BreadcrumbList are declared',
          types == {'Person', 'BreadcrumbList'}, sorted(types))
    person = next((d for d in parsed if d.get('@type') == 'Person'), {})
    check('the Person url is the canonical, not the request host',
          person.get('url') == ORIGIN + '/u/ada-lovelace', person.get('url'))
    check('Person names the page it describes',
          person.get('mainEntityOfPage', {}).get('@id', '').endswith('/u/ada-lovelace'),
          person.get('mainEntityOfPage'))
    crumbs = next((d for d in parsed if d.get('@type') == 'BreadcrumbList'), {})
    names = [i['name'] for i in crumbs.get('itemListElement', [])]
    check('the breadcrumb trail runs Home > Professionals > name',
          names == ['Home', 'Professionals', 'Ada Lovelace'], names)

    # ── Cross-links back to the directory ───────────────────────────────────
    section('Cross-links to the directory')
    check('the breadcrumb links to /explore',
          re.search(r'<nav class="bc".*?href="/explore".*?</nav>', body, re.S) is not None)
    check('a directory cross-link block sits above the footer',
          'class="dir-xlink"' in body and 'Professionals directory' in body)
    check('and it links home as well', '>How it works →</a>' in body)

    explore = client.get('/explore').get_data(as_text=True)
    check('the directory links back to the bio, so the link is reciprocal',
          '/u/ada-lovelace' in explore)
    check('the directory canonicalises itself',
          ('rel="canonical" href="%s/explore"' % ORIGIN) in explore)
    filtered = client.get('/explore?category=finance').get_data(as_text=True)
    check('a filtered directory view does not compete for the same query',
          'content="noindex, follow"' in filtered)
    check('and still points its canonical at the unfiltered listing',
          ('rel="canonical" href="%s/explore"' % ORIGIN) in filtered)

    # ── The sitemap agrees with the pages ───────────────────────────────────
    section('The sitemap agrees with the pages')
    res = client.get('/sitemap.xml')
    smap = res.get_data(as_text=True)
    check('/sitemap.xml serves', res.status_code == 200, res.status_code)
    from xml.dom.minidom import parseString as _parse_xml
    try:
        _parse_xml(smap)
        check('it is well-formed XML', True)
    except Exception as exc:
        check('it is well-formed XML', False, str(exc))

    check('the curated static entries survive',
          (ORIGIN + '/') in smap and '/careers' in smap)
    # Every URL in a sitemap must sit on the same host as the sitemap itself.
    hosts = set(re.findall(r'<loc>(https?://[^/]+)', smap))
    check('every sitemap URL is on one host', hosts == {ORIGIN}, sorted(hosts))
    check('the directory is listed', '/explore' in smap)
    check('the public bio URL is listed', '/u/ada-lovelace' in smap)
    check('it carries a lastmod for the bio',
          re.search(r'/u/ada-lovelace</loc>\s*<lastmod>\d{4}-\d{2}-\d{2}</lastmod>',
                    smap) is not None)
    for slug, why in [('grace-hopper', 'private'), ('alan-turing', 'draft'),
                      ('edsger-dijkstra', 'profile noindex'),
                      ('barbara-liskov', 'page noindex')]:
        check('a {} bio is withheld from the sitemap'.format(why),
              slug not in smap)

    # The gate really is shared: what the sitemap lists, the page invites.
    listed_slugs = re.findall(r'/u/([a-z0-9\-]+)</loc>', smap)
    mismatched = []
    for slug in listed_slugs:
        page = client.get('/u/' + slug).get_data(as_text=True)
        if 'content="noindex' in page:
            mismatched.append(slug)
    check('nothing in the sitemap serves noindex', not mismatched, mismatched)

    # And the inverse: a withheld page says so itself.
    priv = client.get('/u/grace-hopper').get_data(as_text=True)
    check('the private page serves noindex', 'content="noindex, follow"' in priv)
    check('the private teaser still gets a real title',
          'Grace Hopper | Professional AI Agent Simulation Bio' in priv,
          re.search(r'<title>(.*?)</title>', priv, re.S).group(1).strip())
    draft = client.get('/u/alan-turing').get_data(as_text=True)
    check('the draft page serves noindex', 'content="noindex' in draft)

    # ── robots.txt ──────────────────────────────────────────────────────────
    section('robots.txt')
    res = client.get('/robots.txt')
    txt = res.get_data(as_text=True)
    check('/robots.txt serves', res.status_code == 200, res.status_code)
    check('it is plain text', res.mimetype == 'text/plain', res.mimetype)
    check('it advertises the sitemap',
          ('Sitemap: %s/sitemap.xml' % ORIGIN) in txt)
    check('bio URLs are crawlable', 'Disallow: /u/\n' not in txt)
    check('the bio editor is not', 'Disallow: /u/*/edit' in txt)
    for path in ('/dashboard', '/settings', '/admin', '/api/', '/simulations/'):
        check('the signed-in app is excluded: {}'.format(path),
              'Disallow: ' + path in txt)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
