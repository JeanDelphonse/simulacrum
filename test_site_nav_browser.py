"""Audience nav in a real browser (NAV_AUDIENCE_PLAN.md, quality checks).

No server: Playwright requests http://simisi.test/... and every request is
answered in-process by the Flask test client (page.route). Other hosts (fonts)
are aborted, so the run is offline.

Run: python test_site_nav_browser.py [--shots]
     --shots saves 1440 px (each panel open) and 390 px (drawer open)
     screenshots to docs/screenshots/nav-audience/.
Skips (exit 0) when Playwright isn't installed.
"""
import os
import re
import sys
import warnings
from urllib.parse import urlsplit

warnings.filterwarnings('ignore')
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    print('playwright not installed; skipped')
    sys.exit(0)

ORIGIN = 'http://simisi.test'
SHOTS = '--shots' in sys.argv
SHOT_DIR = os.path.join('docs', 'screenshots', 'nav-audience')
PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    print('  %s %s%s' % ('ok  ' if condition else 'FAIL', label, ('  -- %s' % (detail,)) if detail and not condition else ''))


def main():
    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)
    ctx = app.app_context()
    ctx.push()
    from app import models as _models  # noqa: F401
    from app.models import user_event as _ue  # noqa: F401
    db.create_all()
    from app.models.platform_settings import PlatformSetting
    PlatformSetting.set('blog_nav_min_posts', '0')
    client = app.test_client()
    events = []

    def serve(route, request):
        parts = urlsplit(request.url)
        if parts.netloc != 'simisi.test':
            return route.abort()
        path = parts.path + ('?' + parts.query if parts.query else '')
        if parts.path == '/api/nav-event':
            events.append(request.post_data)
        r = client.open(path, method=request.method, data=request.post_data_buffer,
                        headers={'Content-Type': request.headers.get('content-type', '')})
        headers = {k: v for k, v in r.headers.items() if k.lower() not in ('content-length', 'transfer-encoding')}
        return route.fulfill(status=r.status_code, headers=headers, body=r.get_data())

    with sync_playwright() as p:
        browser = p.chromium.launch()

        def page(width, height=900):
            pg = browser.new_page(viewport={'width': width, 'height': height})
            errors = []
            pg.on('pageerror', lambda e: errors.append(str(e)))
            pg.on('console', lambda m: m.type == 'error' and not (
                m.text.startswith('Failed to load resource') and 'simisi.test' not in (m.location or {}).get('url', ''))
                and errors.append(m.text))
            pg.route('**/*', serve)
            pg.errors = errors
            return pg

        def state(pg):
            return pg.evaluate("""() => {
              const t = id => document.getElementById(id);
              const vis = el => !!el && !el.hidden && getComputedStyle(el).display !== 'none';
              return {
                pro: vis(t('ssPanel-professionals')), emp: vis(t('ssPanel-employers')), scrim: vis(t('ssScrim')),
                proExp: t('ssTrig-professionals').getAttribute('aria-expanded'),
                empExp: t('ssTrig-employers').getAttribute('aria-expanded'),
                active: document.activeElement && (document.activeElement.id || document.activeElement.getAttribute('data-nav-item')),
              };
            }""")

        for path in ('/', '/transition-package'):
            print('\n1440 px  %s' % path)
            pg = page(1440)
            pg.goto(ORIGIN + path, wait_until='load')
            pg.wait_for_timeout(200)
            kind = pg.evaluate("() => [document.getElementById('ssTrig-professionals').tagName, document.getElementById('ssTrig-employers').tagName]")
            check('triggers are real buttons with aria-controls', kind == ['BUTTON', 'BUTTON'] and pg.evaluate(
                "() => document.getElementById('ssTrig-employers').getAttribute('aria-controls') === 'ssPanel-employers'"))
            hero_before = pg.evaluate("() => { const m = document.querySelector('main').firstElementChild; return m.getBoundingClientRect().top; }")
            pg.click('#ssTrig-professionals')
            s = state(pg)
            check('click opens For professionals, with the scrim', s['pro'] and not s['emp'] and s['scrim'] and s['proExp'] == 'true', s)
            hero_after = pg.evaluate("() => { const m = document.querySelector('main').firstElementChild; return m.getBoundingClientRect().top; }")
            check('no layout shift: the panel overlays the page', hero_before == hero_after, (hero_before, hero_after))
            look = pg.evaluate("""() => {
              const t = document.getElementById('ssTrig-professionals');
              const inner = document.querySelector('#ssPanel-professionals .ss-mega-inner').getBoundingClientRect();
              const logo = document.querySelector('#ssNav .ss-logo').getBoundingClientRect();
              const right = document.querySelector('#ssNav .ss-nav-right').getBoundingClientRect();
              return {line: getComputedStyle(t, '::after').opacity, chev: getComputedStyle(t.querySelector('.ss-chev')).transform,
                      color: getComputedStyle(t).color, left: Math.round(inner.left - logo.left), right: Math.round(inner.right - right.right),
                      cols: document.querySelectorAll('#ssPanel-professionals .ss-mcol').length,
                      promo: !!document.querySelector('#ssPanel-professionals .ss-promo')};
            }""")
            check('open trigger: navy, gold underline, chevron turned', look['line'] == '1' and look['chev'] != 'none'
                  and look['color'] == 'rgb(13, 27, 62)', look)
            check('panel aligned to the page container', abs(look['left']) <= 1 and abs(look['right']) <= 1, look)
            check('three columns and the promo card', look['cols'] == 3 and look['promo'], look)
            pg.click('#ssTrig-employers')
            s = state(pg)
            check('opening For employers closes For professionals (one at a time)', s['emp'] and not s['pro']
                  and s['proExp'] == 'false' and s['empExp'] == 'true', s)
            pg.click('#ssTrig-employers')
            s = state(pg)
            check('clicking the open trigger closes it', not s['emp'] and not s['scrim'] and s['empExp'] == 'false', s)
            pg.click('#ssTrig-employers')
            pg.keyboard.press('Escape')
            s = state(pg)
            check('Escape closes and returns focus to the trigger', not s['emp'] and s['active'] == 'ssTrig-employers', s)
            pg.click('#ssTrig-professionals')
            pg.mouse.click(700, 860)
            s = state(pg)
            check('a click outside (on the scrim) closes', not s['pro'] and not s['scrim'], s)

            # Keyboard only.
            pg.goto(ORIGIN + path, wait_until='load')
            for _ in range(12):
                pg.keyboard.press('Tab')
                if state(pg)['active'] == 'ssTrig-professionals':
                    break
            check('Tab reaches For professionals', state(pg)['active'] == 'ssTrig-professionals')
            pg.keyboard.press('Enter')
            check('Enter opens it', state(pg)['pro'])
            order = pg.evaluate("() => [...document.querySelectorAll('#ssPanel-professionals [data-nav-item]')].map(e => e.getAttribute('data-nav-item'))")
            seen = []
            for _ in order:
                pg.keyboard.press('Tab')
                seen.append(state(pg)['active'])
            check('Tab walks the panel in reading order', seen == order, (seen, order))
            pg.keyboard.press('Tab')
            check('then on to For employers', state(pg)['active'] == 'ssTrig-employers', state(pg)['active'])
            pg.keyboard.press(' ')
            s = state(pg)
            check('Space opens For employers and closes the other', s['emp'] and not s['pro'], s)
            ring = pg.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
            check('the focused trigger shows a focus ring', ring not in ('none', ''), ring)
            pg.keyboard.press('Tab')
            check('Tab moves into the employer panel', state(pg)['active'] == 'tp_included', state(pg)['active'])
            ring = pg.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
            check('panel items show a focus ring', ring not in ('none', ''), ring)
            pg.keyboard.press('Escape')
            check('Escape from inside the panel returns focus to its trigger', state(pg)['active'] == 'ssTrig-employers')

            if SHOTS and path == '/':
                os.makedirs(SHOT_DIR, exist_ok=True)
                for g in ('professionals', 'employers'):
                    pg.click('#ssTrig-' + g)
                    pg.wait_for_timeout(250)
                    pg.screenshot(path=os.path.join(SHOT_DIR, '1440-%s-open.png' % g), clip={'x': 0, 'y': 0, 'width': 1440, 'height': 560})
                    pg.keyboard.press('Escape')

            pg.click('#ssTrig-professionals')
            pg.set_viewport_size({'width': 390, 'height': 800})
            pg.wait_for_timeout(150)
            check('resizing to phone width closes the panel', not state(pg)['pro'] and not state(pg)['scrim'])
            pg.set_viewport_size({'width': 1440, 'height': 900})

            n0 = len(events)
            pg.click('#ssTrig-employers')
            with pg.expect_navigation():
                pg.click('#ssPanel-employers [data-nav-item="tp_privacy"]')
            pg.wait_for_timeout(300)
            got = ''.join(x or '' for x in events[n0:])
            check('nav_open and nav_click reach /api/nav-event', '"nav_open"' in got and '"employers"' in got
                  and '"nav_click"' in got and '"tp_privacy"' in got, got)
            check('choosing a link navigates to it', pg.url.endswith('/transition-package#privacy'), pg.url)
            check('no console errors', not pg.errors, pg.errors)
            pg.close()

        print('\n390 px')
        for path in ('/', '/transition-package'):
            pg = page(390, 800)
            pg.goto(ORIGIN + path, wait_until='load')
            pg.wait_for_timeout(150)
            bar = pg.evaluate("""() => { const v = s => { const e = document.querySelector(s); return !!e && getComputedStyle(e).display !== 'none'; };
              return {links: v('#ssNav .ss-nav-links'), login: v('#ssNav .ss-nav-login'), cta: v('#ssNav .btn.primary'), menu: v('#ssMenuBtn')}; }""")
            check('%s bar shows the logo and Menu only' % path, bar == {'links': False, 'login': False, 'cta': False, 'menu': True}, bar)
            pg.click('#ssMenuBtn')
            pg.wait_for_timeout(150)
            d = pg.evaluate("""() => { const dr = document.getElementById('ssDrawer'), mb = document.getElementById('ssMenuBtn');
              return {open: dr.classList.contains('open') && getComputedStyle(dr).visibility === 'visible',
                      exp: mb.getAttribute('aria-expanded'), label: mb.textContent.trim(),
                      focusIn: dr.contains(document.activeElement), lock: getComputedStyle(document.documentElement).overflow,
                      top: Math.round(dr.getBoundingClientRect().top), navBottom: Math.round(document.getElementById('ssNav').getBoundingClientRect().bottom),
                      order: [...dr.querySelectorAll('.ss-dsec-btn, .ss-dlink, .ss-dctas a')].map(e => e.textContent.trim()),
                      desc: dr.querySelectorAll('.ss-mi-desc').length,
                      hscroll: document.documentElement.scrollWidth - innerWidth}; }""")
            if SHOTS and path == '/':
                os.makedirs(SHOT_DIR, exist_ok=True)
                pg.wait_for_timeout(250)
                pg.screenshot(path=os.path.join(SHOT_DIR, '390-drawer-open.png'))
            check('Menu opens the drawer, label Close, aria-expanded', d['open'] and d['exp'] == 'true' and d['label'] == 'Close', d)
            check('focus moves into the drawer', d['focusIn'])
            check('drawer starts right under the bar', d['top'] == d['navBottom'], (d['top'], d['navBottom']))
            check('drawer order: two sections, Playbook, Pricing, Get started free, Log in',
                  d['order'] == ['For professionals', 'For employers', 'Playbook', 'Pricing', 'Get started free', 'Log in'], d['order'])
            check('titles only, no descriptions', d['desc'] == 0)
            check('no horizontal scroll', d['hscroll'] <= 0, d['hscroll'])
            y0 = pg.evaluate('scrollY')
            pg.mouse.wheel(0, 900)
            pg.wait_for_timeout(200)
            check('the page does not scroll behind the drawer', d['lock'] == 'hidden' and pg.evaluate('scrollY') == y0)
            sec = lambda g: pg.evaluate("""g => { const b = document.querySelector('[aria-controls="ssDsec-' + g + '"]');
              return [b.getAttribute('aria-expanded'), !document.getElementById('ssDsec-' + g).hidden]; }""", g)
            check('For professionals starts open, For employers closed', sec('professionals') == ['true', True]
                  and sec('employers') == ['false', False])
            pg.click('[aria-controls="ssDsec-employers"]')
            pg.click('[aria-controls="ssDsec-professionals"]')
            check('sections expand and collapse', sec('employers') == ['true', True] and sec('professionals') == ['false', False])
            pg.keyboard.press('Enter')   # focus is on the professionals header after the click
            check('a section header works from the keyboard', sec('professionals') == ['true', True])
            pg.keyboard.press('Escape')
            end = pg.evaluate("""() => ({open: document.getElementById('ssDrawer').classList.contains('open'),
              focus: document.activeElement && document.activeElement.id, label: document.getElementById('ssMenuBtn').textContent.trim(),
              lock: document.documentElement.classList.contains('ss-lock')})""")
            check('Escape closes the drawer, unlocks the page, focus back on Menu',
                  end == {'open': False, 'focus': 'ssMenuBtn', 'label': 'Menu', 'lock': False}, end)
            pg.click('#ssMenuBtn')
            pg.wait_for_timeout(100)
            with pg.expect_navigation():
                pg.click('#ssDrawer [data-nav-item="people"]')
            check('a drawer link navigates', pg.url.endswith('/explore'), pg.url)
            check('no console errors', not pg.errors, pg.errors)
            pg.close()

        print('\nEvery nav link resolves')
        pg = page(1440)
        pg.goto(ORIGIN + '/', wait_until='load')
        hrefs = pg.evaluate("() => [...new Set([...document.querySelectorAll('#ssNav a[href], #ssDrawer a[href]')].map(a => a.getAttribute('href')))]")
        pg.close()
        bad = []
        for href in hrefs:
            if href.startswith('http'):
                if href != app.config['PLAYBOOK_URL']:
                    bad.append((href, 'unexpected external link'))
                continue
            path, _, anchor = href.partition('#')
            r = client.get(path or '/')
            if r.status_code != 200:
                bad.append((href, r.status_code))
            elif anchor and ('id="%s"' % anchor) not in r.get_data(as_text=True):
                bad.append((href, 'no #%s on the page' % anchor))
        check('%d links: every page returns 200 and every #anchor exists' % len(hrefs), not bad, bad)

        print('\nWithout JavaScript')
        nojs = browser.new_context(java_script_enabled=False, viewport={'width': 1440, 'height': 900}).new_page()
        nojs.route('**/*', serve)
        nojs.goto(ORIGIN + '/', wait_until='load')
        links = nojs.evaluate("() => ['professionals', 'employers'].map(g => { const t = document.getElementById('ssTrig-' + g); return [t.tagName, t.getAttribute('href')]; })")
        check('the triggers are links to /#how and /transition-package', links == [['A', '/#how'], ['A', '/transition-package']], links)
        browser.close()

    ctx.pop()
    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
