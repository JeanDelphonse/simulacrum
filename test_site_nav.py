"""Audience navigation (NAV_AUDIENCE_PLAN.md): config, rendering, events.

Run: python test_site_nav.py   (Flask test client, in-memory SQLite, no network)
"""
import os
import re
import sys
import warnings

warnings.filterwarnings('ignore')
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    print('  %s %s%s' % ('ok  ' if condition else 'FAIL', label, ('  -- %s' % (detail,)) if detail and not condition else ''))


def main():
    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=True)

    with app.app_context():
        from app import models as _models  # noqa: F401
        from app.models import user_event as _ue  # noqa: F401
        db.create_all()
        from app.services import site_nav
        from app.models.platform_settings import PlatformSetting

        print('\n1. One config')
        with app.test_request_context('/'):
            nav = site_nav.resolve()
        groups = {g['id']: g for g in nav['groups']}
        check('two audience groups, in order', [g['id'] for g in nav['groups']] == ['professionals', 'employers'])
        check('professionals has four columns incl. the promo; employers three',
              len(groups['professionals']['columns']) == 3 and len(groups['employers']['columns']) == 2)
        items = {it['id']: it for g in nav['groups'] for c in g['columns'] for it in c['items']}
        check('every item has a title and a one-line description',
              all(it['title'] and it['desc'] and '\n' not in it['desc'] for it in items.values()))
        check('item ids are unique across the whole nav',
              len(site_nav.ITEM_IDS) == len(list(site_nav._ids())))
        expect = {'run_simulation': '/register', 'how_it_works': '/#how', 'sample_simulations': '/#sims',
                  'bio_page': '/#bio', 'agents': '/#act', 'sme_console': '/#more', 'people': '/explore',
                  'questions': '/#faq', 'tp_included': '/transition-package#included',
                  'tp_how': '/transition-package#how', 'tp_privacy': '/transition-package#privacy'}
        check('links map to the planned routes', all(items[k]['href'] == v for k, v in expect.items()),
              {k: items[k]['href'] for k in expect})
        check('Watch the demo opens the video modal, not a URL',
              items['watch_demo'].get('action') == 'demo' and items['watch_demo']['href'] is None)
        links = {x['id']: x for x in nav['links'] + nav['actions']}
        check('Playbook goes to the Amazon listing in a new tab',
              links['playbook']['href'] == app.config['PLAYBOOK_URL'] and links['playbook']['external'])
        check('Pricing, Log in, Get started free', links['pricing']['href'] == '/#pricing'
              and links['log_in']['href'] == '/login' and links['get_started']['href'] == '/register')
        check('no-JS trigger targets', groups['professionals']['nojs_href'] == '/#how'
              and groups['employers']['nojs_href'] == '/transition-package')
        src = open('app/services/site_nav.py', encoding='utf-8').read()
        check('no prices in the nav config', not re.search(r'\$\s?\d|\d+\s?(usd|USD)|_cents', src))

        saved = app.config['PLAYBOOK_URL']
        app.config['PLAYBOOK_URL'] = ''
        with app.test_request_context('/'):
            nav2 = site_nav.resolve()
        app.config['PLAYBOOK_URL'] = saved
        check('Playbook falls back to /#playbook when no listing is set',
              {x['id']: x for x in nav2['links']}['playbook']['href'] == '/#playbook')

        print('\n2. Gates and the employer card')
        PlatformSetting.set('blog_nav_min_posts', '5')
        with app.test_request_context('/'):
            hidden = site_nav.resolve()
        PlatformSetting.set('blog_nav_min_posts', '0')
        with app.test_request_context('/'):
            shown = site_nav.resolve()
        ids = lambda n: {it['id'] for g in n['groups'] for c in g['columns'] for it in c['items']}
        check('the Journal follows the blog nav gate', 'journal' not in ids(hidden) and 'journal' in ids(shown))
        PlatformSetting.set('transition_package_mode', 'design_partner')
        with app.test_request_context('/'):
            dp = {g['id']: g for g in site_nav.resolve()['groups']}['employers']['promo']
        PlatformSetting.set('transition_package_mode', 'general')
        with app.test_request_context('/'):
            gen = {g['id']: g for g in site_nav.resolve()['groups']}['employers']['promo']
        PlatformSetting.set('transition_package_mode', 'design_partner')
        check('design-partner mode: the design-partner card', dp['title'] == 'Now enrolling design partners'
              and dp['link_label'] == 'Become a design partner' and dp['href'] == '/transition-package#inquiry')
        check('general mode: the quote card, same form', gen['link_label'] == 'Request a quote'
              and gen['href'] == '/transition-package#inquiry')

        print('\n3. Rendered on every page with the shared header')
        PlatformSetting.set('blog_nav_min_posts', '0')
        client = app.test_client()
        with app.test_request_context('/'):
            nav = site_nav.resolve()
        all_items = [it for g in nav['groups'] for c in g['columns'] for it in c['items']]
        for url in ('/', '/login', '/register', '/careers', '/explore', '/blog', '/transition-package', '/legal/terms'):
            r = client.get(url)
            html = r.get_data(as_text=True)
            head = html.split('id="ssNav"')[1].split('</nav>')[0] if 'id="ssNav"' in html else ''
            drawer = html.split('id="ssDrawer"')[1].split('<main')[0] if 'id="ssDrawer"' in html else ''
            ok = (r.status_code == 200 and 'ss-nav-aud' in html
                  and all(head.count('data-nav-item="%s"' % it['id']) == 1 for it in all_items)
                  and all(drawer.count('data-nav-item="%s"' % it['id']) == 1 for it in all_items)
                  and head.count('class="ss-trigger"') == 2 and head.count('class="ss-mega"') == 2)
            check('%-20s bar, both panels and the drawer from the one config' % url, ok, r.status_code)
        home = client.get('/').get_data(as_text=True)
        navhtml = home.split('id="ssNav"')[1].split('</nav>')[0]
        top = re.findall(r'<li><a [^>]*>([^<]+)<', navhtml)     # bar items only; panel items start on a new line
        check('bar order: For professionals, For employers, Playbook, Pricing',
              top == ['For professionals', 'For employers', 'Playbook', 'Pricing'], top)
        check('removed from the bar: The model, Bio page, People, Journal, Questions, Watch demo, flat employer link',
              not set(top) & {'The model', 'Bio page', 'People on SimiSI', 'Journal', 'Questions', 'Watch demo'})
        check('each panel sits right after its trigger (Tab moves into it)',
              navhtml.index('id="ssTrig-professionals"') < navhtml.index('id="ssPanel-professionals"')
              < navhtml.index('id="ssTrig-employers"') < navhtml.index('id="ssPanel-employers"')
              < navhtml.index('data-nav-item="playbook"'))
        check('panels start hidden; triggers are links until JS upgrades them',
              home.count('class="ss-mega" id="ssPanel-') == 2 and 'role="region" aria-labelledby="ssTrig-professionals" hidden' in home
              and '<a class="ss-trigger" id="ssTrig-professionals" href="/#how"' in home
              and '<a class="ss-trigger" id="ssTrig-employers" href="/transition-package"' in home)
        dr = home.split('id="ssDrawer"')[1].split('<main')[0]
        check('drawer: professionals open, employers collapsed, titles only',
              'aria-expanded="true" aria-controls="ssDsec-professionals"' in dr
              and 'aria-expanded="false" aria-controls="ssDsec-employers"' in dr
              and '<ul id="ssDsec-employers" hidden>' in dr and 'ss-mi-desc' not in dr)
        check('drawer ends with Get started free then Log in',
              dr.index('>Get started free<') < dr.index('>Log in<') and 'btn primary btn-full' in dr)
        check('the mascot logo stays', 'class="ss-logo' in home.split('id="ssNav"')[1].split('</nav>')[0])
        check('the footer link columns are untouched', '<footer class="pf">' in home)

        tpl_dir = 'app/templates'
        literal = []
        for root, _d, files in os.walk(tpl_dir):
            for f in files:
                if not f.endswith('.html') or f == '_site_nav.html':
                    continue
                t = open(os.path.join(root, f), encoding='utf-8').read()
                for phrase in ('They draft; you approve every send', 'From resume to plan in five steps',
                               'Seats, invites and an aggregate dashboard', 'Browse live bio pages'):
                    if phrase in t:
                        literal.append((f, phrase))
        check('no nav description is written into a template', not literal, literal)

        from app.models.user import User
        from datetime import datetime
        u = User(id='NAVUSER01', email='nav@t.local', full_name='Nav Tester', password_hash='x',
                 email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add(u)
        db.session.commit()
        with client.session_transaction() as s:
            s['_user_id'] = 'NAVUSER01'
            s['_fresh'] = True
        from flask import g
        g.pop('_login_user', None)
        PlatformSetting.set('blog_posting_enabled', 'false')
        signed = client.get('/careers').get_data(as_text=True)
        check('signed-in pages get the member nav, not the visitor nav', 'ss-nav-aud' not in signed
              and 'ss-nav-member' in signed and 'ssPanel-professionals' not in signed)

        print('\n5. Member nav')
        mnav_html = signed.split('id="ssNav"')[1].split('</nav>')[0]
        mdrawer = signed.split('id="ssDrawer"')[1].split('<main')[0]
        top = re.findall(r'<li(?: class="ss-has-drop")?><a (?![^>]*ss-mi)[^>]*>([^<]+)<', mnav_html)
        check('bar: Dashboard, My work, Community (no role links for a plain member)',
              top == ['Dashboard', 'My work', 'Community'], top)
        check('the account menu is labelled with the name',
              '<span class="ss-acct-name">Nav Tester</span>' in mnav_html and 'id="ssPanel-account"' in mnav_html)
        for pid, ids in (('my_work', ['m_simulations', 'm_bio_page', 'm_resumes', 'm_contacts']),
                         ('community', ['m_feed', 'm_people', 'm_chats', 'm_journal']),
                         ('account', ['m_settings', 'm_integrations', 'm_notifications', 'm_demo', 'm_help', 'm_signout'])):
            panel = mnav_html.split('id="ssPanel-%s"' % pid)[1].split('</ul>')[0]
            got = re.findall(r'data-nav-item="([^"]+)"', panel)
            check('%s panel: %s' % (pid, ', '.join(ids)), got == ids, got)
        check('Sign out is a sign-out button, set apart', 'data-signout data-nav-item="m_signout"' in mnav_html
              and '<li class="ss-sep">' in mnav_html)
        check('no bio username yet: My bio page goes to Settings -> Profile',
              re.search(r'href="/settings/profile" data-nav-item="m_bio_page"', mnav_html) is not None)
        check('Dashboard is marked current on the dashboard only', 'aria-current="page"' not in mnav_html)
        check('SME guide, and Write a post while posting is paused, are hidden',
              'm_guide' not in mnav_html and 'm_write' not in mnav_html)
        dorder = re.findall(r'class="ss-dlink"[^>]*>([^<]+)<|class="ss-dsec-btn"[^>]*>([^<]+)<', mdrawer)
        dorder = [x or y for x, y in dorder]
        check('drawer: Dashboard, My work (open), Community, Account, then Sign out',
              dorder == ['Dashboard', 'My work', 'Community', 'Account'] and 'aria-controls="ssDsec-my_work"' in mdrawer
              and '<ul id="ssDsec-community" hidden>' in mdrawer and '<ul id="ssDsec-account" hidden>' in mdrawer
              and mdrawer.count('data-signout') == 1 and 'ss-mi-desc' not in mdrawer, dorder)
        check('every member item renders once in the bar and once in the drawer',
              all(mnav_html.count('data-nav-item="%s"' % i) == 1 and mdrawer.count('data-nav-item="%s"' % i) == 1
                  for i in ('m_simulations', 'm_bio_page', 'm_feed', 'm_chats', 'm_settings', 'm_demo', 'm_signout')))

        from app.models.profile import UserProfile
        db.session.add(UserProfile(user_id='NAVUSER01', username='navtester'))
        u = db.session.get(User, 'NAVUSER01')
        u.is_admin = True
        u.is_partner = True
        db.session.commit()
        g.pop('_login_user', None)
        dash = client.get('/dashboard').get_data(as_text=True)
        mh = dash.split('id="ssNav"')[1].split('</nav>')[0]
        top = re.findall(r'<li(?: class="ss-has-drop")?><a (?![^>]*ss-mi)[^>]*>([^<]+)<', mh)
        check('roles add their links: Partner dashboard, Admin', top == ['Dashboard', 'My work', 'Community',
                                                                      'Partner dashboard', 'Admin'], top)
        check('with a username, My bio page opens the editor', 'href="/u/navtester/edit" data-nav-item="m_bio_page"' in mh)
        check('Dashboard is marked current on /dashboard', 'class="is-current" aria-current="page">Dashboard<' in mh)
        check('admins can always post, so Write a post shows', 'data-nav-item="m_write"' in mh)
        with app.test_request_context('/'):
            sme = site_nav.resolve_member(u, is_sme=True)
        check('an SME sees the SME Console link', [x['id'] for x in sme['role_links']] == ['m_partner', 'm_sme', 'm_admin'])
        check('member ids are in the event allowlist', {'my_work', 'community', 'account'} <= site_nav.PANEL_IDS
              and {'m_bio_page', 'm_signout', 'm_dashboard'} <= site_nav.ITEM_IDS)
        u.is_admin = False
        u.is_partner = False
        db.session.commit()

        print('\n4. nav_open / nav_click events')
        from app.models.user_event import UserEvent
        anon = app.test_client()
        before = UserEvent.query.count()
        r1 = anon.post('/api/nav-event', json={'event': 'nav_open', 'panel': 'employers'})
        r2 = anon.post('/api/nav-event', json={'event': 'nav_click', 'item': 'tp_privacy'},
                       headers={'Referer': 'https://simisi.io/?email=a@b.com'})
        rows = UserEvent.query.order_by(UserEvent.id.desc()).limit(2).all()
        check('a known panel and item are stored', r1.status_code == 204 and r2.status_code == 204
              and UserEvent.query.count() == before + 2
              and {(x.event_type, tuple(sorted(x.event_data.items()))) for x in rows}
              == {('nav_open', (('panel', 'employers'),)), ('nav_click', (('item', 'tp_privacy'),))})
        check('no user, IP or referrer on the rows', all(x.user_id is None and x.ip_address is None
                                                         and set(x.event_data) <= {'panel', 'item'} for x in rows))
        bad = [anon.post('/api/nav-event', json=b).status_code for b in (
            {'event': 'nav_click', 'item': 'someone@example.com'}, {'event': 'nav_open', 'panel': 'admin'},
            {'event': 'signup', 'item': 'pricing'}, {})]
        check('unknown ids and events are rejected and not stored', bad == [400] * 4
              and UserEvent.query.count() == before + 2, bad)
        js = open('app/static/js/site_nav.js', encoding='utf-8').read()
        check('the browser sends only the event and the id', "data[key] = value" in js
              and 'location' not in js and 'cookie' not in js)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
