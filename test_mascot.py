"""The hero mascot's per-load colours.

The failure mode worth guarding is silent: the colours are driven by three CSS
hooks on the SVG (.m-stop-1, .m-stop-2, .m-limb). Drop one while editing the
artwork and the mascot keeps rendering perfectly in its default colours, with no
error anywhere — the randomiser just stops having any effect.

The colours themselves are verified in a browser; this pins the wiring.

    python test_mascot.py
"""
import io
import os
import re
import sys

os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []

# The palette as briefed. "white" was listed twice; one entry is enough.
BRIEFED = ['red', 'white', 'blue', 'yellow', 'brown', 'purple', 'green',
           'teal', 'gold', 'silver']


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label,
                              ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def section(title):
    print('\n' + title)


def main():
    tpl = io.open('app/templates/landing.html', encoding='utf-8').read()

    # ── The hooks the randomiser drives ─────────────────────────────────────
    section('The three CSS hooks')
    check('the body gradient has both stops labelled',
          tpl.count('class="m-stop-1"') == 1 and tpl.count('class="m-stop-2"') == 1,
          '%d / %d' % (tpl.count('class="m-stop-1"'), tpl.count('class="m-stop-2"')))
    check('both arms are labelled', tpl.count('class="m-limb"') == 2,
          tpl.count('class="m-limb"'))
    for sel, prop in [('.m-stop-1', '--m-body-1'), ('.m-stop-2', '--m-body-2'),
                      ('.m-limb', '--m-arm')]:
        check('%-10s reads var(%s)' % (sel, prop),
              re.search(r'\.walker \%s\s*\{[^}]*var\(%s\)' % (sel, prop), tpl)
              is not None)
    check('the defaults are the original light body and arm',
          '--m-body-1:#F4F7FB' in tpl and '--m-body-2:#D6DEEA' in tpl
          and '--m-arm:#E4EAF2' in tpl)

    # Everything the body is made of must share the one gradient, or a colour
    # change would leave part of the mascot behind.
    body_parts = tpl.count('fill="url(#mascotBody)"')
    check('every body part shares the one gradient (feet, torso, head)',
          body_parts == 4, '%d fills' % body_parts)

    # ── The palette ─────────────────────────────────────────────────────────
    section('The palette')
    block = tpl[tpl.index('var P = {'):tpl.index('};', tpl.index('var P = {'))]
    got = re.findall(r'(\w+):\s*\[', block)
    check('all ten briefed colours are present', got == BRIEFED,
          'missing %s / extra %s' % (sorted(set(BRIEFED) - set(got)),
                                     sorted(set(got) - set(BRIEFED))))
    triples = re.findall(r'\w+:\s*\[([^\]]*)\]', block)
    check('each is [body light, body mid, arm]',
          all(len(t.split(',')) == 3 for t in triples),
          [len(t.split(',')) for t in triples])
    hexes = re.findall(r'#([0-9A-Fa-f]{6})', block)
    check('every value is a full 6-digit hex', len(hexes) == 30, len(hexes))

    # Body tints have to stay light: the mascot sits on a dark navy hero and its
    # own visor face is near-black, so a dark body loses its outline against one
    # and its features against the other.
    dark = []
    for t in triples:
        light = re.findall(r'#([0-9A-Fa-f]{6})', t)[0]
        lum = (int(light[0:2], 16) * 0.299 + int(light[2:4], 16) * 0.587
               + int(light[4:6], 16) * 0.114)
        if lum < 180:
            dark.append((light, round(lum)))
    check('no body tint is dark enough to swallow the face', not dark, dark)

    # ── The picker ──────────────────────────────────────────────────────────
    section('The picker')
    check('it runs against .walker', "document.querySelector('.walker')" in tpl)
    check('it bails out rather than throwing if the mascot is gone',
          'if (!W) return;' in tpl)
    check('the arm is drawn from the colours the body did not take',
          "filter(function (n) { return n !== body; })" in tpl)
    check('it sets all three properties',
          all(("setProperty('%s'" % v) in tpl
              for v in ('--m-body-1', '--m-body-2', '--m-arm')))
    check('it records the pair for support questions',
          "setAttribute('data-body'" in tpl and "setAttribute('data-arm'" in tpl)

    # Placement matters: in the page's load handler it would paint the default
    # first and then visibly swap.
    walker = tpl.index('<div class="walker"')
    picker = tpl.index("var W = document.querySelector('.walker')")
    track_end = tpl.index('<div class="hero-chip">')
    check('the picker sits inline beside the mascot, not in the load handler',
          walker < picker < track_end)
    # The walk/dance sequence deliberately runs from a load handler placed
    # before the markup; the colour picker must not, or the default paints
    # first and then visibly swaps. So assert on its own script block.
    blk_open = tpl.rindex('<script>', 0, picker)
    blk_close = tpl.index('</script>', picker)
    check('the picker runs at parse time, not from a deferred handler',
          'addEventListener' not in tpl[blk_open:blk_close]
          and 'DOMContentLoaded' not in tpl[blk_open:blk_close])

    # ── The link wrapped around the mascot ──────────────────────────────────
    # Each of these was a live defect: the runway's pointer-events:none made
    # the link clickable by keyboard and dead to the mouse; the lean selector
    # was a direct child of .walker, which the <a> silently broke; and the
    # link's only accessible name came from the artwork, so it announced
    # "Simulacrum mascot" and said nothing about where it goes.
    section('The mascot link')
    check('the pointer is handed back to the link',
          re.search(r'\.walker a \{[^}]*pointer-events:\s*auto', tpl) is not None)
    check('and it shows a pointer cursor',
          re.search(r'\.walker a \{[^}]*cursor:\s*pointer', tpl) is not None)
    check('the <a> is a block, so it wraps the artwork box',
          re.search(r'\.walker a \{[^}]*display:\s*block', tpl) is not None)
    check('keyboard focus is visible', '.walker a:focus-visible' in tpl)
    check('the lean survives a wrapper round the svg',
          '.walker.walking svg{' in tpl and '.walker.walking > svg' not in tpl)
    check('the link names its destination',
          'aria-label="Sign in to SimiSI"' in tpl)
    check('the artwork is hidden from assistive tech, so the name is stated once',
          'aria-hidden="true" focusable="false"' in tpl
          and 'aria-label="Simulacrum mascot"' not in tpl)
    check('no leftover animation tooltip on hover',
          'walks in, dances, then idles"' not in tpl)
    check('the runway itself stays inert',
          'pointer-events:none' in tpl[tpl.index('.track{'):tpl.index('.track{') + 200])

    # ── It survives the render ──────────────────────────────────────────────
    section('The rendered page')
    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        body = app.test_client().get('/').get_data(as_text=True)
    check('the page renders', '<section id="hero">' in body)
    check('the hooks survive Jinja',
          'class="m-stop-1"' in body and body.count('class="m-limb"') == 2)
    check('the palette reaches the browser',
          all(("%s:" % c) in body for c in BRIEFED))
    check('no unrendered Jinja left in the picker',
          '{{' not in body[body.index("var P = {"):body.index('})();')])

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
