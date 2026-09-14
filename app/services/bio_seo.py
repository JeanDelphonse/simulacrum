"""Search metadata for the public bio pages at /u/<slug>.

One module decides three things that have to agree: the title and description a
bio page renders, whether that page tells crawlers to index it, and whether the
sitemap lists it. When those drift you get the worst outcome in SEO — a URL
advertised in the sitemap that serves `noindex`, or a private page indexed
because the sitemap and the template disagreed about what "private" means.

`is_indexable()` is the single gate. The template asks it for the robots tag and
the sitemap asks it for inclusion, so a page is either fully advertised or fully
withheld.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The title pattern for every bio page. Two qualifiers after the name: what the
# page is, then what the product is, so a name search and a category search both
# land on something readable in the SERP.
TITLE_SUFFIX = 'Professional AI Agent Simulation Bio | AI agent career builder'

SITE_NAME = 'Simulacrum'

# Google truncates around 155-160 characters; past that we are writing for
# nobody. Titles get more room but still want a ceiling.
MAX_DESCRIPTION = 160
MAX_TITLE = 120


def name_parts(full_name: str) -> tuple:
    """(first, last) from a display name, either of which may be empty.

    A single-word name gives a first name and no last — the title falls back to
    whatever is there rather than rendering a stray separator.
    """
    parts = [p for p in (full_name or '').split() if p]
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], parts[-1]


def display_name(full_name: str) -> str:
    first, last = name_parts(full_name)
    return ('%s %s' % (first, last)).strip() or 'Professional'


def page_title(full_name: str) -> str:
    """"First Last | Professional AI Agent Simulation Bio | AI agent career builder"."""
    title = '%s | %s' % (display_name(full_name), TITLE_SUFFIX)
    return title if len(title) <= MAX_TITLE else title[:MAX_TITLE - 1].rstrip() + '…'


def meta_description(full_name: str, professional_title: str = '',
                     positioning: str = '', about_text: str = '') -> str:
    """The page's own words first, a composed sentence only if it has none.

    A hand-written positioning line always beats a template, so this only
    assembles something when the member has left both fields empty.
    """
    own = (positioning or '').strip() or (about_text or '').strip()
    if own:
        text = ' '.join(own.split())
    else:
        name = display_name(full_name)
        role = (professional_title or '').strip()
        if role:
            text = ('%s is a %s. See the career-to-wealth simulation behind this '
                    'bio and the AI agents building it.' % (name, role))
        else:
            text = ('%s on Simulacrum. See the career-to-wealth simulation behind '
                    'this bio and the AI agents building it.' % name)
    if len(text) <= MAX_DESCRIPTION:
        return text
    return text[:MAX_DESCRIPTION - 1].rstrip() + '…'


def is_indexable(profile, bio_page) -> bool:
    """Whether crawlers should index this bio, and the sitemap should list it.

    Four ways to be excluded, any one of which is enough:
      - no published bio page (a draft is the owner's preview, not a document)
      - the member set the page to noindex
      - the member set the profile to noindex
      - the profile is in Private Mode, where strangers only ever see a teaser
    """
    if bio_page is None or profile is None:
        return False
    try:
        if bio_page.status != bio_page.STATUS_PUBLISHED:
            return False
        if bio_page.sections.get('visibility', {}).get('noindex'):
            return False
        if getattr(profile, 'noindex', False):
            return False
        if getattr(profile, 'is_private', False):
            return False
    except Exception:
        logger.warning('bio_seo: indexability check failed for %s',
                       getattr(bio_page, 'slug', '?'), exc_info=True)
        return False
    return True


def canonical_origin(fallback: str = '') -> str:
    """The one origin every canonical, og:url and sitemap entry uses.

    A canonical built from the request host varies with however the visitor
    arrived — www or bare, http or https — which is exactly the duplicate-URL
    problem canonicals exist to solve. Sitemaps are stricter still: every URL
    must sit on the same host as the sitemap itself, so one configured origin is
    the only workable answer. BASE_URL already backs every OAuth redirect, so it
    is the origin the deployment is actually identified by.
    """
    try:
        from flask import current_app
        configured = (current_app.config.get('BASE_URL') or '').strip()
        if configured:
            return configured.rstrip('/')
    except Exception:
        pass
    return (fallback or '').rstrip('/')


def bio_url(root: str, slug: str) -> str:
    return '%s/u/%s' % (canonical_origin(root), slug)


def seo_context(profile, bio_page, ctx: dict, root: str, slug: str) -> dict:
    """Everything the bio template's <head> and breadcrumb need."""
    full_name = (ctx or {}).get('full_name', '')
    first, last = name_parts(full_name)
    return {
        'title': page_title(full_name),
        'description': meta_description(
            full_name,
            (ctx or {}).get('professional_title', ''),
            (ctx or {}).get('positioning', ''),
            (ctx or {}).get('about_text', ''),
        ),
        'display_name': display_name(full_name),
        'first_name': first,
        'last_name': last,
        'indexable': is_indexable(profile, bio_page),
        'canonical': bio_url(root, slug),
        'origin': canonical_origin(root),
        'site_name': SITE_NAME,
    }


def indexable_bios() -> list:
    """Every bio the sitemap should advertise: [{slug, updated_at}, ...].

    Ordered by most recently updated so the freshest pages sit at the top of the
    sitemap, which is what a crawler reads first on a partial fetch.
    """
    from app.models.bio_page import BioPage
    from app.models.profile import UserProfile
    from app.models.user import User
    from app.extensions import db

    out = []
    rows = db.session.query(BioPage, UserProfile).join(
        UserProfile, BioPage.user_id == UserProfile.user_id,
    ).filter(
        BioPage.status == BioPage.STATUS_PUBLISHED,
    ).order_by(BioPage.updated_at.desc()).all()

    for bio_page, profile in rows:
        if not is_indexable(profile, bio_page):
            continue
        # A deleted owner leaves the row behind; the page 404s in spirit even if
        # the route still answers, so it has no business in the sitemap.
        user = User.query.get(bio_page.user_id)
        if not user or user.deleted_at:
            continue
        out.append({
            'slug': bio_page.slug,
            'updated_at': bio_page.updated_at,
        })
    return out
