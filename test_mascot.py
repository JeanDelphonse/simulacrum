"""The logo mascot (docs/simisi_system_mockup.html) and the hero lift-off
(docs/marketing/simisi_mascot_liftoff.html).

The walking hero mascot with per-load colours was retired when the home page
moved to the system mockup; the mascot now lives in the logo, in the nav and
the public footer. The failure modes worth guarding are quiet ones: an element
id in the SVG would collide between the two copies on every page, and a renamed
hook class stops the blink and the hover wave with no error anywhere.

    python test_mascot.py
"""
import io
import os
import sys

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
    section('The partial')
    logo = io.open('app/templates/partials/_logo.html', encoding='utf-8').read()
    art = io.open('app/templates/partials/_mascot_svg.html', encoding='utf-8').read()
    check('the logo is one macro', '{% macro logo(' in logo)
    check('the logo draws the shared artwork', "mascot_ui.mascot_svg('logo-mascot')" in logo)
    check('no element ids, so several copies can share a page', ' id="' not in logo + art)
    check('the artwork is hidden from assistive tech; the link carries the name',
          'aria-hidden="true"' in art and 'aria-label="{{ label }}"' in logo)
    check('the wordmark is Simi + gold SI', '<span>Simi<b>SI</b></span>' in logo)
    check('the blink and wave hooks are present',
          'class="lm-eyes"' in art and 'class="lm-arm"' in art)
    copies = 0
    for root, _d, files in os.walk('app/templates'):
        for f in files:
            # The SME Console draws its own large mascot from its own mockup.
            if f.endswith('.html') and f != 'console.html':
                copies += io.open(os.path.join(root, f), encoding='utf-8').read().count('M146,46 C150,16 174,8 194,4')
    check('the logo and hero artwork lives in one file', copies == 1, copies)

    section('The hero lift-off (docs/marketing/simisi_mascot_liftoff.html)')
    home_css = io.open('app/static/css/home.css', encoding='utf-8').read()
    check('a 10 s loop (owner, 2026-11-04; the demo ran 5 s)',
          'animation:mbLift 10s' in home_css and 'animation:mbShadow 10s' in home_css)
    check('the lift itself keeps the demo timing (stops halved to 5/15/22%)',
          '  5%  {opacity:.22;' in home_css and '  15% {opacity:.22;' in home_css and '  22% {opacity:0;' in home_css)
    check('the lifted copy peaks at .22 and the print sits at .07',
          '{opacity:.22;transform:translateY(-14px)' in home_css and '.mascot-bg .print{opacity:.07;' in home_css)
    check('never blocks clicks', '.mascot-bg{position:absolute;z-index:5;pointer-events:none;' in home_css)
    check('reduced motion shows only the print',
          '@media(prefers-reduced-motion:reduce){.mascot-bg .lift,.mascot-bg .shadow{animation:none;opacity:0;}}' in home_css)
    check('the logo blink stays off in the hero copy', '.mascot-bg .lm-eyes,.mascot-bg .lm-arm{animation:none;}' in home_css)

    css = io.open('app/static/css/system.css', encoding='utf-8').read()
    check('system.css animates those hooks', '.lm-eyes {' in css and '.ss-logo:hover .lm-arm' in css)
    check('…and honours reduced motion', 'prefers-reduced-motion' in css)

    section('The rendered pages')
    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        client = app.test_client()
        for url in ('/', '/login', '/careers'):
            body = client.get(url).get_data(as_text=True)
            nav = body.split('id="ssNav"')[1].split('</nav>')[0] if 'id="ssNav"' in body else ''
            foot = body.split('<footer class="pf">')[1] if '<footer class="pf">' in body else ''
            check('%-9s logo in the nav and the footer' % url,
                  'class="ss-logo' in nav and 'class="ss-logo' in foot)
        home = client.get('/').get_data(as_text=True)
        check('the retired walker is gone from the home page',
              'class="walker"' not in home and 'm-stop-1' not in home)
        hero = home.split('<header class="hero has-mascot-bg" id="top">')[1] if 'has-mascot-bg' in home else ''
        first = hero.split('<div class="wrap hero-top">')[0]
        check('the mascot is the first child of the hero, hidden from assistive tech',
              '<div class="mascot-bg" aria-hidden="true">' in first
              and first.count('<svg class="print"') == 1 and first.count('<svg class="lift"') == 1
              and '<span class="shadow"></span>' in first)
        check('…on the home page only', 'mascot-bg' not in client.get('/login').get_data(as_text=True)
              and 'mascot-bg' not in client.get('/careers').get_data(as_text=True))
        check('the slogan is unchanged',
              'See the five ways your career becomes wealth&nbsp;— before you live any of them.' in home)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
