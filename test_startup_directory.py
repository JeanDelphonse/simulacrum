"""Founder Track investor directory (/startups).

Published, name-cleared startup pages whose founders opted in, grouped by
stage in ladder order. Public facts only (never the ask). "Unlock full
details" follows the per-venture investor access rule and only shows while
investor connection is open; a browser that already has access gets "Open
investor view". The opt-in toggle is the publishing decision. Sitemap, nav
and footer. In-memory SQLite; network and email blocked.

    python test_startup_directory.py
"""
import io
import os
import sys
from datetime import datetime

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail and not condition else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def main():
    import re
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
    import tempfile
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      UPLOAD_FOLDER=tempfile.mkdtemp(prefix='stdir_'), BASE_URL='https://simisi.io')
    from app.services import email_service
    email_service._send = lambda *a, **k: None

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.founder import StartupPage, VentureMember
        from app.models.user import User
        from app.services import founder_config, founder_feature, founder_ventures
        from app.services import startup_access_service as sas
        from app.services import startup_page_service as sps
        now = datetime.utcnow()

        def user(email, admin=False):
            u = User(id=generate_id(), email=email, full_name=email.split('@')[0].title(), password_hash='x',
                     is_admin=admin, email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        A_ID = user('admin@x.com', admin=True).id
        owners = {k: user('%s@x.com' % k).id for k in ('seedco', 'ideaco', 'preco', 'draftco', 'heldco')}
        COF = user('cof@x.com').id

        client = app.test_client()
        for verb in ('get', 'post'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                if uid:
                    s['_user_id'], s['_fresh'] = uid, True
                else:
                    s.pop('_user_id', None)

        def page_for(key, name, stage, status='published', check_state='clear', listed=True, rung=1, **extra):
            v = founder_ventures.create_venture(db.session.get(User, owners[key]), name, 'One liner', stage)
            v.paid_at, v.rung = now, rung
            p = sps.ensure_page(v)
            p.status, p.name_check, p.show_in_directory, p.stage = status, check_state, listed, stage
            p.published_at = now if status == 'published' else None
            p.one_liner = extra.pop('one_liner', '%s does useful things for small businesses.' % name)
            for k, val in extra.items():
                setattr(p, k, val)
            db.session.commit()
            return v, p

        seed_v, seed_p = page_for('seedco', 'Dockwise', 'seed', rung=4,
                                  traction=[{'label': 'Paying pilots', 'value': '3', 'show': True}],
                                  ask_round='SECRETROUND', ask_amount=1500000, use_of_funds='SECRETUSE',
                                  founder_stated=['one_liner'])
        idea_v, idea_p = page_for('ideaco', 'Tinyledger', 'idea')
        pre_v, pre_p = page_for('preco', 'Quietpre', 'pre-seed', listed=False)
        page_for('draftco', 'Draftly', 'seed', status='draft')
        page_for('heldco', 'Heldname', 'seed', check_state='held')

        print('\n1. Hidden while the Founder Track is off')
        login(None)
        check('/startups 404s while the track is off', client.get('/startups').status_code == 404)

        print('\n2. Listing and grouping')
        founder_feature.set_mode('founder_track', 'allowlist', A_ID)
        res = client.get('/startups')
        html = res.get_data(as_text=True)
        check('/startups opens to visitors once startup pages are public', res.status_code == 200, res.status_code)
        check('opted-in published pages are listed', 'Dockwise' in html and 'Tinyledger' in html)
        check('a published page that did not opt in is not', 'Quietpre' not in html)
        check('drafts and name-held pages are not', 'Draftly' not in html and 'Heldname' not in html)
        check('stages are grouped in ladder order (Idea before Seed)',
              html.index('id="sd-h-idea"') < html.index('id="sd-h-seed"') and 'id="sd-h-pre-seed"' not in html)
        check('each card links to the public startup page',
              'href="/startup/%s"' % seed_p.slug in html and 'href="/startup/%s"' % idea_p.slug in html)
        check('cards show stage, rung and public metrics', 'Rung 4 of 5' in html and 'Paying pilots' in html)
        check('founder-stated claims are labelled', 'founder-stated' in html)
        check('the ask never appears', 'SECRETROUND' not in html and 'SECRETUSE' not in html and '1,500,000' not in html)
        check('no unlock while investor connection is off', '/unlock' not in html)
        check('stage pills carry counts', re.search(r'Seed <span class="n">1</span>', html) is not None
              and re.search(r'All stages <span class="n">2</span>', html) is not None)
        check('the page has the public footer', 'class="pf"' in html)
        check('the full page is indexable with its own canonical',
              'content="index, follow"' in html and 'href="https://simisi.io/startups"' in html)

        print('\n3. Stage filter')
        html = client.get('/startups?stage=seed').get_data(as_text=True)
        check('?stage=seed shows only seed startups', 'Dockwise' in html and 'Tinyledger' not in html)
        check('a filtered view is noindex and points its canonical at /startups',
              'noindex, follow' in html and 'href="https://simisi.io/startups"' in html)
        html = client.get('/startups?stage=series-a').get_data(as_text=True)
        check('an empty stage says so', 'No Series A startups are listed yet' in html)
        check('an unknown stage falls back to all stages',
              'Tinyledger' in client.get('/startups?stage=bogus').get_data(as_text=True))

        print('\n4. The payment rule for full details')
        founder_config.set('fnd_counsel_signoff_at', '2026-11-01', A_ID)
        founder_feature.set_mode('founder_investor', 'on', A_ID)
        html = client.get('/startups').get_data(as_text=True)
        price = sas.price_cents()
        check('with investor connection open, each card offers the paid unlock',
              'href="/startup/%s/unlock"' % seed_p.slug in html
              and ('Unlock full details · $%d' % (price // 100)) in html)
        check('the investor access terms are shown (payment does not verify accreditation)',
              'does not verify' in html)
        sas.issue_link(seed_p, db.session.get(User, owners['seedco']), 'Ivy', investor_email='ivy@fund.vc')
        sas.grant_invited(seed_p, None, 'ivy@fund.vc')
        with client.session_transaction() as s:
            s[sas.SESSION_KEY] = ['ivy@fund.vc']
        html = client.get('/startups').get_data(as_text=True)
        check('a browser that already has access gets "Open investor view" instead',
              'href="/startup/%s/investor"' % seed_p.slug in html
              and 'href="/startup/%s/unlock"' % seed_p.slug not in html
              and 'href="/startup/%s/unlock"' % idea_p.slug in html)
        with client.session_transaction() as s:
            s.pop(sas.SESSION_KEY, None)

        print('\n5. The opt-in toggle is the publishing decision')
        founder_feature.set_mode('founder_track', 'on', A_ID)
        login(owners['preco'])
        body = client.get('/founder/v/%s/page' % pre_v.id).get_data(as_text=True)
        check('the editor shows the directory toggle', 'id="dirToggle"' in body and 'List in the investor directory' in body)
        r = client.post('/api/founder/v/%s/page' % pre_v.id, json={'show_in_directory': True})
        check('the publishing holder can list the page', r.status_code == 200
              and db.session.get(StartupPage, pre_p.id).show_in_directory, r.get_json())
        login(None)
        check('... and it appears in the directory', 'Quietpre' in client.get('/startups').get_data(as_text=True))
        db.session.add(VentureMember(venture_id=pre_v.id, user_id=COF, role='cofounder', status='active', joined_at=now))
        db.session.commit()
        login(COF)
        r = client.post('/api/founder/v/%s/page' % pre_v.id, json={'show_in_directory': False})
        check('a co-founder without the publishing decision cannot change it', r.status_code == 400
              and db.session.get(StartupPage, pre_p.id).show_in_directory, r.get_json())
        login(owners['seedco'])
        r = client.post('/api/founder/v/%s/page' % pre_v.id, json={'show_in_directory': False})
        check('a non-member cannot change it', r.status_code in (403, 404), r.status_code)
        login(owners['preco'])
        r = client.post('/api/founder/v/%s/page' % pre_v.id, json={'show_in_directory': False})
        check('the holder can take it off the directory', r.status_code == 200
              and not db.session.get(StartupPage, pre_p.id).show_in_directory)
        sps.unpublish(db.session.get(StartupPage, seed_p.id), db.session.get(User, owners['seedco']))
        login(None)
        html = client.get('/startups').get_data(as_text=True)
        check('an unpublished page drops out of the directory even when opted in',
              db.session.get(StartupPage, seed_p.id).status != 'published'
              and db.session.get(StartupPage, seed_p.id).show_in_directory and 'Dockwise' not in html)

        print('\n6. Sitemap, navigation, robots')
        smap = client.get('/sitemap.xml').get_data(as_text=True)
        check('the sitemap lists /startups while a startup is listed', '<loc>https://simisi.io/startups</loc>' in smap)
        from app.models.bio_page import BioPage
        from app.models.profile import UserProfile
        db.session.add(UserProfile(user_id=COF, username='cora', display_name='Cora Cofounder'))
        db.session.add(BioPage(user_id=COF, slug='cora', status='published'))
        db.session.commit()
        home = client.get('/').get_data(as_text=True)
        check('the home page has a "Startups on SimiSI" section', 'id="startups"' in home and 'Startups on SimiSI' in home)
        check('... with a "Browse all startups" link to /startups', 'href="/startups">Browse all startups</a>' in home)
        check('... listing the opted-in startups with links to their pages',
              'Tinyledger' in home and 'href="/startup/%s"' % idea_p.slug in home and 'Idea · View page' in home)
        check('... and not the ones that are unpublished or not listed', 'Draftly' not in home and 'Heldname' not in home
              and 'Dockwise' not in home and 'Quietpre' not in home)
        check('... placed right below People on SimiSI', 'id="people"' in home
              and home.index('id="people"') < home.index('id="startups"') < home.index('id="sims"'))
        fpage = client.get('/founder').get_data(as_text=True)
        check('/founder shows "Startups on SimiSI" with a Browse all startups link',
              'id="startups"' in fpage and 'Startups on SimiSI' in fpage and 'href="/startups"' in fpage
              and 'Tinyledger' in fpage, fpage[:0])
        check('... above How it works', fpage.index('id="startups"') < fpage.index('id="how"'))
        StartupPage.query.update({'show_in_directory': False})
        db.session.commit()
        smap = client.get('/sitemap.xml').get_data(as_text=True)
        check('... and not when none is', '/startups</loc>' not in smap)
        check('the home page hides the section when no startup is listed', 'id="startups"' not in client.get('/').get_data(as_text=True))
        check('/founder hides it too when no startup is listed', 'id="startups"' not in client.get('/founder').get_data(as_text=True))
        check('the empty directory explains how founders get listed',
              'Founders choose whether to appear here' in client.get('/startups').get_data(as_text=True))
        founder_feature.set_mode('founder_track', 'on', A_ID)
        from app.services import site_nav
        with app.test_request_context('/'):
            nav = site_nav.resolve()
        items = {it['id']: it['href'] for gr in nav['groups'] if gr['id'] == 'founders' for c in gr['columns']
                 for it in c['items']}
        check('the Founder Track menu links to Explore startups', items.get('fnd_directory') == '/startups', items)
        check('robots.txt does not block /startups', 'Disallow: /startups' not in client.get('/robots.txt').get_data(as_text=True))

        print('\n7. Copy')
        from app.services import compliance_filter
        tpl = io.open(os.path.join(ROOT, 'app/templates/startup/directory.html'), encoding='utf-8').read()
        visible = re.sub(r'\{[%#{].*?[%#}]\}', ' ', re.sub(r'<style.*?</style>', ' ', tpl, flags=re.S), flags=re.S)
        visible = re.sub(r'<[^>]+>', ' ', visible)
        check('directory copy passes the banned-phrase check', not compliance_filter.banned_phrases(visible),
              compliance_filter.banned_phrases(visible))
        check('the migration exists', os.path.exists(os.path.join(ROOT, 'sql', '075_startup_directory.sql')))

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
