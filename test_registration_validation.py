"""Registration input validation.

Written around the signup that got through:

    ?70k TL bonusla kazanmak cok kolay! https://bit.ly/4xwFNFb ?

The two failure modes worth guarding are opposite in direction. Letting that
string through puts an advert on a public bio page. But over-correcting is worse
than it looks: a validator built from `[A-Za-z]` would reject "Ayşe Yılmaz",
"李明" and "Владимир Ковалёв", which is a far larger group than the spammers.
So the accept list here is as long as the reject list on purpose.

Against in-memory SQLite; no network.

    python test_registration_validation.py
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


# The exact name from the spam registration.
SPAM_NAME = '?70k TL bonusla kazanmak cok kolay! https://bit.ly/4xwFNFb ?'


def main():
    from utils.validators import (
        ValidationError, validate_email, validate_full_name,
    )

    def name_ok(value):
        try:
            return True, validate_full_name(value)
        except ValidationError as exc:
            return False, str(exc)

    def email_ok(value, **kw):
        try:
            return True, validate_email(value, **kw)
        except ValidationError as exc:
            return False, str(exc)

    # ── The reported string ─────────────────────────────────────────────────
    section('The signup that got through')
    accepted, detail = name_ok(SPAM_NAME)
    check('the exact spam name is rejected', not accepted, detail)
    check('the message names the actual problem',
          'web address' in detail or 'numbers' in detail, detail)

    # ── Names that must be rejected ─────────────────────────────────────────
    section('Rejected names')
    for value, why in (
        ('https://bit.ly/4xwFNFb', 'a bare URL'),
        ('Win money at casino.com', 'a bare host.tld'),
        ('Visit WWW.SPAM.RU now', 'an upper-case www host'),
        ('ｈｔｔｐｓ://ｅｖｉｌ.ｃｏｍ', 'a fullwidth URL that NFKC folds'),
        ('spam@spammer.com', 'an email address'),
        ('Jane 70k Smith', 'digits'),
        ('Jane Smith!!!', 'sentence punctuation'),
        ('Jane 😀 Smith', 'an emoji'),
        ('....', 'punctuation only'),
        ('aaaaaaaaaa', 'a long repeated run'),
        ('One two three four five six seven', 'seven words'),
        ('a', 'a single character'),
        ('x' * 101, 'over 100 characters'),
        ('', 'an empty string'),
        ('   ', 'whitespace only'),
        (None, 'a missing value'),
        ('Jane​Smith.ru', 'a zero-width char hiding a host'),
        ('<script>alert(1)</script>', 'markup'),
        ('Jane <b>Smith</b>', 'inline tags'),
    ):
        accepted, detail = name_ok(value)
        check('rejects ' + why, not accepted,
              detail if not accepted else 'ACCEPTED as %r' % (detail,))

    # ── Names that must be accepted ─────────────────────────────────────────
    # This half of the suite is the one that matters for not breaking signups.
    section('Accepted names — every script, not just ASCII')
    for value, who in (
        ('Jane Smith', 'a plain name'),
        ('Jean M. Delphonse', 'a middle initial'),
        ("Seamus O'Brien", 'an apostrophe'),
        ("O’Brien", 'a typographic apostrophe'),
        ('Ana Sofía Muñoz-Pérez', 'accents and a hyphen'),
        ('Ayşe Yılmaz', 'Turkish'),
        ('Владимир Ковалёв', 'Cyrillic'),
        ('李明', 'Chinese'),
        ('山田 太郎', 'Japanese with an ideographic space'),
        ('김지연', 'Korean'),
        ('محمد عبد الله', 'Arabic'),
        ('אברהם כהן', 'Hebrew'),
        ('Ἀλέξανδρος Παπαδόπουλος', 'Greek'),
        ('नरेन्द्र मोदी', 'Devanagari with combining marks'),
        ('Nguyễn Thị Minh Khai', 'Vietnamese diacritics'),
        ('Ólafur Þórðarson', 'Icelandic'),
        ('Renée Zellweger', 'a diaeresis'),
        ('Jan de Vries', 'a lowercase particle'),
        ('Mary-Jane Watson Jr.', 'a hyphen and a suffix'),
        ('Smith, III', 'a comma and a numeral written in letters'),
        ('Jo', 'a two-letter name'),
    ):
        accepted, detail = name_ok(value)
        check('accepts %s (%s)' % (who, value), accepted, detail)

    section('Names are cleaned, not just judged')
    accepted, cleaned = name_ok('  Jane   Smith  ')
    check('whitespace is collapsed and trimmed',
          accepted and cleaned == 'Jane Smith', repr(cleaned))
    accepted, cleaned = name_ok('Jane\tSmith\n')
    check('tabs and newlines are normalised',
          accepted and cleaned == 'Jane Smith', repr(cleaned))
    check('the returned value is what a caller should store',
          name_ok('Jane​ Smith')[1] == 'Jane Smith',
          repr(name_ok('Jane​ Smith')[1]))

    # ── Email ───────────────────────────────────────────────────────────────
    section('Rejected emails')
    for value, why in (
        ('not-an-email', 'no @'),
        ('two@@at.com', 'a double @'),
        ('a@b', 'no TLD'),
        ('a@b.c', 'a one-character TLD'),
        ('@nolocal.com', 'no local part'),
        ('.leading@dot.com', 'a leading dot'),
        ('trailing.@dot.com', 'a trailing dot'),
        ('double..dot@x.com', 'consecutive dots'),
        ('has space@x.com', 'a space'),
        ('a@-baddomain.com', 'a leading hyphen in the domain'),
        ('a@baddomain-.com', 'a trailing hyphen in the domain'),
        ('x' * 65 + '@x.com', 'a local part over 64 chars'),
        ('a@' + 'x' * 260 + '.com', 'an address over 254 chars'),
        ('', 'an empty string'),
        (None, 'a missing value'),
        ('spam@mailinator.com', 'a known throwaway inbox'),
    ):
        accepted, detail = email_ok(value)
        check('rejects ' + why, not accepted,
              detail if not accepted else 'ACCEPTED as %r' % (detail,))

    section('Accepted emails')
    for value in (
        'jane@example.com',
        'jane.smith@example.co.uk',
        'jane+tag@example.com',
        "o'brien@example.com",
        'jane_smith@sub.example.com',
        'a@b.io',
        'JANE@EXAMPLE.COM',
    ):
        accepted, detail = email_ok(value)
        check('accepts ' + value, accepted, detail)

    accepted, cleaned = email_ok('  JANE@Example.COM  ')
    check('the address is trimmed and lower-cased',
          accepted and cleaned == 'jane@example.com', repr(cleaned))
    check('a throwaway domain is allowed when the caller opts in',
          email_ok('spam@mailinator.com', allow_disposable=True)[0])

    # ── Registration end to end ─────────────────────────────────────────────
    section('The register endpoint')
    from app import create_app
    from app.extensions import db

    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      WTF_CSRF_ENABLED=False)
    with app.app_context():
        db.create_all()

    client = app.test_client()

    def register(name, email, password='pw12345678'):
        return client.post('/api/auth/register', json={
            'full_name': name, 'email': email, 'password': password,
        })

    from app.models.user import User

    res = register(SPAM_NAME, 'spammer@example.com')
    check('the spam registration is refused', res.status_code == 400,
          res.status_code)
    check('with a message the person can act on',
          'error' in (res.get_json() or {}), res.get_json())
    with app.app_context():
        check('and no user row was created', User.query.count() == 0,
              User.query.count())

    res = register('Jane Smith', 'not-an-email')
    check('a malformed email is refused', res.status_code == 400, res.status_code)
    with app.app_context():
        check('still no user row', User.query.count() == 0)

    res = register('Jane Smith', 'jane@mailinator.com')
    check('a throwaway inbox is refused at signup', res.status_code == 400,
          (res.get_json() or {}).get('error'))

    res = register('Ayşe Yılmaz', 'ayse@example.com')
    check('a Turkish name registers fine', res.status_code == 201, res.status_code)
    with app.app_context():
        u = User.query.filter_by(email='ayse@example.com').first()
        check('the name is stored intact', u is not None and u.full_name == 'Ayşe Yılmaz',
              u.full_name if u else None)

    res = register('  Jean   Delphonse ', '  JEAN@Example.COM ')
    check('a messy but valid signup is accepted and cleaned',
          res.status_code == 201, res.status_code)
    with app.app_context():
        u = User.query.filter_by(email='jean@example.com').first()
        check('the email was normalised before storage', u is not None,
              [x.email for x in User.query.all()])
        check('the name was normalised before storage',
              u is not None and u.full_name == 'Jean Delphonse',
              u.full_name if u else None)

    res = register('Jean Delphonse', 'JEAN@example.com')
    check('normalisation makes the duplicate check case-insensitive',
          res.status_code == 409, res.status_code)

    res = client.post('/api/auth/register', json={'full_name': 'Jane Smith'})
    check('missing fields are still refused', res.status_code == 400)

    # ── The other public forms with the same vector ─────────────────────────
    section('The same check on the other public forms')
    # A valid partner_type, so the name check is what decides rather than the
    # type check short-circuiting ahead of it.
    from app.models.partner import ReferralPartner
    valid_type = ReferralPartner.PARTNER_TYPES[0]

    res = client.post('/api/partners/apply', json={
        'full_name': SPAM_NAME, 'email': 'x@example.com',
        'partner_type': valid_type,
    })
    body = res.get_json() or {}
    check('the partner application refuses the spam name',
          res.status_code == 400, '%s %s' % (res.status_code, body))
    check('and refuses it for the name, not something else',
          'name' in (body.get('error') or '').lower(), body.get('error'))

    res = client.post('/api/partners/apply', json={
        'full_name': 'Jane Smith', 'email': 'not-an-email',
        'partner_type': valid_type,
    })
    check('and refuses a malformed email', res.status_code == 400,
          (res.get_json() or {}).get('error'))

    res = client.post('/api/partners/apply', json={
        'full_name': 'Ayşe Yılmaz', 'email': 'ayse.partner@example.com',
        'partner_type': valid_type,
    })
    check('while a legitimate application still goes through',
          res.status_code in (200, 201), '%s %s' % (res.status_code, res.get_json()))

    # ── The register page ───────────────────────────────────────────────────
    section('The register page')
    page = client.get('/register').get_data(as_text=True)
    check('the name field is length-bounded in markup',
          'maxlength="100"' in page and 'minlength="2"' in page)
    check('the email field is length-bounded too', 'maxlength="254"' in page)
    check('the page validates before it posts', 'function nameProblem' in page
          and 'function emailProblem' in page)
    check('submit is gated on the client check',
          page.index('function nameProblem') < page.index("addEventListener('submit'"))
    check('the client check uses Unicode letter classes, not [A-Za-z]',
          '\\p{L}' in page and '[A-Za-z]' not in page.split('function nameProblem')[1][:1200])
    # Anchored on behaviour, not prose: the property-escape regexes are built
    # inside a try, and a browser that cannot compile them defers to the server
    # rather than falling back to an ASCII-only rule.
    guard = page.split('var letterRe, markRe;')[1][:420] if 'var letterRe, markRe;' in page else ''
    check('it degrades gracefully on browsers without property escapes',
          'try {' in guard and 'catch' in guard and 'return null' in guard,
          guard[:160])
    check('the invisible-character strip avoids escape sequences entirely',
          'function stripInvisible' in page and 'charCodeAt' in page)

    print('\n{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
