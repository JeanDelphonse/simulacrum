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
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)

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

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
