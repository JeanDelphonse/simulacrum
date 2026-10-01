"""Mask email addresses and phone numbers on public surfaces.

A bio page is a public, indexed document, and the Ask Simi widget answers
strangers. Contact details reach both by accident rather than by intent: a rate
card artifact ends with a signature block, a career-history entry carries an old
work address, an imported LinkedIn "About" has a mobile number in it. Once
rendered, that is a scrapable address on an indexed page.

The owner's *deliberate* channels are untouched — the contact form, the booking
link, the social URLs. Those are the front door. This module closes the windows.

The hard part here is not finding phone numbers, it is not finding things that
merely look like them. This codebase renders money ("$4,500/mo"), years
("2019-2024"), ranges ("10-15 clients"), percentages and week counts all over
the same fields. A loose phone pattern would corrupt every one of them, which
would be a far more visible failure than the leak it prevents. So phone matching
is deliberately conservative and requires positive evidence of being a phone
number: an international prefix, a parenthesised area code, or a properly
grouped 7-15 digit run — and never a bare integer or a decimal.
"""
from __future__ import annotations

import re

# ── Settings ────────────────────────────────────────────────────────────────

SETTINGS_SPEC = {
    'bio_mask_contacts': (
        'bool', True, 'Mask contact details on public pages',
        'Masks email addresses and phone numbers found in bio page text and in '
        'Ask Simi replies. The contact form, booking link and social URLs are '
        'never affected — only contact details that appear inside prose.'),
    'bio_mask_style': (
        'str', 'partial', 'Masking style',
        'partial keeps a hint of the shape ("j•••@•••.com", '
        '"•••-•••-4567") so a reader understands what was '
        'there; hidden replaces it outright with "[email hidden]" / '
        '"[phone hidden]".'),
}

_TRUE = ('1', 'true', 'yes', 'on', 'True')

DOT = '•'   # bullet, used as the mask character


def setting(key):
    """Read one masking setting, coerced to its declared type."""
    from app.models.platform_settings import PlatformSetting

    kind, default = SETTINGS_SPEC[key][0], SETTINGS_SPEC[key][1]
    try:
        raw = PlatformSetting.get(key)
    except Exception:
        return default
    if raw is None or raw == '':
        return default
    if kind == 'bool':
        return str(raw) in _TRUE
    return str(raw)


def all_settings() -> list:
    return [
        {'key': key, 'type': spec[0], 'value': setting(key),
         'default': spec[1], 'label': spec[2], 'help': spec[3]}
        for key, spec in SETTINGS_SPEC.items()
    ]


def is_enabled() -> bool:
    return bool(setting('bio_mask_contacts'))


# ── Patterns ────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'
)

# A `mailto:` link carries the address twice — in the href and in the label.
MAILTO_RE = re.compile(r'mailto:\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})',
                       re.I)

# Three shapes, each requiring positive evidence of being a phone number.
# Ordered longest-first so a fuller match wins over a subset of itself.
PHONE_RE = re.compile(
    r'(?<![\w.])('
    # +90 532 123 45 67 / +1-555-123-4567 / +442071234567
    r'\+\d[\d\s().\-]{6,18}\d'
    # (555) 123-4567 / (0212) 123 45 67
    r'|\(\d{2,5}\)[\s.\-]*\d[\d\s.\-]{5,14}\d'
    # 555-123-4567 / 555.123.4567 / 0532 123 45 67 — at least two separators,
    # so "10-15" and "2019-2024" cannot match.
    r'|\d{2,5}[\s.\-]\d{2,4}[\s.\-]\d{2,4}(?:[\s.\-]\d{2,4})?'
    # Trailing guard rejects a word character, and a dot only when a digit
    # follows it — so "3.2.1.5" cannot be entered mid-number, while
    # "call 555-123-4567." at the end of a sentence still matches. An earlier
    # `(?![\w.])` here silently skipped every phone number that ended a
    # sentence, which is most of them.
    r')(?!\w)(?!\.\d)'
)

# Rejected after a phone-shaped match, by looking at what surrounds it.
_CURRENCY_BEFORE = re.compile(r'[$€£¥]\s*$')
_UNIT_AFTER = re.compile(r'^\s*(?:%|k\b|m\b|mo\b|hr\b|hrs\b|/|usd\b|eur\b|gbp\b)', re.I)
# An ISO-ish date is grouped exactly like a phone number.
_DATE_LIKE = re.compile(r'^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$')

PHONE_MIN_DIGITS = 7
PHONE_MAX_DIGITS = 15


def _looks_like_phone(match, text: str) -> bool:
    """Second-stage filter: does the surrounding text argue against it?"""
    token = match.group(1)
    digits = [c for c in token if c.isdigit()]
    if not PHONE_MIN_DIGITS <= len(digits) <= PHONE_MAX_DIGITS:
        return False
    if _DATE_LIKE.match(token.strip()):
        return False
    if _CURRENCY_BEFORE.search(text[max(0, match.start() - 3):match.start()]):
        return False
    if _UNIT_AFTER.match(text[match.end():match.end() + 5]):
        return False
    # A run of digits with no separator at all is an id, an amount or a year —
    # never treated as a phone unless it carried an international prefix.
    if not token.startswith('+') and not any(c in token for c in ' .-()'):
        return False
    return True


# ── Masking ─────────────────────────────────────────────────────────────────

def mask_email(address: str, style: str = 'partial') -> str:
    if style == 'hidden':
        return '[email hidden]'
    local, _, domain = address.partition('@')
    # Lower-cased: the mask should read the same whether the source wrote
    # "Jean@Example.COM" or "jean@example.com".
    tld = domain.rsplit('.', 1)[-1].lower() if '.' in domain else ''
    head = local[0] if local else ''
    return '%s%s@%s.%s' % (head, DOT * 3, DOT * 3, tld) if tld else \
           '%s%s@%s' % (head, DOT * 3, DOT * 3)


def mask_phone(number: str, style: str = 'partial') -> str:
    if style == 'hidden':
        return '[phone hidden]'
    digits = [c for c in number if c.isdigit()]
    # Keep the last two digits only. Enough for someone who already knows the
    # number to recognise it, useless to a scraper.
    return '%s%s' % (DOT * 6, ''.join(digits[-2:]))


def mask_text(text, style: str | None = None, force: bool = False):
    """Return `text` with email addresses and phone numbers masked.

    Returns the input unchanged when masking is switched off, and passes
    non-strings straight through so callers can hand it any field value.
    `force=True` skips the settings lookup, for callers that have already
    decided (and for tests).
    """
    if not isinstance(text, str) or not text:
        return text
    if not force and not is_enabled():
        return text
    if style is None:
        style = setting('bio_mask_style') if not force else 'partial'

    # mailto: first, so the scheme goes with the address rather than being left
    # as a dangling "mailto:" in front of a mask.
    out = MAILTO_RE.sub(lambda m: mask_email(m.group(1), style), text)
    out = EMAIL_RE.sub(lambda m: mask_email(m.group(0), style), out)

    def phone_sub(match):
        if not _looks_like_phone(match, out):
            return match.group(0)
        return mask_phone(match.group(1), style)

    return PHONE_RE.sub(phone_sub, out)


def mask_deep(value, style: str | None = None, force: bool = False):
    """Mask every string inside a nested dict/list, in place of its value.

    The bio page context is a tree of dicts and lists assembled from a dozen
    sources, so masking it field by field at each call site would guarantee one
    gets forgotten the next time a section is added. Walking the whole structure
    once, right before it is handed to the template, is the only version of
    this that stays correct.

    Keys whose values are deliberate contact channels are skipped — see
    SKIP_KEYS.
    """
    if not force and not is_enabled():
        return value
    if style is None:
        style = setting('bio_mask_style') if not force else 'partial'
    return _walk(value, style)


# The owner's intended channels, and machine-readable fields a mask would
# break. A booking URL or an avatar path is not a leak, it is the front door.
SKIP_KEYS = frozenset((
    'booking_url', 'cta_url', 'linkedin_url', 'twitter_url', 'website_url',
    'other_link_url', 'avatar_path', 'hero_image_url', 'qr_code_url',
    'stream_url', 'thumbnail_url', 'slug', 'id', 'simulation_id',
))


def _walk(value, style):
    if isinstance(value, str):
        return mask_text(value, style=style, force=True)
    if isinstance(value, dict):
        return {
            k: (v if k in SKIP_KEYS else _walk(v, style))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_walk(v, style) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v, style) for v in value)
    return value


# ── Streaming ───────────────────────────────────────────────────────────────

# The longest contact token worth holding back for. An email or phone longer
# than this is not a realistic address.
_HOLD = 72


class StreamMasker(object):
    """Masks a token-by-token stream without splitting a match in half.

    Masking each streamed chunk on its own does not work: the model emits
    "jean@" and "example.com" as separate tokens, and neither half matches on
    its own. So text is released only once it cannot still be the start of a
    contact detail.

    Release happens at a boundary that can never occur *inside* an email or a
    phone number — a newline, or punctuation followed by a space. When no such
    boundary arrives, a hard cap releases everything except a trailing window of
    `_HOLD` characters, cut back to a space so a grouped phone number is not
    severed.
    """

    __slots__ = ('_buffer', 'style', '_cap')

    _BOUNDARY = re.compile(r'(?:[.!?,;:]\s|\n)(?!\S*@)')

    def __init__(self, style: str | None = None, cap: int = 320):
        if style is None:
            try:
                style = setting('bio_mask_style')
            except Exception:
                style = 'partial'
        self.style = style
        self._buffer = ''
        self._cap = cap

    def feed(self, chunk: str) -> str:
        """Take a streamed chunk; return the text that is safe to send now."""
        if chunk:
            self._buffer += chunk

        release_at = -1
        for match in self._BOUNDARY.finditer(self._buffer):
            release_at = match.end()

        if release_at <= 0 and len(self._buffer) > self._cap:
            # No natural boundary in a long run — release all but a tail window,
            # backed off to a space so a "(555) 123 4567" is not cut in two.
            cut = len(self._buffer) - _HOLD
            space = self._buffer.rfind(' ', 0, cut)
            release_at = space + 1 if space > 0 else 0

        if release_at <= 0:
            return ''

        head, self._buffer = self._buffer[:release_at], self._buffer[release_at:]
        return mask_text(head, style=self.style, force=True)

    def flush(self) -> str:
        """Mask and return whatever is left. Call once the stream has ended."""
        remainder, self._buffer = self._buffer, ''
        if not remainder:
            return ''
        return mask_text(remainder, style=self.style, force=True)
