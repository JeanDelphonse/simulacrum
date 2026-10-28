"""Public listings show only profiles /u/<slug> would actually serve.

The landing page (recent bios + hero cards), /explore and the sitemap share one
rule: the bio page is published and the owner hasn't been deactivated from
/admin. Links use the profile's current username, so a bio page whose stored
slug went stale after a username change doesn't land on "This profile is not
yet published".

Runs against an in-memory SQLite database; no network calls.

    python test_public_listings.py
"""
import os
import sys

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from utils.id_gen import generate_id                          # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    mark = 'ok  ' if condition else 'FAIL'
    line = '  {} {}{}'.format(mark, label, ('  -- ' + str(detail)) if detail else '')
    print(line.encode(sys.stdout.encoding or 'ascii', 'replace')
              .decode(sys.stdout.encoding or 'ascii'))


def main():
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        from datetime import datetime
        from app.models.user import User
        from app.models.profile import UserProfile
        from app.models.bio_page import BioPage
        from app.models.simulation import Simulation
        from app.blueprints.pages.routes import _get_recent_bio_pages, _get_hero_cards
        from app.services import bio_seo

        def make(username, name, status='published', deactivated=False, slug=None):
            u = User(id=generate_id(), email='%s@t.local' % username, full_name=name,
                     password_hash='x', deleted_at=datetime.utcnow() if deactivated else None)
            db.session.add(u)
            db.session.flush()
            db.session.add(UserProfile(id=generate_id(), user_id=u.id, username=username,
                                       display_name=name, tagline='Tagline for %s' % name))
            db.session.add(BioPage(id=generate_id(), user_id=u.id, slug=slug or username,
                                   status=status, theme='light'))
            db.session.add(Simulation(id=generate_id(), user_id=u.id, name='Sim',
                                      expertise_zone='Operations Consulting', status='complete'))
            db.session.commit()
            return u

        make('ada-active', 'Ada Active')
        gone = make('j-m', 'Jay Deactivated', deactivated=True)
        make('dee-draft', 'Dee Draft', status='draft')
        make('new-name', 'Sam Renamed', slug='old-name')

        recent = {c['slug'] for c in _get_recent_bio_pages()}
        hero = {c['slug'] for c in _get_hero_cards(limit=10)}
        smap = {b['slug'] for b in bio_seo.indexable_bios()}
        for label, got in (('recent bios', recent), ('hero cards', hero), ('sitemap', smap)):
            check('%s exclude the deactivated user' % label, 'j-m' not in got, sorted(got))
            check('%s exclude unpublished pages' % label, 'dee-draft' not in got, sorted(got))
            check('%s link renamed users by current username' % label,
                  'new-name' in got and 'old-name' not in got, sorted(got))
            check('%s still include active published users' % label, 'ada-active' in got)

        client = app.test_client()
        home = client.get('/').get_data(as_text=True)
        check('home page renders', 'Ada Active' in home)
        check('home page does not show the deactivated user', 'Jay Deactivated' not in home)
        check('home page does not show the draft profile', 'Dee Draft' not in home)
        check('home page has no link to the deactivated profile', '/u/j-m"' not in home)

        explore = client.get('/explore').get_data(as_text=True)
        check('/explore does not show the deactivated user', 'Jay Deactivated' not in explore)
        check('/explore links the renamed user by username',
              '/u/new-name' in explore and '/u/old-name' not in explore)

        page = client.get('/u/j-m').get_data(as_text=True)
        check('/u/j-m still says not yet published', 'not yet published' in page)

        gone.deleted_at = None   # reactivated from /admin
        db.session.commit()
        check('a reactivated user returns to the listings',
              'j-m' in {c['slug'] for c in _get_recent_bio_pages()})

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
