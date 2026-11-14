"""Founder Track public startup pages (FOUNDER_PLAN.md WP6, PRD "Startup bio page").

One page per venture at /startup/<slug>:

    ensure_page(venture)                draft the page from the latest evaluation
    slugify(text) / unique_slug(base)   lowercase-hyphen slugs, never reserved, never taken
    set_slug(page, slug, user)          rename; the old slug keeps a 301 (startup_slug_history)
    update(page, data, user)            founder edits (claims rules, demo allowlist, no return projections)
    request_publish(page, user)         ask the publish_page decision-right holder
    publish(page, user)                 holder only, and only with a clear name check
    unpublish(page, user)               any active member, any time
    public_context(page)                what the public view renders (never the ask)
    investor_context(page, access)      WP8 extension point (public context plus the ask)
    record_visit(page, req, ...)        hashed visitor (ip + user agent), like bio/routes.py track
    analytics(page, days)               views over time for the founders

Claims rule: a story field drafted from a cited "strength" finding is evidence-
backed; anything else the page says (assumption citations, founder edits,
traction the founders enter) is listed in page.founder_stated and renders with
a "founder-stated" label. Copy that projects investor returns is refused.

Name check: a name that imitates a well-known company, or exactly matches
another published startup page, sets name_check='pending'; publishing then
waits until an admin clears it in /admin/founder (Name checks tab).
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLUG_MIN, SLUG_MAX = 3, 60
SLUG_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$')

# Our own reserved set (profile._RESERVED is deliberately not used or changed):
# path segments under /startup/<slug>/... and words that would mislead.
RESERVED = frozenset((
    'edit', 'i', 'unlock', 'signin', 'request', 'onepager', 'onepager-pdf', 'admin', 'api', 'new', 'static',
    'startup', 'startups', 'founder', 'founders', 'login', 'logout', 'register', 'signup', 'settings', 'help',
    'about', 'search', 'explore', 'directory', 'index', 'home', 'www', 'mail', 'support', 'security', 'legal',
    'terms', 'privacy', 'pricing', 'billing', 'checkout', 'pay', 'payments', 'investor', 'investors', 'invest',
    'simisi', 'simulacrum', 'simi', 'official', 'verified', 'null', 'none', 'undefined', 'logo', 'preview',
    'page', 'pages', 'sitemap', 'robots', 'feed', 'rss', 'u', 'v', 'test',
))

STORY_FIELDS = ('problem', 'customer', 'solution', 'market', 'business_model')
TEXT_FIELDS = STORY_FIELDS + ('market_method',)
FIELD_DIMENSION = {'problem': 'problem', 'customer': 'customer', 'solution': 'solution',
                   'market': 'market', 'business_model': 'business_model'}
FIELD_LABELS = {'name': 'Name', 'one_liner': 'One-line pitch', 'problem': 'Problem', 'customer': 'Customer',
                'solution': 'Solution', 'market': 'Market', 'market_method': 'Sizing method',
                'business_model': 'Business model', 'demo_url': 'Demo video', 'traction': 'Traction'}
FIELD_MAX = {'name': 200, 'one_liner': 300, 'problem': 2000, 'customer': 2000, 'solution': 2000,
             'market': 1500, 'market_method': 800, 'business_model': 1500, 'use_of_funds': 2000,
             'ask_round': 60}
PUBLISH_REQUIRED = ('name', 'one_liner', 'problem')
STAGE_LABELS = {'idea': 'Idea', 'pre-seed': 'Pre-seed', 'seed': 'Seed', 'series-a': 'Series A'}

TRACTION_MAX = 6
METRIC_LABEL_MAX, METRIC_VALUE_MAX, METRIC_NOTE_MAX = 60, 40, 160

LOGO_MAX_BYTES = 1024 * 1024
LOGO_EXTS = {'png': 'PNG', 'jpg': 'JPEG', 'jpeg': 'JPEG', 'webp': 'WEBP'}

REQUEST_KINDS = {'intro': 'Introduction request', 'data_room': 'Data room request'}
PUBLIC_REQUEST_KINDS = ('intro',)  # the data-room request is investor-view only (PRD access levels)
REQUEST_LIMIT_DEFAULT = 5          # per IP per hour (fnd_startup_request_limit when WP0/WP3 add it)
PAGE_REQUEST_LIMIT_DEFAULT = 30    # per page per hour, whatever the IP (fnd_startup_page_request_limit)
REQUEST_DEDUPE_HOURS = 24          # the same email on the same page notifies the founders once a day
REQUEST_FIRM_MAX, REQUEST_MESSAGE_MAX = 200, 2000

_BOT_RE = re.compile(r'bot|crawl|spider|slurp|preview|facebookexternalhit|headless', re.I)

# Well-known companies a startup name must not imitate. A hit is not a refusal:
# the page is held (name_check='pending') until an admin looks at it.
FAMOUS_BRANDS = frozenset((
    'google', 'alphabet', 'apple', 'microsoft', 'amazon', 'meta', 'facebook', 'instagram', 'whatsapp', 'netflix',
    'tesla', 'spacex', 'openai', 'anthropic', 'nvidia', 'intel', 'ibm', 'oracle', 'salesforce', 'adobe', 'uber',
    'lyft', 'airbnb', 'stripe', 'paypal', 'visa', 'mastercard', 'amex', 'shopify', 'spotify', 'twitter',
    'linkedin', 'tiktok', 'bytedance', 'snapchat', 'pinterest', 'reddit', 'youtube', 'samsung', 'sony', 'disney',
    'nike', 'adidas', 'cocacola', 'pepsi', 'mcdonalds', 'starbucks', 'walmart', 'costco', 'ikea', 'toyota',
    'honda', 'bmw', 'mercedes', 'ferrari', 'porsche', 'coinbase', 'robinhood', 'dropbox', 'github', 'gitlab',
    'atlassian', 'figma', 'canva', 'deloitte', 'mckinsey', 'goldmansachs', 'jpmorgan', 'blackrock', 'sequoia',
    'ycombinator', 'andreessen', 'simisi', 'chatgpt', 'gemini', 'claude', 'alibaba', 'tencent', 'huawei',
    'xiaomi', 'siemens', 'boeing', 'airbus', 'pfizer', 'moderna', 'zoom', 'slack', 'notion', 'hubspot',
))
_LEET = str.maketrans({'0': 'o', '1': 'l', '3': 'e', '4': 'a', '5': 's', '7': 't', '$': 's', '@': 'a'})

# Investor-return projections: never on a startup page (FOUNDER_PLAN 1.5).
_RETURN_RE = re.compile(
    r'(\b\d+(?:\.\d+)?\s*x\s+(?:return|returns|multiple|your money)\b'
    r'|\b(?:irr|moic)\b'
    r'|\breturn on (?:your )?investment\b'
    r'|\binvestors? (?:will|can expect to|could) (?:earn|make|see|get)\b'
    r'|\b(?:expected|projected|target(?:ed)?) (?:investor )?returns?\b'
    r'|\binvestor returns?\b)', re.I)

_DEMO_HOSTS = {
    'youtube.com': 'youtube', 'www.youtube.com': 'youtube', 'm.youtube.com': 'youtube',
    'youtu.be': 'youtube', 'youtube-nocookie.com': 'youtube', 'www.youtube-nocookie.com': 'youtube',
    'vimeo.com': 'vimeo', 'www.vimeo.com': 'vimeo', 'player.vimeo.com': 'vimeo',
    'loom.com': 'loom', 'www.loom.com': 'loom',
}
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{6,64}$')


class PageError(ValueError):
    """A refusal with a message safe to show the founder."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _clean(text) -> str:
    text = unicodedata.normalize('NFKC', str(text or ''))
    text = ''.join(c for c in text if c in '\n\t' or unicodedata.category(c) not in ('Cc', 'Cf'))
    return '\n'.join(' '.join(line.split()) for line in text.strip().splitlines()).strip()


def _now():
    return datetime.utcnow()


def _page_for(venture):
    from app.models.founder import StartupPage
    return StartupPage.query.filter_by(venture_id=venture.id).first() if venture is not None else None


def _venture(page):
    from app.extensions import db
    from app.models.founder import FounderVenture
    return db.session.get(FounderVenture, page.venture_id)


def _member(venture, user):
    from app.services import founder_ventures
    return founder_ventures.get_member(venture, getattr(user, 'id', None))


def _log(page, kind, note='', user=None):
    from app.services import founder_ventures
    founder_ventures.log_event(page.venture_id, 'page_' + kind, note, user_id=getattr(user, 'id', None))


def _stream(page, event, data=None):
    """Tell the founder workspace; never fails the caller."""
    try:
        from app.services import founder_stream
        founder_stream.publish(page.venture_id, event, dict({'actor_kind': 'founder'}, **(data or {})), commit=False)
    except Exception as exc:
        logger.warning('startup page stream event %s failed: %s', event, exc)


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------

def slugify(text) -> str:
    """'Acme Robotics, Inc.' -> 'acme-robotics-inc'. Never empty."""
    t = unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode('ascii').lower()
    t = re.sub(r'[^a-z0-9]+', '-', t).strip('-')
    t = t[:SLUG_MAX].strip('-')
    if len(t) < SLUG_MIN:
        t = (t + '-startup').strip('-') if t else 'startup'
    return t


def _taken(slug: str, page_id=None) -> bool:
    """Is the slug used by another page, now or in another page's history?"""
    from app.models.founder import StartupPage, StartupSlugHistory
    p = StartupPage.query.filter_by(slug=slug).first()
    if p is not None and p.id != page_id:
        return True
    h = StartupSlugHistory.query.filter_by(old_slug=slug).first()
    return h is not None and h.startup_page_id != page_id


def unique_slug(base: str, page_id=None) -> str:
    base = slugify(base)
    if base not in RESERVED and not _taken(base, page_id):
        return base
    stem = base[:SLUG_MAX - 4].strip('-')
    n = 2
    while True:
        cand = '%s-%d' % (stem, n)
        if cand not in RESERVED and not _taken(cand, page_id):
            return cand
        n += 1


def validate_slug(raw, page_id=None) -> str:
    slug = (raw or '').strip().lower()
    if not (SLUG_MIN <= len(slug) <= SLUG_MAX) or not SLUG_RE.match(slug) or '--' in slug:
        raise PageError('Use %d to %d lowercase letters, numbers and single hyphens, starting and ending '
                        'with a letter or number.' % (SLUG_MIN, SLUG_MAX))
    if slug in RESERVED:
        raise PageError('That address is reserved. Choose another.')
    if _taken(slug, page_id):
        raise PageError('That address is taken. Choose another.')
    return slug


def set_slug(page, raw, user=None, commit: bool = True) -> str:
    """Rename the page address. The old slug goes to startup_slug_history so old
    links keep working (301); a slug the page used before can be taken back."""
    from app.extensions import db
    from app.models.founder import StartupSlugHistory
    slug = validate_slug(raw, page.id)
    if slug == page.slug:
        return slug
    old = page.slug
    mine = StartupSlugHistory.query.filter_by(old_slug=slug, startup_page_id=page.id).first()
    if mine is not None:
        db.session.delete(mine)
    if StartupSlugHistory.query.filter_by(old_slug=old).first() is None:
        db.session.add(StartupSlugHistory(startup_page_id=page.id, old_slug=old))
    db.session.flush()
    page.slug = slug
    page.updated_at = _now()
    _log(page, 'slug_changed', '%s -> %s' % (old, slug), user)
    if commit:
        db.session.commit()
    return slug


def resolve_slug(raw):
    """(page, redirect_slug). redirect_slug is set when raw is an old slug."""
    from app.extensions import db
    from app.models.founder import StartupPage, StartupSlugHistory
    slug = (raw or '').strip().lower()
    if not slug or len(slug) > 80:
        return None, None
    p = StartupPage.query.filter_by(slug=slug).first()
    if p is not None:
        return p, None
    h = StartupSlugHistory.query.filter_by(old_slug=slug).first()
    if h is not None:
        p = db.session.get(StartupPage, h.startup_page_id)
        if p is not None and p.slug != slug:
            return p, p.slug
    return None, None


# ---------------------------------------------------------------------------
# Name check
# ---------------------------------------------------------------------------

def _compact(name: str) -> str:
    t = unicodedata.normalize('NFKD', str(name or '')).encode('ascii', 'ignore').decode('ascii').lower()
    t = t.translate(_LEET)
    return re.sub(r'[^a-z0-9]+', '', t)


def name_concerns(name: str, page_id=None) -> list:
    """Why a name needs an admin look; [] when it doesn't."""
    out = []
    raw = unicodedata.normalize('NFKD', str(name or '')).encode('ascii', 'ignore').decode('ascii').lower()
    words = [w.translate(_LEET) for w in re.findall(r'[a-z0-9$@]+', raw)]
    words = [re.sub(r'[^a-z0-9]', '', w) for w in words]
    compact = _compact(name)
    pairs = {words[i] + words[i + 1] for i in range(len(words) - 1)}
    for b in sorted(FAMOUS_BRANDS):
        if b in words or b in pairs or compact == b or (len(b) >= 6 and b in compact):
            out.append('Resembles the well-known name "%s".' % b)
            break
    if compact:
        from app.models.founder import StartupPage
        for p in StartupPage.query.filter(StartupPage.status == 'published').all():
            if p.id != page_id and _compact(p.name) == compact:
                out.append('Matches another published startup page.')
                break
    return out


def _admin_hold_open(page) -> bool:
    """Is an admin's last name decision on this page a hold? (Renaming after a
    hold goes to 'pending'; a second rename must not clear it on its own.)"""
    if page.id is None:
        return False
    from app.models.audit_log import AuditLog
    row = (AuditLog.query.filter_by(action='founder_name_check', resource_id=page.id)
           .order_by(AuditLog.created_at.desc()).first())
    return bool(row is not None and row.extra.get('to') == 'held')


def run_name_check(page) -> str:
    """Set page.name_check from the name: pending on a concern, otherwise clear.
    An admin hold ('held') is sticky: a renamed page goes back to 'pending' for
    a fresh admin review, never straight to 'clear'. A published page whose
    name is not clear is held (offline) until an admin clears it."""
    previous = page.name_check
    concerns = name_concerns(page.name, page.id)
    if previous == 'held' or (previous == 'pending' and _admin_hold_open(page)):
        page.name_check = 'pending'
    else:
        page.name_check = 'pending' if concerns else 'clear'
    if page.name_check != 'clear' and page.status == 'published':
        page.status = 'held'
    return page.name_check


def name_check_message(page) -> str:
    """Founder-facing wording for a name that is not clear."""
    if page.name_check == 'held':
        return ('SimiSI did not clear this name. Choose a different name; the new name gets a fresh review '
                'before the page can go live.')
    return 'The name is being reviewed by SimiSI. The page can go live once the review clears it.'


# ---------------------------------------------------------------------------
# Drafting from the evaluation
# ---------------------------------------------------------------------------

def _evidence(venture) -> dict:
    """{field: (text, supported)} from the venture's current findings. Empty
    for an unpaid venture: finding text and citations stay locked in the free
    preview (FOUNDER_PLAN section 0), so the page is not drafted from them."""
    try:
        from app.services import founder_ventures
        if not founder_ventures.is_paid(venture):
            return {}
    except (ImportError, AttributeError):
        return {}
    try:
        from app.services import founder_evaluation_service as fes
        rows = fes.findings(venture)
    except Exception as exc:
        logger.warning('startup page draft: findings unavailable for %s: %s', venture.id, exc)
        return {}
    out = {}
    for field, dim in FIELD_DIMENSION.items():
        cited = [f for f in rows if f.dimension == dim and (f.citation_text or '').strip()]
        strong = sorted([f for f in cited if f.ftype == 'strength'], key=lambda f: -(f.confidence or 0))
        weak = sorted([f for f in cited if f.ftype in ('assumption', 'contradiction')],
                      key=lambda f: -(f.confidence or 0))
        if strong:
            out[field] = (_clean(strong[0].citation_text), True)
        elif weak:
            out[field] = (_clean(weak[0].citation_text), False)
    return out


def supported_fields(venture) -> set:
    return {k for k, (_t, ok) in _evidence(venture).items() if ok}


def _draft_fields(page, venture, overwrite: bool = False) -> list:
    """Fill story fields from cited findings. Returns the fields filled."""
    stated = set(page.founder_stated or [])
    filled = []
    for field, (text, supported) in _evidence(venture).items():
        if not text or (getattr(page, field) and not overwrite):
            continue
        setattr(page, field, text[:FIELD_MAX.get(field, 2000)])
        if supported:
            stated.discard(field)
        else:
            stated.add(field)
        filled.append(field)
    page.founder_stated = sorted(stated)
    return filled


def ensure_page(venture, user=None):
    """The venture's startup page, drafted on first call from the latest
    evaluation (cited findings) and the venture's name, one-liner and stage."""
    from app.extensions import db
    from app.models.founder import StartupPage
    page = _page_for(venture)
    if page is not None:
        return page
    now = _now()
    page = StartupPage(venture_id=venture.id, slug=unique_slug(venture.name), status='draft',
                       name=venture.name, one_liner=venture.one_liner, stage=venture.stage,
                       traction=[], founder_stated=[], created_at=now, updated_at=now)
    stated = ['one_liner'] if venture.one_liner else []
    page.founder_stated = stated
    _draft_fields(page, venture)
    run_name_check(page)
    from sqlalchemy.exc import IntegrityError
    try:
        db.session.add(page)
        db.session.flush()
        _log(page, 'drafted', 'Startup page drafted at /startup/%s' % page.slug, user)
        db.session.commit()
    except IntegrityError:
        # Two first visits at once (editor and preview tabs): the other one won.
        db.session.rollback()
        existing = _page_for(venture)
        if existing is None:
            raise
        return existing
    return page


def refresh_from_evaluation(page, user=None, overwrite: bool = False) -> list:
    """Fill empty story fields (or all, with overwrite) from the latest findings."""
    from app.extensions import db
    filled = _draft_fields(page, _venture(page), overwrite=overwrite)
    if filled:
        page.updated_at = _now()
        _log(page, 'redrafted', ', '.join(filled), user)
    db.session.commit()
    return filled


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------

def demo_embed(url):
    """{'provider', 'embed_url'} for an allowlisted demo video URL, else None."""
    if not url:
        return None
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return None
    if u.scheme not in ('http', 'https'):
        return None
    host = (u.hostname or '').lower()
    provider = _DEMO_HOSTS.get(host)
    parts = [p for p in u.path.split('/') if p]
    vid = None
    if provider == 'youtube':
        if host == 'youtu.be' and parts:
            vid = parts[0]
        elif parts[:1] == ['watch']:
            vid = (parse_qs(u.query).get('v') or [None])[0]
        elif len(parts) >= 2 and parts[0] in ('embed', 'shorts', 'live'):
            vid = parts[1]
        if vid and _ID_RE.match(vid):
            return {'provider': 'YouTube', 'embed_url': 'https://www.youtube-nocookie.com/embed/' + vid}
    elif provider == 'vimeo':
        nums = [p for p in parts if p.isdigit()]
        if nums:
            return {'provider': 'Vimeo', 'embed_url': 'https://player.vimeo.com/video/' + nums[0]}
    elif provider == 'loom':
        if len(parts) >= 2 and parts[0] in ('share', 'embed') and _ID_RE.match(parts[1]):
            return {'provider': 'Loom', 'embed_url': 'https://www.loom.com/embed/' + parts[1]}
    return None


def return_projection(text) -> str:
    """The first phrase that projects investor returns, or ''."""
    m = _RETURN_RE.search(text or '')
    if m:
        return m.group(0)
    try:                                   # WP4's compliance checker, when present
        from app.services import founder_compliance
        for issue in founder_compliance.check(text or '', 5) or []:
            if isinstance(issue, dict) and issue.get('rule') in ('return_projection', 'banned_phrase'):
                return issue.get('excerpt') or issue.get('label') or issue['rule']
    except (ImportError, AttributeError):
        pass
    except Exception as exc:
        logger.warning('founder_compliance.check failed: %s', exc)
    return ''


# Investor-only fields (the ask) are checked against these rules; public copy
# against every rule founder_compliance knows.
_ASK_RULES = ('return_projection', 'banned_phrase', 'advice')
_PUBLIC_FIELDS = ('name', 'one_liner') + TEXT_FIELDS


def _exemption(venture) -> str:
    try:
        from app.services import founder_compliance
        return founder_compliance.exemption_for(venture) or 'undecided'
    except (ImportError, AttributeError):
        return 'undecided'
    except Exception as exc:
        logger.warning('founder_compliance.exemption_for failed: %s', exc)
        return 'undecided'


def copy_problem(text, exemption='undecided', public: bool = True) -> str:
    """A founder-facing refusal for page copy that breaks a founder rule, or ''.

    Public copy (name, one-liner, story, traction) is shown on an indexed page,
    so every founder_compliance rule applies, and public solicitation of
    investors is refused unless the venture's exemption is 506(c). The ask
    (investor view only) is held to the narrower rules in _ASK_RULES."""
    text = text or ''
    hit = _RETURN_RE.search(text)
    if hit:
        return 'Startup pages make no return projections or earnings claims. Remove "%s".' % hit.group(0)
    try:
        from app.services import founder_compliance
        issues = founder_compliance.check(text, 5, exemption=exemption) or []
    except (ImportError, AttributeError):
        issues = []
        if public and exemption != '506c' and re.search(
                r'\b(?:invest\s+in\s+us|now\s+accepting\s+investments?|anyone\s+(?:can|interested\s+in)\s+invest)\b',
                text, re.I):
            return ('A public startup page cannot invite investment. Remove the wording that offers the raise '
                    'to the public.')
    except Exception as exc:
        logger.warning('founder_compliance.check failed: %s', exc)
        issues = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        rule = issue.get('rule')
        if not public and rule not in _ASK_RULES:
            continue
        if rule == 'general_solicitation' and exemption == '506c':
            continue
        excerpt = (issue.get('match') or issue.get('excerpt') or '').strip()
        if rule in ('return_projection', 'banned_phrase'):
            return 'Startup pages make no return projections or earnings claims. Remove "%s".' % (excerpt or rule)
        if rule == 'general_solicitation':
            return ('A public startup page cannot invite investment while the offering is not a 506(c) raise. '
                    'Remove "%s".' % excerpt)
        return 'Startup pages cannot include %s. Remove "%s".' % (issue.get('label') or rule, excerpt)
    return ''


def _clean_traction(raw) -> list:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PageError('Traction must be a list of metrics.')
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = _clean(item.get('label'))[:METRIC_LABEL_MAX]
        value = _clean(item.get('value'))[:METRIC_VALUE_MAX]
        if not label and not value:
            continue
        if not label or not value:
            raise PageError('Each metric needs a label and a value.')
        note = _clean(item.get('note'))[:METRIC_NOTE_MAX]
        as_of = _clean(item.get('as_of'))[:10]
        if as_of:
            try:
                datetime.strptime(as_of, '%Y-%m-%d')
            except ValueError:
                raise PageError('Metric dates use the form YYYY-MM-DD.')
        out.append({'label': label, 'value': value, 'note': note, 'as_of': as_of,
                    'show': item.get('show', True) not in (False, 'false', '0', 0, 'off'), 'source': 'founder'})
    if len(out) > TRACTION_MAX:
        raise PageError('Show at most %d metrics.' % TRACTION_MAX)
    return out


def update(page, data: dict, user=None, commit: bool = True):
    """Apply founder edits. Changed story text becomes founder-stated (an edit
    is not evidence). Raises PageError with a founder-facing message."""
    from app.extensions import db
    data = data or {}
    stated = set(page.founder_stated or [])
    changes = []
    exemption = _exemption(_venture(page))

    for field in ('name', 'one_liner') + TEXT_FIELDS + ('use_of_funds', 'ask_round'):
        if field not in data:
            continue
        val = _clean(data.get(field))
        if len(val) > FIELD_MAX[field]:
            raise PageError('%s is too long (%d characters at most).'
                            % (FIELD_LABELS.get(field, field.replace('_', ' ').capitalize()), FIELD_MAX[field]))
        problem = copy_problem(val, exemption, public=field in _PUBLIC_FIELDS)
        if problem:
            raise PageError(problem)
        if field == 'name' and not val:
            raise PageError('The page needs a name.')
        if val != (getattr(page, field) or ''):
            setattr(page, field, val or None)
            changes.append(field)
            if field in STORY_FIELDS or field in ('one_liner', 'market_method'):
                if val:
                    stated.add(field)
                else:
                    stated.discard(field)

    if 'stage' in data:
        stage = (data.get('stage') or '').strip().lower()
        if stage not in STAGE_LABELS:
            raise PageError('Unknown stage.')
        if stage != page.stage:
            page.stage = stage
            changes.append('stage')

    if 'demo_url' in data:
        url = (data.get('demo_url') or '').strip()
        if url and demo_embed(url) is None:
            raise PageError('Demo videos can come from YouTube, Vimeo or Loom. Paste the video\'s share link.')
        if (url or None) != page.demo_url:
            page.demo_url = url[:500] or None
            changes.append('demo_url')

    if 'ask_amount' in data:
        raw = str(data.get('ask_amount') or '').replace(',', '').replace('$', '').strip()
        if raw:
            try:
                amt = int(float(raw))
            except ValueError:
                raise PageError('The amount is a whole number of US dollars.')
            if amt < 0 or amt > 10 ** 11:
                raise PageError('The amount is out of range.')
        else:
            amt = None
        if amt != page.ask_amount:
            page.ask_amount = amt
            changes.append('ask_amount')

    if 'traction' in data:
        tr = _clean_traction(data.get('traction'))
        for m in tr:
            problem = copy_problem(' '.join((m['label'], m['value'], m['note'])), exemption, public=True)
            if problem:
                raise PageError(problem)
        if tr != (page.traction or []):
            page.traction = tr
            changes.append('traction')

    page.founder_stated = sorted(stated)
    if 'name' in changes:
        run_name_check(page)
    if changes:
        page.updated_at = _now()
        _log(page, 'edited', ', '.join(changes), user)
    if commit:
        db.session.commit()
    return changes


def save_logo(page, file_storage, user=None) -> str:
    """Store an uploaded logo (PNG, JPEG or WebP, up to 1 MB), re-encoded."""
    import io
    import os
    from flask import current_app
    from app.extensions import db
    if file_storage is None or not getattr(file_storage, 'filename', ''):
        raise PageError('Choose an image.')
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''
    if ext not in LOGO_EXTS:
        raise PageError('Logos can be PNG, JPEG or WebP.')
    blob = file_storage.read(LOGO_MAX_BYTES + 1)
    if len(blob) > LOGO_MAX_BYTES:
        raise PageError('The logo is larger than 1 MB.')
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(blob))
        img.load()
        img.thumbnail((512, 512))
        if img.mode not in ('RGB', 'RGBA'):
            img = img.convert('RGBA')
        out = io.BytesIO()
        img.save(out, 'PNG')
    except Exception:
        raise PageError('That file is not an image we can read.')
    folder = os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), 'startup_logos')
    os.makedirs(folder, exist_ok=True)
    rel = 'startup_logos/%s.png' % page.id
    with open(os.path.join(current_app.config.get('UPLOAD_FOLDER', 'uploads'), rel), 'wb') as fh:
        fh.write(out.getvalue())
    page.logo_path = rel
    page.updated_at = _now()
    _log(page, 'logo', 'Logo updated', user)
    db.session.commit()
    return rel


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

def publish_holder(venture):
    """The VentureMember holding the publish_page decision right, or None."""
    from app.extensions import db
    from app.models.founder import DecisionRight, VentureMember
    r = (DecisionRight.query.filter_by(venture_id=venture.id, decision_type='publish_page')
         .order_by(DecisionRight.layer.is_(None).desc(), DecisionRight.created_at).first())
    if r is None:
        return None
    m = db.session.get(VentureMember, r.holder_member_id)
    return m if m is not None and m.status == 'active' else None


def _paid(venture) -> bool:
    from app.services import founder_ventures
    return founder_ventures.is_paid(venture)


def can_publish(page, user) -> bool:
    holder = publish_holder(_venture(page))
    return bool(holder is not None and user is not None and holder.user_id == getattr(user, 'id', None))


def missing_for_publish(page) -> list:
    return [FIELD_LABELS[f] for f in PUBLISH_REQUIRED if not (getattr(page, f) or '').strip()]


def pending_request(page):
    """The latest publish request not yet followed by a publish/unpublish, or None."""
    from app.models.audit_log import AuditLog
    rows = (AuditLog.query.filter(AuditLog.resource_id == page.venture_id,
                                  AuditLog.action.in_(('founder_page_publish_requested', 'founder_page_published',
                                                       'founder_page_unpublished')))
            .order_by(AuditLog.created_at.desc()).first())
    if rows is not None and rows.action == 'founder_page_publish_requested':
        return {'user_id': rows.user_id, 'at': rows.created_at}
    return None


def request_publish(page, user) -> dict:
    """Any active member asks to publish. The decision-right holder publishes
    straight away (if the name check is clear); anyone else notifies the holder.
    A name still under review holds the page."""
    from app.extensions import db
    from app.models.user import User
    venture = _venture(page)
    if _member(venture, user) is None:
        raise PageError('Only the venture\'s founders can publish its page.')
    missing = missing_for_publish(page)
    if missing:
        raise PageError('Fill in %s before publishing.' % ', '.join(missing))
    if page.status == 'published':
        return {'status': 'published', 'message': 'The page is already live.'}
    if not _paid(venture):
        raise PageError('The startup page goes live with the Founder Track. Unlock it to publish.')
    if page.name_check != 'clear':
        page.status = 'held'
        page.updated_at = _now()
        _log(page, 'held', 'Publish waits for the name check (%s)' % page.name_check, user)
        db.session.commit()
        return {'status': 'held', 'message': name_check_message(page)}
    if can_publish(page, user):
        publish(page, user)
        return {'status': 'published', 'message': 'The page is live.'}
    _log(page, 'publish_requested', 'Publish requested', user)
    _stream(page, 'page_publish_requested', {'actor_user_id': user.id})
    db.session.commit()
    holder = publish_holder(venture)
    hu = db.session.get(User, holder.user_id) if holder is not None and holder.user_id else None
    if hu is not None:
        _email(hu.email, 'Publish request for the %s startup page' % page.name,
               '%s asked to publish the %s startup page. You hold the publishing decision, so it goes live '
               'only when you publish it from the page editor.' % (getattr(user, 'full_name', 'A co-founder'),
                                                                    page.name))
    return {'status': page.status, 'message': 'Request sent to the co-founder who decides on publishing.'}


def publish(page, user, commit: bool = True):
    """Holder of the publish_page right only, and only with a clear name check."""
    from app.extensions import db
    if not can_publish(page, user):
        raise PageError('Only the co-founder who holds the publishing decision can publish the page.')
    if not _paid(_venture(page)):
        raise PageError('The startup page goes live with the Founder Track. Unlock it to publish.')
    if page.name_check != 'clear':
        raise PageError(name_check_message(page))
    missing = missing_for_publish(page)
    if missing:
        raise PageError('Fill in %s before publishing.' % ', '.join(missing))
    page.status = 'published'
    page.published_at = _now()
    page.publish_approved_by = user.id
    page.updated_at = _now()
    _log(page, 'published', '/startup/%s' % page.slug, user)
    _stream(page, 'page_published', {'actor_user_id': user.id, 'slug': page.slug})
    if commit:
        db.session.commit()
    return page


def unpublish(page, user, commit: bool = True):
    """Any active member may take the page down at any time."""
    from app.extensions import db
    if _member(_venture(page), user) is None:
        raise PageError('Only the venture\'s founders can unpublish its page.')
    was = page.status
    page.status = 'unpublished'
    page.updated_at = _now()
    _log(page, 'unpublished', 'from %s' % was, user)
    _stream(page, 'page_unpublished', {'actor_user_id': user.id})
    if commit:
        db.session.commit()
    if was == 'published':
        try:                        # WP8: refund investors inside the refund window
            from app.services import startup_access_service
            startup_access_service.refund_if_unpublished(page)
        except (ImportError, AttributeError):
            pass
        except Exception as exc:
            logger.error('refund_if_unpublished failed for %s: %s', page.id, exc)
    return page


def is_public(page) -> bool:
    return bool(page is not None and page.status == 'published' and page.name_check == 'clear')


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _ladder():
    try:
        from app.services.founder_evaluation_service import LADDER
        return LADDER
    except (ImportError, AttributeError):
        return ((1, 'Claimed pain', '', None), (2, 'Confirmed pain', '', None),
                (3, 'Agreed proof of concept', '', None), (4, 'Shown benefit', '', None), (5, 'Paid', '', None))


def _proof(venture) -> dict:
    """The benefits case (measured results) and quotes used with permission."""
    from app.models.founder import ProofResult
    rows = (ProofResult.query.filter_by(venture_id=venture.id)
            .order_by(ProofResult.created_at.desc()).limit(20).all())
    benefits = next((_clean(r.benefits_text) for r in rows if r.measured and (r.benefits_text or '').strip()), '')
    quotes = [_clean(r.quote_text) for r in rows if r.quote_permission and (r.quote_text or '').strip()][:3]
    paid = any(r.paid_at for r in rows)
    return {'benefits': benefits, 'quotes': quotes, 'paid': paid}


def _team(venture) -> list:
    from app.models.bio_page import BioPage
    from app.models.founder import VentureMember
    from app.models.profile import UserProfile
    from app.models.user import User
    members = (VentureMember.query.filter_by(venture_id=venture.id, status='active')
               .order_by(VentureMember.role.desc(), VentureMember.joined_at, VentureMember.created_at).all())
    out = []
    for m in members:
        if not m.user_id:
            continue
        u = User.query.get(m.user_id)
        if u is None or getattr(u, 'deleted_at', None):
            continue
        prof = UserProfile.query.filter_by(user_id=u.id).first()
        bio = BioPage.query.filter_by(user_id=u.id, status=BioPage.STATUS_PUBLISHED).first()
        linked = prof is not None and bio is not None and bool(prof.username)
        name = (prof.display_name if prof is not None and prof.display_name else u.full_name) or 'Co-founder'
        out.append({'name': name, 'title': m.title or ('Founder' if m.role == 'owner' else 'Co-founder'),
                    'url': '/u/%s' % prof.username if linked else None,
                    'avatar': prof.avatar_path if linked and prof.avatar_path else None,
                    'initials': ''.join(w[0] for w in name.split()[:2]).upper()})
    return out


def public_context(page) -> dict:
    """Everything the public view renders. Never contains the ask
    (ask_round, ask_amount, use_of_funds) or any contact detail of the founders."""
    venture = _venture(page)
    stated = set(page.founder_stated or [])
    rung = int(getattr(venture, 'rung', 1) or 1)
    ladder = [{'n': n, 'title': t, 'text': e, 'reached': n <= rung, 'current': n == rung}
              for (n, t, e, *_rest) in _ladder()]
    metrics = [m for m in (page.traction or []) if m.get('show', True)]
    sections = {f: {'text': getattr(page, f) or '', 'founder_stated': f in stated} for f in TEXT_FIELDS}
    ctx = {
        'investor_view': False,
        'id': page.id,
        'slug': page.slug,
        'name': page.name,
        'one_liner': page.one_liner or '',
        'one_liner_founder_stated': 'one_liner' in stated,
        'stage': page.stage or getattr(venture, 'stage', None),
        'stage_label': STAGE_LABELS.get(page.stage or getattr(venture, 'stage', ''), ''),
        'as_of': (page.updated_at or page.created_at or _now()),
        'logo': bool(page.logo_path),
        'sections': sections,
        'demo': demo_embed(page.demo_url),
        'metrics': metrics,
        'rung': rung,
        'ladder': ladder,
        'proof': _proof(venture),
        'team': _team(venture),
        'founder_stated': sorted(stated),
        'request_kinds': {k: REQUEST_KINDS[k] for k in PUBLIC_REQUEST_KINDS},
    }
    try:
        from app.services import contact_masking
        ctx['sections'] = contact_masking.mask_deep(ctx['sections'], force=True)
        ctx['metrics'] = contact_masking.mask_deep(ctx['metrics'], force=True)
        ctx['proof'] = contact_masking.mask_deep(ctx['proof'], force=True)
        ctx['one_liner'] = contact_masking.mask_deep(ctx['one_liner'], force=True)
    except Exception as exc:
        logger.warning('startup page masking failed: %s', exc)
    return ctx


def investor_context(page, access=None) -> dict:
    """WP8 extension point: the public context plus the ask, built only after
    the caller has checked access (startup_access_service.has_access)."""
    if access is None:
        raise PageError('No investor access.')
    ctx = public_context(page)
    ctx.update({'investor_view': True, 'ask': {'round': page.ask_round, 'amount': page.ask_amount,
                                               'use_of_funds': page.use_of_funds},
                'request_kinds': dict(REQUEST_KINDS)})
    return ctx


# ---------------------------------------------------------------------------
# Visits and analytics
# ---------------------------------------------------------------------------

def _private_addr(addr: str) -> bool:
    import ipaddress
    try:
        ip = ipaddress.ip_address((addr or '').strip())
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _client_ip(req) -> str:
    """The address to count requests by. The client controls everything it
    puts in X-Forwarded-For, so the header is read only when the connection
    comes from a local proxy (a private or loopback remote_addr), and then only
    its rightmost hop, the one that proxy appended."""
    remote = (req.remote_addr or '').strip()
    fwd = req.headers.get('X-Forwarded-For') or ''
    if fwd and (not remote or _private_addr(remote)):
        hops = [h.strip() for h in fwd.split(',') if h.strip()]
        if hops:
            return hops[-1]
    return remote


def visitor_hash(req) -> str:
    return hashlib.sha256((_client_ip(req) + (req.headers.get('User-Agent') or '')).encode()).hexdigest()[:32]


def record_visit(page, req, view: str = 'public', investor_link_id=None, user=None):
    """One startup_page_visit row. Bots and the venture's own members are not counted."""
    from app.extensions import db
    from app.models.founder import StartupPageVisit
    ua = req.headers.get('User-Agent') or ''
    if _BOT_RE.search(ua):
        return None
    if user is not None and getattr(user, 'is_authenticated', False):
        if _member(_venture(page), user) is not None:
            return None
    ref = (req.headers.get('Referer') or '')[:500]
    try:
        host = urlsplit(ref).hostname or ''
        if host and host == (urlsplit(req.host_url).hostname or ''):
            ref = ''
    except ValueError:
        ref = ''
    row = StartupPageVisit(startup_page_id=page.id, visitor_hash=visitor_hash(req),
                           investor_link_id=investor_link_id, view=view if view in ('public', 'investor') else 'public',
                           referrer=ref or None)
    try:
        db.session.add(row)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.warning('startup page visit not recorded: %s', exc)
        return None
    return row


def analytics(page, days: int = 30) -> dict:
    from app.models.founder import StartupPageVisit
    days = max(7, min(int(days or 30), 90))
    now = _now()
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (StartupPageVisit.query.filter(StartupPageVisit.startup_page_id == page.id,
                                          StartupPageVisit.created_at >= start).all())
    by_day = {}
    for r in rows:
        k = r.created_at.strftime('%Y-%m-%d')
        by_day[k] = by_day.get(k, 0) + 1
    series = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime('%Y-%m-%d')
        series.append({'date': d, 'views': by_day.get(d, 0)})
    refs = {}
    for r in rows:
        host = ''
        try:
            host = (urlsplit(r.referrer).hostname or '') if r.referrer else ''
        except ValueError:
            pass
        host = host[4:] if host.startswith('www.') else host
        refs[host or 'Direct or unknown'] = refs.get(host or 'Direct or unknown', 0) + 1
    d7 = now - timedelta(days=7)
    total = StartupPageVisit.query.filter_by(startup_page_id=page.id).count()
    return {
        'days': days, 'series': series, 'peak': max([s['views'] for s in series] or [0]),
        'views': len(rows), 'unique': len({r.visitor_hash for r in rows if r.visitor_hash}),
        'views_7d': sum(1 for r in rows if r.created_at >= d7),
        'investor_views': sum(1 for r in rows if r.view == 'investor'),
        'total': total,
        'referrers': sorted(refs.items(), key=lambda kv: -kv[1])[:5],
    }


# ---------------------------------------------------------------------------
# Inbound requests (intro / data room)
# ---------------------------------------------------------------------------

def request_limit() -> int:
    try:
        from app.services import founder_config
        return int(founder_config.get('fnd_startup_request_limit'))
    except (ImportError, AttributeError, KeyError, TypeError, ValueError):
        return REQUEST_LIMIT_DEFAULT


def page_request_limit() -> int:
    try:
        from app.services import founder_config
        return int(founder_config.get('fnd_startup_page_request_limit'))
    except (ImportError, AttributeError, KeyError, TypeError, ValueError):
        return PAGE_REQUEST_LIMIT_DEFAULT


def ip_hash(req) -> str:
    return hashlib.sha256(_client_ip(req).encode()).hexdigest()[:32]


def _email_hash(email: str) -> str:
    return hashlib.sha256((email or '').strip().lower().encode()).hexdigest()[:32]


def rate_limited(req, page=None) -> str:
    """'' when the request may go ahead, else 'ip' (this connection sent too
    many in the last hour) or 'page' (the page got too many from everyone)."""
    from app.models.audit_log import AuditLog
    since = _now() - timedelta(hours=1)
    recent = AuditLog.query.filter(AuditLog.action == 'startup_page_request', AuditLog.created_at >= since)
    if recent.filter(AuditLog._metadata.like('%%"ip_hash": "%s"%%' % ip_hash(req))).count() >= request_limit():
        return 'ip'
    if page is not None and recent.filter(AuditLog.resource_id == page.id).count() >= page_request_limit():
        return 'page'
    return ''


def _recent_duplicate(page, email: str) -> bool:
    """Did this email already send this page a request within REQUEST_DEDUPE_HOURS?"""
    from app.models.audit_log import AuditLog
    since = _now() - timedelta(hours=REQUEST_DEDUPE_HOURS)
    return (AuditLog.query.filter(AuditLog.action == 'startup_page_request', AuditLog.resource_id == page.id,
                                  AuditLog.created_at >= since,
                                  AuditLog._metadata.like('%%"email_hash": "%s"%%' % _email_hash(email)))
            .first() is not None)


def submit_request(page, data: dict, req, allow_data_room: bool = False) -> dict:
    """Validate an intro or data-room request, store it as a Contact of the
    venture owner (source 'startup_page'), audit it and notify the founders.
    The data-room request is investor-view only: allow_data_room is set by
    WP8's investor route; from the public view it becomes an intro request.
    A repeat from the same email within REQUEST_DEDUPE_HOURS is audited but
    adds no CRM activity and sends no email. Raises PageError (400) for bad input."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.contact import Contact, ContactActivity
    from utils.validators import ValidationError, validate_email, validate_full_name

    if not isinstance(data, dict):
        raise PageError('Send the request as a form or a JSON object.')
    for key in ('kind', 'name', 'email', 'firm', 'message'):
        if data.get(key) is not None and not isinstance(data.get(key), str):
            raise PageError('Each field must be text.')
    kind = (data.get('kind') or 'intro').strip()
    if kind not in REQUEST_KINDS or (kind not in PUBLIC_REQUEST_KINDS and not allow_data_room):
        kind = 'intro'
    try:
        name = validate_full_name(data.get('name'))
    except ValidationError as exc:
        raise PageError(str(exc))
    try:
        email = validate_email(data.get('email'))
    except ValidationError as exc:
        raise PageError(str(exc))
    firm = _clean(data.get('firm'))
    if len(firm) > REQUEST_FIRM_MAX:
        raise PageError('The firm name is too long.')
    message = _clean(data.get('message'))
    if len(message) > REQUEST_MESSAGE_MAX:
        raise PageError('The message is too long (%d characters at most).' % REQUEST_MESSAGE_MAX)
    if kind == 'data_room' and not message and not firm:
        raise PageError('Tell the founders who you are with, or add a short note.')

    venture = _venture(page)
    owner_id = venture.owner_user_id
    if _recent_duplicate(page, email):
        AuditLog.log('startup_page_request', resource_id=page.id,
                     metadata={'kind': kind, 'venture_id': venture.id, 'ip_hash': ip_hash(req),
                               'email_hash': _email_hash(email), 'duplicate': True})
        db.session.commit()
        return {'contact_id': None, 'kind': kind, 'duplicate': True}
    parts = name.split()
    first, last = parts[0][:100], (' '.join(parts[1:]) or '-')[:100]
    label = REQUEST_KINDS[kind]
    note = '%s via /startup/%s%s' % (label, page.slug, (': ' + message) if message else '')
    c = Contact.query.filter_by(user_id=owner_id, email=email).first()
    if c is None:
        c = Contact(user_id=owner_id, first_name=first, last_name=last, email=email,
                    company_name=firm or None, source='startup_page', source_notes=note[:500],
                    role_to_user='investor', notes=message or None)
        db.session.add(c)
        db.session.flush()
    else:
        if firm and not c.company_name:
            c.company_name = firm
    db.session.add(ContactActivity(contact_id=c.id, activity_type='startup_page_request', notes=note[:2000],
                                   created_by='visitor'))
    AuditLog.log('startup_page_request', resource_id=page.id,
                 metadata={'kind': kind, 'contact_id': c.id, 'venture_id': venture.id, 'ip_hash': ip_hash(req),
                           'email_hash': _email_hash(email)})
    _stream(page, 'page_request', {'kind': kind, 'contact_id': c.id, 'note': label, 'actor_kind': 'system'})
    db.session.commit()
    _notify_founders(page, venture, kind, name, email, firm, message)
    return {'contact_id': c.id, 'kind': kind}


def _notify_founders(page, venture, kind, name, email, firm, message):
    from app.models.founder import VentureMember
    from app.models.user import User
    ids = [m.user_id for m in VentureMember.query.filter_by(venture_id=venture.id, status='active').all()
           if m.user_id]
    users = User.query.filter(User.id.in_(ids or ['-'])).all()
    label = REQUEST_KINDS[kind]
    body = ('%s for %s through its startup page.\n\nFrom: %s <%s>%s\n\n%s\n\nThe request is in your contacts '
            '(source: startup page). SimiSI has not replied for you; you decide whether and how to respond.'
            % (label, page.name, name, email, ('\nFirm: ' + firm) if firm else '', message or '(No message.)'))
    for u in users:
        if getattr(u, 'deleted_at', None):
            continue
        _email(u.email, '%s: %s' % (label, page.name), body)


def _email(to, subject, text):
    """A plain notification through the house email path; never raises."""
    try:
        from markupsafe import escape
        from app.services import email_service as es
        html = es._html_wrap(es._h1(str(escape(subject))) + ''.join(
            es._p(str(escape(p)).replace('\n', '<br>')) for p in text.split('\n\n')), preheader=subject[:90])
        es._send(subject=subject + ' - SimiSI', recipients=[to], body=text + '\n\n- SimiSI', html=html)
        return True
    except Exception as exc:
        logger.error('startup page email to %s failed: %s', to, exc)
        return False
