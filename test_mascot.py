"""The logo mascot (docs/simisi_system_mockup.html).

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
    check('the logo is one macro', '{% macro logo(' in logo)
    check('no element ids, so two copies can share a page', ' id="' not in logo)
    check('the artwork is hidden from assistive tech; the link carries the name',
          'aria-hidden="true"' in logo and 'aria-label="{{ label }}"' in logo)
    check('the wordmark is Simi + gold SI', '<span>Simi<b>SI</b></span>' in logo)
    check('the blink and wave hooks are present',
          'class="lm-eyes"' in logo and 'class="lm-arm"' in logo)

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

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
