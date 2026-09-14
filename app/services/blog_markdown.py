"""Markdown-subset renderer for community blog posts and comments.

PRD-SIM-BLOG-001 asks for "Markdown or basic rich text" with a strict allowlist.
Rather than pull in `markdown` plus a sanitizer (two more wheels on a Python 3.9
cPanel host, and a sanitizer is only ever as good as its blocklist), this module
inverts the problem: every character of author input is HTML-escaped first, and
only then is a small, fixed set of Markdown constructs turned back into tags.
Nothing an author types can become markup that is not on this list, so there is
no sanitizer to keep ahead of.

Supported: headings (#..###), unordered/ordered lists, blockquotes, fenced and
inline code, horizontal rules, bold, italic, links, bare-URL autolinking, and
paragraphs with soft line breaks.

Deliberately unsupported: raw HTML, images, tables, footnotes, nested lists.
"""
from __future__ import annotations

import re

# Only these schemes may reach an href. Anything else renders as literal text,
# which is what keeps javascript: and data: out without a blocklist.
# The bare "/" branch is deliberately "/ not followed by /": a protocol-relative
# "//evil.com" is an external link wearing an internal link's clothes.
_SAFE_SCHEME = re.compile(r'^(?:https?://|mailto:|/(?!/))', re.IGNORECASE)

_HEADING = re.compile(r'^(#{1,3})\s+(.*)$')
_ULI     = re.compile(r'^\s*[-*+]\s+(.*)$')
_OLI     = re.compile(r'^\s*\d{1,3}[.)]\s+(.*)$')
_QUOTE   = re.compile(r'^\s*>\s?(.*)$')
_HR      = re.compile(r'^\s*(?:-{3,}|\*{3,}|_{3,})\s*$')
_FENCE   = re.compile(r'^\s*```')

_INLINE_CODE = re.compile(r'`([^`\n]+)`')
_LINK        = re.compile(r'\[([^\]\n]{1,200})\]\(([^)\s]{1,500})\)')
_BOLD        = re.compile(r'\*\*(\S(?:[^*]*\S)?)\*\*')
_ITALIC      = re.compile(r'(?<![\w*])[*_](\S(?:[^*_]*\S)?)[*_](?![\w*])')
# Runs on already-escaped text, so it must not start inside an entity or a tag.
_BARE_URL    = re.compile(r'(?<![">\w])(https?://[^\s<>"\x27]{4,500})')

_TOKEN_RE = re.compile(r'\x00(\d+)\x00')


def _escape(text):
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;')
                .replace("'", '&#39;'))


def _inline_basic(escaped):
    """Emphasis only. Used for link labels, where nesting another link is meaningless."""
    escaped = _BOLD.sub(r'<strong>\1</strong>', escaped)
    escaped = _ITALIC.sub(r'<em>\1</em>', escaped)
    return escaped


def _inline(escaped):
    """Apply inline markup to text that is ALREADY HTML-escaped."""
    vault = []

    def stash(html):
        vault.append(html)
        return '\x00%d\x00' % (len(vault) - 1)

    # Code spans go first and are stashed, so their contents are never re-marked up.
    escaped = _INLINE_CODE.sub(lambda m: stash('<code>' + m.group(1) + '</code>'), escaped)

    def link(m):
        label, href = m.group(1), m.group(2)
        # href is escaped text; an &amp; inside a query string is valid in an href.
        if not _SAFE_SCHEME.match(href.replace('&amp;', '&')):
            return m.group(0)
        target = '' if href.startswith('/') else ' target="_blank"'
        return stash('<a href="%s" rel="nofollow noopener noreferrer"%s>%s</a>'
                     % (href, target, _inline_basic(label)))

    escaped = _LINK.sub(link, escaped)
    escaped = _BARE_URL.sub(
        lambda m: stash('<a href="%s" rel="nofollow noopener noreferrer" target="_blank">%s</a>'
                        % (m.group(1), m.group(1))),
        escaped,
    )
    escaped = _inline_basic(escaped)
    return _TOKEN_RE.sub(lambda m: vault[int(m.group(1))], escaped)


def _para(buf):
    return '<p>' + _inline(_escape('\n'.join(buf))).replace('\n', '<br>') + '</p>'


def _starts_block(line):
    return bool(_FENCE.match(line) or _HR.match(line) or _HEADING.match(line)
                or _QUOTE.match(line) or _ULI.match(line) or _OLI.match(line))


def render(source):
    """Render the Markdown subset to HTML that is safe to mark |safe in a template."""
    if not source:
        return ''
    lines = source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]

        if _FENCE.match(line):
            i += 1
            code = []
            while i < n and not _FENCE.match(lines[i]):
                code.append(_escape(lines[i]))
                i += 1
            i += 1                                   # closing fence, or EOF
            out.append('<pre><code>' + '\n'.join(code) + '</code></pre>')

        elif not line.strip():
            i += 1

        elif _HR.match(line):
            out.append('<hr>')
            i += 1

        elif _HEADING.match(line):
            m = _HEADING.match(line)
            level = len(m.group(1)) + 1              # h1 belongs to the page, not the body
            out.append('<h%d>%s</h%d>' % (level, _inline(_escape(m.group(2).strip())), level))
            i += 1

        elif _QUOTE.match(line):
            buf = []
            while i < n and _QUOTE.match(lines[i]):
                buf.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            out.append('<blockquote>' + _para(buf) + '</blockquote>')

        elif _ULI.match(line) or _OLI.match(line):
            pattern, tag = (_ULI, 'ul') if _ULI.match(line) else (_OLI, 'ol')
            items = []
            while i < n and pattern.match(lines[i]):
                items.append('<li>' + _inline(_escape(pattern.match(lines[i]).group(1).strip())) + '</li>')
                i += 1
            out.append('<%s>%s</%s>' % (tag, ''.join(items), tag))

        else:
            buf = []
            while i < n and lines[i].strip() and not _starts_block(lines[i]):
                buf.append(lines[i])
                i += 1
            out.append(_para(buf))

    return '\n'.join(out)


def render_plain(source):
    """Comment rendering: paragraphs, soft breaks, emphasis, links. No block markup."""
    if not source:
        return ''
    text = source.replace('\r\n', '\n').replace('\r', '\n').strip()
    blocks = [b for b in re.split(r'\n{2,}', text) if b.strip()]
    return '\n'.join(_para([b.strip()]) for b in blocks)


def to_text(source):
    """Strip markup down to prose. Used for excerpts and for the profanity scan."""
    if not source:
        return ''
    text = source.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)
    text = _LINK.sub(r'\1', text)
    text = re.sub(r'^\s*(?:[-*+]|\d{1,3}[.)])\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'[`*_>#]+', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def excerpt(source, limit=240):
    """First `limit` characters of prose, cut on a word boundary."""
    text = to_text(source)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(' ')
    return (cut[:space] if space > limit * 0.6 else cut).rstrip(' ,.;:') + '…'
