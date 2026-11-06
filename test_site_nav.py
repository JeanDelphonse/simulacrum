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
        bar = home.split('<ul class="ss-nav-links ss-aud-links">')[1].split('</ul>')[0]
        check('bar order: For professionals, For employers, Playbook, Pricing',
              [m for m in re.findall(r'>([A-Z][A-Za-z ]+)<', bar)] == ['For professionals', 'For employers', 'Playbook', 'Pricing'],
              re.findall(r'>([A-Z][A-Za-z ]+)<', bar))
        check('removed from the bar: The model, Bio page, Questions, Watch demo, the flat employer link',
              not any(x in bar for x in ('The model', 'Bio page', 'Questions', 'Watch demo', '>Journal<')))
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
        signed = client.get('/careers').get_data(as_text=True)
        check('signed-in pages keep the app nav (D2)', 'ss-nav-aud' not in signed and '>Dashboard<' in signed
              and '>Settings<' in signed and 'id="signOutBtn"' in signed and 'ss-mega' not in signed)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
