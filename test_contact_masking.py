"""Masking email addresses and phone numbers on public surfaces.

Two risks pull in opposite directions, and the suite is weighted accordingly.

Under-masking leaves a scrapable address on an indexed page. Over-masking is
the louder failure: this codebase renders money ("$4,500/mo"), year ranges
("2019-2024"), cohort sizes ("10-15 clients"), version numbers and percentages
through the very same fields, and a greedy phone pattern would corrupt all of
them. So roughly half of what follows asserts that ordinary numbers are left
alone.

The streaming masker gets the most attention because it carries the real
logic risk: the model emits "jean@" and "example.com" as separate tokens, so
anything that masks chunk-by-chunk silently fails to match.

Against in-memory SQLite; no network.

    python test_contact_masking.py
"""
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


EMAIL = 'jean.delphonse@example.com'
PHONE = '+1 (555) 123-4567'


def main():
    from app.services import contact_masking as cm

    def m(text, **kw):
        kw.setdefault('force', True)
        return cm.mask_text(text, **kw)

    # ── Emails ──────────────────────────────────────────────────────────────
    section('Emails are masked')
    for raw, why in (
        ('Reach me at %s anytime.' % EMAIL, 'in prose'),
        ('Email: JEAN@EXAMPLE.CO.UK', 'upper case'),
        ('mailto:%s' % EMAIL, 'a mailto scheme'),
        ('a+tag@sub.domain.io', 'a plus tag and subdomain'),
        ('contact  <jean@example.com>', 'inside angle brackets'),
        ('two: a@x.com and b@y.org', 'two in one string'),
    ):
        out = m(raw)
        check('masks %s' % why, '@' in out and EMAIL not in out
              and 'jean@example.com' not in out and 'a@x.com' not in out, out)

    check('the local part keeps only its first letter',
          m(EMAIL) == 'j•••@•••.com', m(EMAIL))
    check('the TLD is lower-cased so the mask reads consistently',
          m('Jean@Example.COM').endswith('.com'), m('Jean@Example.COM'))
    check('mailto: goes with the address, leaving no dangling scheme',
          'mailto:' not in m('mailto:%s' % EMAIL), m('mailto:%s' % EMAIL))
    check('both addresses in one string are masked',
          'x.com' not in m('a@x.com and b@y.org').replace('•••.com', ''),
          m('a@x.com and b@y.org'))

    # ── Phones ──────────────────────────────────────────────────────────────
    section('Phone numbers are masked')
    for raw, why in (
        ('Call %s to book.' % PHONE, 'international with parens'),
        ('Mobile: +90 532 123 45 67', 'a Turkish mobile'),
        ('Tel 555-123-4567', 'hyphen groups'),
        ('Phone 555.123.4567', 'dot groups'),
        ('Ring (0212) 123 45 67 for the office', 'a parenthesised area code'),
        ('Direct: +442071234567', 'an unspaced international number'),
        ('reach 0532 123 45 67 now', 'space groups'),
    ):
        out = m(raw)
        check('masks %s' % why, out != raw and '•' in out, out)

    check('only the last two digits survive',
          m('Tel 555-123-4567') == 'Tel ••••••67',
          m('Tel 555-123-4567'))

    # ── The over-masking risk ───────────────────────────────────────────────
    section('Ordinary numbers are left completely alone')
    for raw in (
        'Rate card starts at $4,500/mo for retainers.',
        'Worked there 2019-2024 building the practice.',
        'Cohort size 10-15 clients per program.',
        'Achieved 35% margin improvement.',
        'Launch in 12 weeks, 3 phases.',
        'Revenue grew from $250,000 to $1,200,000.',
        'Section 401(k) planning and 529 plans.',
        'ISO 9001-2015 certified since 2018.',
        'Version 3.2.1 of the framework.',
        'Scores of 4.8 out of 5.0 across 120 reviews.',
        'Serving 1,500 customers in 20 markets.',
        'Between 2020 and 2026 revenue tripled.',
        'Available 9-5 Monday to Friday.',
        'Budget of 50,000 EUR approved.',
        'Invoice 2024-01-15 was settled.',
        '$695 per simulation.',
        'A 3x return over 18 months.',
        '2026-09-29 was the launch date.',
        'Suite 200, 1234 Market Street',
        'Grew the team from 5 to 40 people.',
    ):
        check('leaves alone: %s' % raw, m(raw) == raw, m(raw))

    # ── Styles and the switch ───────────────────────────────────────────────
    section('Styles and the master switch')
    check('hidden style replaces the address outright',
          m('at %s ok' % EMAIL, style='hidden') == 'at [email hidden] ok',
          m('at %s ok' % EMAIL, style='hidden'))
    check('hidden style replaces the number outright',
          '[phone hidden]' in m('Tel 555-123-4567', style='hidden'),
          m('Tel 555-123-4567', style='hidden'))
    check('non-strings pass straight through', m(None) is None and m(42) == 42)
    check('empty strings pass straight through', m('') == '')

    from app import create_app
    from app.extensions import db

    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()
        from app.models.platform_settings import PlatformSetting

        check('masking is on by default', cm.is_enabled())
        check('the default style is partial', cm.setting('bio_mask_style') == 'partial')

        PlatformSetting.set('bio_mask_contacts', 'false')
        db.session.commit()
        check('the switch turns it off', not cm.is_enabled())
        check('and text passes through untouched',
              cm.mask_text('at %s' % EMAIL) == 'at %s' % EMAIL)
        check('mask_deep also respects the switch',
              cm.mask_deep({'a': EMAIL})['a'] == EMAIL)
        PlatformSetting.set('bio_mask_contacts', 'true')
        db.session.commit()
        check('and back on again', cm.is_enabled())
        check('every setting is declared with a label and help',
              all(len(spec) == 4 for spec in cm.SETTINGS_SPEC.values()))

    # ── Walking a nested structure ──────────────────────────────────────────
    section('Nested structures are walked, not spot-checked')
    ctx = {
        'about_text': 'Mail me at %s.' % EMAIL,
        'positioning': 'Call %s' % PHONE,
        'booking_url': 'https://cal.com/jean',
        'linkedin_url': 'https://linkedin.com/in/jean',
        'avatar_path': 'jean-1234-5678.png',
        'sim_bios': [
            {'label': 'Consulting', 'narrative': 'Reach %s' % EMAIL,
             'services': ['Audit', 'Ping %s' % EMAIL],
             'layers': [{'ai_narrative': 'Call 555-123-4567 to start'}]},
        ],
        'career_history': [{'role': 'CFO', 'notes': EMAIL}],
        'view_count': 12,
        'nested': {'deep': {'deeper': EMAIL}},
    }
    out = cm.mask_deep(ctx, force=True)
    blob = repr(out)
    check('no address survives anywhere in the tree', EMAIL not in blob, blob[:200])
    check('no phone survives anywhere in the tree', '555-123-4567' not in blob)
    check('a list inside a list inside a dict is reached',
          EMAIL not in repr(out['sim_bios'][0]['services']))
    check('arbitrary depth is reached', EMAIL not in repr(out['nested']))
    check('the booking URL is untouched — it is the front door',
          out['booking_url'] == ctx['booking_url'])
    check('social URLs are untouched', out['linkedin_url'] == ctx['linkedin_url'])
    check('an avatar filename with digits is untouched',
          out['avatar_path'] == 'jean-1234-5678.png', out['avatar_path'])
    check('non-string values survive', out['view_count'] == 12)
    check('the input is not mutated', ctx['about_text'] == 'Mail me at %s.' % EMAIL)

    # ── The streaming masker ────────────────────────────────────────────────
    section('Streaming — a match split across chunks still gets masked')

    def run_stream(chunks, cap=320):
        masker = cm.StreamMasker(style='partial', cap=cap)
        out = ''
        for c in chunks:
            out += masker.feed(c)
        out += masker.flush()
        return out

    # This is the case that defeats naive per-chunk masking.
    out = run_stream(['You can reach him at jean', '@', 'example', '.com', ' today.'])
    check('an address split over four chunks is masked',
          'jean@example.com' not in out and '•' in out, out)
    check('and the surrounding sentence survives intact',
          out.startswith('You can reach him at') and out.endswith(' today.'), out)

    out = run_stream(['His number is ', '555', '-', '123', '-', '4567', '. Call him.'])
    check('a phone split over five chunks is masked',
          '555-123-4567' not in out and '•' in out, out)

    out = run_stream(list('Mail jean@example.com now.'))
    check('one character at a time still masks',
          'jean@example.com' not in out, out)

    out = run_stream(['Hello there, nothing sensitive here at all.'])
    check('plain text passes through unchanged',
          out == 'Hello there, nothing sensitive here at all.', out)

    out = run_stream(['No trailing punctuation so nothing flushes early'])
    check('text with no boundary is still released on flush',
          out == 'No trailing punctuation so nothing flushes early', out)

    # Nothing may be silently dropped: the masker holds text back, and every
    # held character has to come out of flush().
    plain = 'One. Two! Three? Four, five; six: seven\nand eight.'
    check('no text is lost across feed/flush', run_stream([plain]) == plain,
          run_stream([plain]))
    check('no text is lost when fed one char at a time',
          run_stream(list(plain)) == plain, run_stream(list(plain)))

    # A long unpunctuated run must still release progressively rather than
    # buffering the entire reply.
    long_run = 'word ' * 200
    masker = cm.StreamMasker(style='partial', cap=100)
    released = masker.feed(long_run)
    check('a long unpunctuated run releases before the end',
          len(released) > 0, len(released))
    check('and the hard-cap release plus flush loses nothing',
          released + masker.flush() == long_run)

    # The cap must not sever a spaced phone number.
    masker = cm.StreamMasker(style='partial', cap=40)
    acc = ''
    for ch in 'x' * 60 + ' call +90 532 123 45 67 ok':
        acc += masker.feed(ch)
    acc += masker.flush()
    check('the hard cap does not sever a spaced phone number',
          '532 123 45 67' not in acc and '•' in acc, acc[-40:])

    out = run_stream(['Reply to a@b.com or c@d.org, either works.'])
    check('two addresses in one streamed sentence are both masked',
          'a@b.com' not in out and 'c@d.org' not in out, out)

    # ── The public surfaces ─────────────────────────────────────────────────
    section('The bio page')
    with app.app_context():
        from app.extensions import bcrypt
        from app.models.bio_page import BioPage
        from app.models.profile import UserProfile
        from app.models.user import User
        from utils.id_gen import generate_id
        from datetime import datetime

        u = User(id=generate_id(), email='owner@t.local', full_name='Jean Delphonse',
                 password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                 email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add(u)
        db.session.flush()
        db.session.add(UserProfile(
            id=generate_id(), user_id=u.id, username='jean',
            display_name='Jean Delphonse',
            tagline='Ops turnarounds — call %s' % PHONE,
            bio='I help teams ship. Email %s or call 555-123-4567.' % EMAIL,
            is_published=True,
        ))
        bp = BioPage(id=generate_id(), user_id=u.id, slug='jean',
                     status=BioPage.STATUS_PUBLISHED)
        bp.custom_testimonials = [{
            'quote': 'Superb work — reach me at ref@client.com',
            'name': 'Sam Referee', 'title': 'CFO', 'company': 'Acme', 'order': 1,
        }]
        db.session.add(bp)
        db.session.commit()
        owner_email = u.email

    client = app.test_client()
    page = client.get('/u/jean').get_data(as_text=True)

    check('the page renders', 'Jean Delphonse' in page)
    check('the bio email is masked in the HTML', EMAIL not in page)
    check('the bio phone is masked in the HTML', '555-123-4567' not in page)
    check('the tagline phone is masked', '(555) 123-4567' not in page)
    check('a testimonial email is masked', 'ref@client.com' not in page)
    check('a mask is actually visible on the page', '•••' in page)
    check('the SEO meta description carries no address',
          '<meta name="description"' in page and EMAIL not in page)
    check('the real content around it survives', 'I help teams ship' in page)

    body = client.get('/u/jean/bio-page.json').get_json()
    check('the public JSON endpoint masks too', EMAIL not in repr(body['context']),
          repr(body['context'])[:160])
    check('and masks the phone', '555-123-4567' not in repr(body['context']))

    # ── The owner's editor must NOT be masked ───────────────────────────────
    # This is the guard that matters: hand the owner a masked copy of their own
    # bio and they will eventually save the mask over their real text.
    section("The owner's own editor is not masked")
    client.post('/api/auth/login',
                json={'email': owner_email, 'password': 'pw12345678'})
    res = client.get('/api/bio')
    check('the editor endpoint answers', res.status_code == 200, res.status_code)
    editor = repr(res.get_json())
    check('the owner still sees their real email', EMAIL in editor)
    check('the owner still sees their real phone',
          '555-123-4567' in editor or '(555) 123-4567' in editor)

    # ── Ask Simi ────────────────────────────────────────────────────────────
    section('Ask Simi')
    with app.app_context():
        from app.models.bio_page import BioPage
        from app.blueprints.bio_chat.routes import _assemble_system_prompt
        bp = BioPage.query.filter_by(slug='jean').first()
        prompt = _assemble_system_prompt(bp)

    check('the system prompt carries no address', EMAIL not in prompt)
    check('the system prompt carries no phone number',
          '555-123-4567' not in prompt and '(555) 123-4567' not in prompt)
    check('the prompt still describes the person',
          'Jean' in prompt and len(prompt) > 200)

    import io as _io
    src = _io.open('app/blueprints/bio_chat/routes.py', encoding='utf8').read()
    check('the reply stream is masked, not just the prompt',
          'StreamMasker' in src and 'payload = emit(text)' in src)
    check('both streaming passes go through the masker',
          src.count('payload = emit(text)') == 2, src.count('payload = emit(text)'))
    check('the held tail is flushed before the done event',
          src.index('tail = drain()') < src.index('"done": True'))
    check('the stored transcript is masked to match what the visitor saw',
          'full_text = mask_text(full_text)' in src)

    # ── The owner's in-app copilot is a different surface ───────────────────
    # Simi inside the app answers the owner about their own CRM and has tools
    # that return contact details on purpose. Masking there would break it.
    section("The owner's in-app copilot is untouched")
    simi_src = _io.open('app/services/simi_service.py', encoding='utf8').read()
    check('simi_service does not import the masker',
          'contact_masking' not in simi_src)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
