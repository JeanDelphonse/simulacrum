"""Input validation for the fields the public can write.

Registration accepted anything at all, which is how a signup landed with the
full name:

    ?70k TL bonusla kazanmak cok kolay! https://bit.ly/4xwFNFb ?

That name is not a typo, it is an advert — and `full_name` is rendered on the
bio page, in outreach emails and in the admin console, so an unvalidated name is
a content-injection surface with a distribution channel attached.

The rules here are structural, not a keyword blocklist. A blocklist of spam
phrases goes stale, is trivially evaded and is hostile to the languages it does
not know. What that string actually has in common with every other name advert
is shape: a URL, digits, sentence punctuation, and eight words. Real names have
none of those. So this module tests shape.

Unicode is a first-class concern, not an afterthought. Checks use
`str.isalpha()` and `unicodedata.category()` rather than `[A-Za-z]`, so
"Ayşe Yılmaz", "Владимир Ковалёв", "李明" and "Ana Sofía Muñoz-Pérez" all pass.
A validator that only accepts ASCII would reject most of the world's names,
which is a worse failure than the spam it prevents.
"""
from __future__ import annotations

import re
import unicodedata

# ── Full name ───────────────────────────────────────────────────────────────

NAME_MIN_LEN = 2
NAME_MAX_LEN = 100
NAME_MAX_WORDS = 6

# Punctuation a real name can legitimately carry: O'Brien, Muñoz-Pérez,
# "Jr.", "Smith, III", and the middle dot used in some Catalan and CJK names.
_NAME_PUNCT = set(" '’-‐‑‒–—.,()·・")

# Unicode categories that are fine inside a name: letters of every script,
# combining marks (Devanagari, Arabic, Vietnamese), and spacing.
_ALLOWED_CATEGORIES = ('Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Mn', 'Mc', 'Zs')

_URLISH = re.compile(
    r'(https?://|www\.|://'
    # A bare host.tld, which is how a link gets past a naive http:// check.
    r'|\b[a-z0-9-]{2,}\.(?:com|net|org|ru|cn|io|co|me|info|biz|xyz|top|live|'
    r'link|click|tk|ly|gl|cc|to|shop|site|online|store|vip|club|bet|casino)\b)',
    re.I,
)


class ValidationError(ValueError):
    """A rejection with a message safe to show the person who typed it."""


def validate_full_name(raw) -> str:
    """Return a cleaned full name, or raise ValidationError.

    Cleaning is deliberately part of validation rather than a separate step: the
    caller gets back the exact string that should be stored, so there is no way
    to validate one value and persist another.
    """
    if raw is None:
        raise ValidationError('Enter your full name.')

    # Normalise first. NFKC folds fullwidth and styled look-alikes (ｈｔｔｐ,
    # 𝐡𝐭𝐭𝐩) onto their plain forms, so the checks below cannot be dodged with
    # a different codepoint for the same glyph.
    name = unicodedata.normalize('NFKC', str(raw))
    # Whitespace controls become spaces *before* the control-character strip, so
    # "Jane\tSmith" ends up as "Jane Smith" rather than "JaneSmith".
    name = re.sub(r'[\t\n\r\v\f]+', ' ', name)
    # Strip zero-width and bidi control characters, which are invisible in the
    # console but break rendering and can hide text.
    name = ''.join(c for c in name if unicodedata.category(c) not in ('Cc', 'Cf'))
    name = re.sub(r'\s+', ' ', name).strip()

    if not name:
        raise ValidationError('Enter your full name.')
    if len(name) < NAME_MIN_LEN:
        raise ValidationError('That name is too short.')
    if len(name) > NAME_MAX_LEN:
        raise ValidationError('Please keep your name under %d characters.'
                              % NAME_MAX_LEN)

    if _URLISH.search(name):
        raise ValidationError('Your name cannot contain a web address.')
    if '@' in name:
        raise ValidationError('Your name cannot contain an email address.')
    if any(c.isdigit() for c in name):
        raise ValidationError('Your name cannot contain numbers.')

    for char in name:
        if char in _NAME_PUNCT:
            continue
        if unicodedata.category(char) in _ALLOWED_CATEGORIES:
            continue
        raise ValidationError(
            'Your name contains a character we cannot accept: %s' % char)

    letters = sum(1 for c in name if c.isalpha())
    if letters < 2:
        raise ValidationError('Please enter your real name.')

    # Mostly-punctuation strings pass the per-character check one symbol at a
    # time, so they get caught in aggregate here.
    #
    # Combining marks count as letter-like, not as decoration. In Devanagari,
    # Bengali, Tamil, Thai and vocalised Arabic the marks *are* the vowels: in
    # "नरेन्द्र मोदी" only 7 of 12 characters are isalpha(), and a naive
    # letters-over-length ratio rejects the name of a head of state. The test
    # is "is this linguistic content or is it punctuation", so marks belong on
    # the letter side of it.
    marks = sum(1 for c in name
                if unicodedata.category(c) in ('Mn', 'Mc', 'Me'))
    body = len(name.replace(' ', ''))
    if body and (letters + marks) < body * 0.6:
        raise ValidationError('Please enter your real name.')

    words = name.split(' ')
    if len(words) > NAME_MAX_WORDS:
        raise ValidationError('That looks like a sentence rather than a name.')

    # "aaaaaaaa" / "........" — a run this long is never a name.
    if re.search(r'(.)\1{4,}', name):
        raise ValidationError('Please enter your real name.')

    return name


# ── Email ───────────────────────────────────────────────────────────────────

EMAIL_MAX_LEN = 254        # RFC 5321 path limit
EMAIL_LOCAL_MAX_LEN = 64

# Deliberately not an RFC 5322 implementation. A fully compliant regex accepts
# quoted strings, comments and bang paths that no mail provider will deliver to,
# so it is permissive where it costs us and no stricter where it matters. This
# matches what a real address looks like.
_EMAIL_RE = re.compile(
    r'^[A-Za-z0-9!#$%&\'*+/=?^_`{|}~-]+'
    r'(?:\.[A-Za-z0-9!#$%&\'*+/=?^_`{|}~-]+)*'
    r'@'
    r'(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+'
    r'[A-Za-z]{2,63}$'
)

# Throwaway-inbox providers. Short and uncontroversial on purpose, and
# overridable from platform_settings (`blocked_email_domains`, comma-separated)
# so a false positive can be cleared without a deploy. Blocking a real
# customer is a worse outcome than accepting one throwaway signup, so this list
# is meant to stay small.
DISPOSABLE_DOMAINS = frozenset((
    'mailinator.com', 'guerrillamail.com', 'guerrillamail.net', 'sharklasers.com',
    '10minutemail.com', 'tempmail.com', 'temp-mail.org', 'throwawaymail.com',
    'yopmail.com', 'trashmail.com', 'getnada.com', 'dispostable.com',
    'maildrop.cc', 'fakeinbox.com', 'mintemail.com', 'mailnesia.com',
))


def _blocked_domains() -> frozenset:
    """The disposable-domain list, with the admin override applied."""
    try:
        from app.models.platform_settings import PlatformSetting
        raw = PlatformSetting.get('blocked_email_domains')
    except Exception:
        return DISPOSABLE_DOMAINS
    if raw is None:
        return DISPOSABLE_DOMAINS
    # An explicitly empty setting means "block nothing" — the escape hatch.
    return frozenset(
        d.strip().lower() for d in str(raw).split(',') if d.strip()
    )


def validate_email(raw, allow_disposable: bool = False) -> str:
    """Return a normalised (trimmed, lower-cased) address, or raise.

    Normalisation happens here so that callers cannot store a different string
    from the one that was checked — which is also what keeps the uniqueness
    check on registration honest, since `Foo@x.com` and `foo@x.com` are the
    same mailbox.
    """
    if raw is None:
        raise ValidationError('Enter your email address.')

    email = unicodedata.normalize('NFKC', str(raw))
    email = ''.join(c for c in email if unicodedata.category(c) not in ('Cc', 'Cf'))
    email = email.strip().strip('<>').strip()

    if not email:
        raise ValidationError('Enter your email address.')
    if len(email) > EMAIL_MAX_LEN:
        raise ValidationError('That email address is too long.')
    if any(c.isspace() for c in email):
        raise ValidationError('An email address cannot contain spaces.')

    email = email.lower()

    if email.count('@') != 1:
        raise ValidationError('Enter a valid email address.')
    local, domain = email.split('@')

    if not local or len(local) > EMAIL_LOCAL_MAX_LEN:
        raise ValidationError('Enter a valid email address.')
    if '..' in local or '..' in domain:
        raise ValidationError('Enter a valid email address.')
    if local.startswith('.') or local.endswith('.'):
        raise ValidationError('Enter a valid email address.')
    if domain.startswith('-') or domain.endswith('-') or '.' not in domain:
        raise ValidationError('Enter a valid email address.')
    if not _EMAIL_RE.match(email):
        raise ValidationError('Enter a valid email address.')

    if not allow_disposable and domain in _blocked_domains():
        raise ValidationError(
            'Please use a permanent email address — we send your simulation '
            'results there.')

    return email
