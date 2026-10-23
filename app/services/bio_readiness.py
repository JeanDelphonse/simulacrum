"""
Bio page source-data readiness.

A bio page is a public, factual document about a real person. It may only be
generated, and only be published, when there is enough real source material
behind it. Before this module the generator ran on a name alone and the publish
endpoint checked nothing, so a user who skipped the resume step in onboarding
got a fabricated biography published under their name.

What counts as source (product decision, 2026-10-22):

  - an uploaded resume (PDF/DOCX), or a LinkedIn import, whose text passes the
    structural checks below; or
  - career history the user entered themselves, with at least
    bio_ready_min_manual_roles entries naming both an employer and a role.

What never counts:

  - the generated bio itself, and anything extracted from it. Career entries
    imported from bio text carry source='bio' and are excluded, which closes
    the loop where invented employers became "career history" and then looked
    like evidence.
  - simulation expertise zones. They describe skills, not employment history.

Every decision here is deterministic: no model call sits on the publish path,
so publishing cannot fail on an API error and the outcome is reproducible.

Settings live in platform_settings under bio_ready_*, declared in SETTINGS_SPEC.
"""
from __future__ import annotations

import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

SETTINGS_SPEC = {
    'bio_ready_min_chars': (
        'int', 600, 'Minimum resume text',
        'Characters of readable text a resume or LinkedIn import needs before it '
        'can support a bio.'),
    'bio_ready_min_roles': (
        'int', 1, 'Minimum dated roles in a resume',
        'Date ranges (e.g. "2019 - Present") the resume text must contain. Each '
        'one is a role the bio can state as fact.'),
    'bio_ready_min_signals': (
        'int', 2, 'Minimum resume structure signals',
        'Out of four: dated roles, an experience heading, an education heading, '
        'contact details. Separates a resume from a cover letter or a stray document.'),
    'bio_ready_min_manual_roles': (
        'int', 2, 'Minimum self-entered career entries',
        'Career history entries (employer and role) a user without a resume must '
        'enter themselves before a bio can be generated or published.'),
    'bio_ready_unreadable_chars': (
        'int', 200, 'Unreadable upload threshold',
        'A resume upload with less readable text than this is refused at upload, '
        'usually because it is a scanned image.'),
    'bio_ready_grounding_check': (
        'bool', True, 'Check generated bios against the source',
        'After generating, verify every employer, title, date and outcome against '
        'the source and remove anything unsupported.'),
}

_TRUE = ('1', 'true', 'yes', 'on', 'True')


def setting(key):
    """Read one readiness setting, coerced to the type declared in SETTINGS_SPEC."""
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
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def all_settings():
    return [
        {'key': key, 'type': spec[0], 'value': setting(key), 'default': spec[1],
         'label': spec[2], 'help': spec[3]}
        for key, spec in SETTINGS_SPEC.items()
    ]


def save_setting(key, value, updated_by=None):
    """Validate and persist one setting. Returns the coerced value."""
    from app.models.platform_settings import PlatformSetting

    if key not in SETTINGS_SPEC:
        raise ValueError('Unknown setting: %s' % key)
    kind, label = SETTINGS_SPEC[key][0], SETTINGS_SPEC[key][2]
    if kind == 'bool':
        stored = '1' if (value is True or str(value) in _TRUE) else '0'
    else:
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError('%s must be a whole number.' % label)
        if number < 0:
            raise ValueError('%s cannot be negative.' % label)
        if key == 'bio_ready_min_signals' and number > 4:
            raise ValueError('%s is out of four, so it cannot exceed 4.' % label)
        stored = str(number)
    PlatformSetting.set(key, stored, updated_by=updated_by)
    return setting(key)


# ---------------------------------------------------------------------------
# Resume text signals
# ---------------------------------------------------------------------------

_MONTH = (r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|'
          r'aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?')
_YEAR = r'(?:19[5-9]\d|20\d\d)'
_POINT = r'(?:%s\s+%s|\d{1,2}/%s|%s)' % (_MONTH, _YEAR, _YEAR, _YEAR)
_END = r'(?:%s|present|current|now|today|date)' % _POINT
# "2019 - 2022", "Jan 2019 – Present", "03/2018 to 06/2021"
_DATE_RANGE = re.compile(r'\b%s\s*(?:-|–|—|to|until)\s*%s\b' % (_POINT, _END), re.IGNORECASE)

_EXPERIENCE = re.compile(
    r'^\s*(?:professional\s+|work\s+|relevant\s+)?(?:experience|employment(?:\s+history)?|'
    r'work\s+history|career\s+history|professional\s+background)\b',
    re.IGNORECASE | re.MULTILINE)
_EDUCATION = re.compile(
    r'^\s*(?:education|academic\s+background|qualifications|degrees?)\b'
    r'|\b(?:university|college|institute\s+of|school\s+of|b\.?\s?sc|b\.?\s?a\.?|m\.?\s?sc|'
    r'mba|ph\.?\s?d|bachelor|master)\b',
    re.IGNORECASE | re.MULTILINE)
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_PHONE = re.compile(r'(?:\+?\d[\d\s().-]{8,}\d)')
_LINKEDIN = re.compile(r'linkedin\.com/in/', re.IGNORECASE)


def resume_signals(text):
    """Structural evidence that `text` is a resume with facts a bio can state.

    Deliberately structural rather than semantic: it asks whether the document
    has the shape of employment history, not whether a model thinks it is good.
    """
    text = text or ''
    stripped = re.sub(r'\s+', ' ', text).strip()
    ranges = {m.group(0).lower() for m in _DATE_RANGE.finditer(text)}
    signals = {
        'dated_roles':  len(ranges) > 0,
        'experience':   bool(_EXPERIENCE.search(text)),
        'education':    bool(_EDUCATION.search(text)),
        'contact':      bool(_EMAIL.search(text) or _PHONE.search(text) or _LINKEDIN.search(text)),
    }
    return {
        'chars':         len(stripped),
        'date_ranges':   len(ranges),
        'signals':       signals,
        'signal_count':  sum(1 for v in signals.values() if v),
    }


def assess_resume_text(text):
    """Judge one resume's text. Returns a dict with `sufficient` and `reasons`."""
    found = resume_signals(text)
    reasons = []
    min_chars = setting('bio_ready_min_chars')
    min_roles = setting('bio_ready_min_roles')
    min_signals = setting('bio_ready_min_signals')

    unreadable = found['chars'] < setting('bio_ready_unreadable_chars')
    if unreadable:
        reasons.append('We could read almost no text from it — it may be a scanned image. '
                       'Upload a text-based PDF or Word file.')
    elif found['chars'] < min_chars:
        reasons.append('It is too short to support a factual bio (%d of %d characters).'
                       % (found['chars'], min_chars))
    if not unreadable and found['date_ranges'] < min_roles:
        reasons.append('It has no dated roles (like "2019 – Present"), so there is no '
                       'employment history a bio can state as fact.'
                       if min_roles <= 1 else
                       'It has %d dated role%s; at least %d are needed.'
                       % (found['date_ranges'], '' if found['date_ranges'] == 1 else 's', min_roles))
    if not unreadable and found['signal_count'] < min_signals:
        reasons.append("It doesn't look like a resume — it lacks the usual experience, "
                       'education and contact sections.')

    found['unreadable'] = unreadable
    found['sufficient'] = not reasons
    found['reasons'] = reasons
    return found


# ---------------------------------------------------------------------------
# Career history provenance
# ---------------------------------------------------------------------------

CAREER_SOURCES = ('resume', 'linkedin', 'manual', 'bio')


def _career_key(entry):
    return (re.sub(r'\s+', ' ', str(entry.get('company_name') or '')).strip().lower(),
            re.sub(r'\s+', ' ', str(entry.get('role') or '')).strip().lower())


def merge_career_provenance(existing, incoming):
    """Carry each entry's `source` across a save.

    The Settings editor rebuilds entries from form fields, so a tag the server
    set at import time would be lost on the next save. An incoming entry that
    matches a stored one (same employer and role) inherits its source; an
    explicit allowed source on the incoming entry is kept; anything else is
    something the user typed, so it is 'manual'.
    """
    stored = {}
    for entry in existing or []:
        if isinstance(entry, dict) and entry.get('source') in CAREER_SOURCES:
            stored.setdefault(_career_key(entry), entry['source'])

    merged = []
    for entry in incoming or []:
        if not isinstance(entry, dict):
            continue
        entry = dict(entry)
        if entry.get('source') not in CAREER_SOURCES:
            entry['source'] = stored.get(_career_key(entry), 'manual')
        merged.append(entry)
    return merged


def tag_entries(entries, source):
    """Stamp `source` on freshly extracted entries."""
    return [dict(e, source=source) for e in (entries or []) if isinstance(e, dict)]


def _effective_source(entry, profile_bio):
    """An entry's provenance, inferring it for entries saved before tagging existed.

    An untagged entry whose employer appears in the profile's bio text is
    treated as extracted from that bio. Before this change the onboarding
    wizard did exactly that, and the generator never read career history, so a
    match means the entry came from the bio rather than the other way round.
    """
    source = entry.get('source')
    if source in CAREER_SOURCES:
        return source
    company = str(entry.get('company_name') or '').strip().lower()
    if company and profile_bio and company in profile_bio.lower():
        return 'bio'
    return 'manual'


def qualifying_career_entries(profile):
    """Career entries that count as source: named employer and role, not bio-derived."""
    if not profile:
        return []
    bio = profile.bio or ''
    return [
        e for e in (profile.career_history or [])
        if isinstance(e, dict)
        and str(e.get('company_name') or '').strip()
        and str(e.get('role') or '').strip()
        and _effective_source(e, bio) != 'bio'
    ]


def _manual_source_text(profile, entries):
    """Render self-entered career history (and education) as source text for the generator."""
    lines = ['CAREER HISTORY (entered by the person):']
    for e in entries:
        span = ' – '.join(x for x in (str(e.get('start_date') or '').strip(),
                                      str(e.get('end_date') or '').strip()) if x)
        line = '- %s, %s' % (str(e.get('role')).strip(), str(e.get('company_name')).strip())
        if span:
            line += ' (%s)' % span
        if str(e.get('description') or '').strip():
            line += ': ' + str(e['description']).strip()
        lines.append(line)
    education = [x for x in (profile.education or []) if isinstance(x, dict)]
    if education:
        lines.append('')
        lines.append('EDUCATION (entered by the person):')
        for x in education:
            parts = [str(x.get(k) or '').strip()
                     for k in ('degree', 'field', 'institution_name', 'year')]
            parts = [p for p in parts if p]
            if parts:
                lines.append('- ' + ', '.join(parts))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

def override_for(bio_page):
    """The admin's 'keep live' decision for this page, if one was recorded."""
    if not bio_page:
        return None
    value = (bio_page.sections or {}).get('readiness_override')
    return value if isinstance(value, dict) and value.get('at') else None


def readiness(user_id):
    """Whether a user has enough real source data for a bio.

    Returns a dict:
      ready        - a bio may be generated (and published) from real source
      can_publish  - ready, or an admin chose to keep this page live anyway
      source       - 'resume' | 'linkedin' | 'manual' | None
      reasons      - what is missing, in words the user can act on
      checklist    - the two routes to readiness, each with done/needed state
      source_text  - the material the generator must use (never sent to the client)
    """
    from app.models.bio_page import BioPage
    from app.models.profile import UserProfile
    from app.models.resume import Resume

    profile = UserProfile.query.filter_by(user_id=user_id).first()
    bio_page = BioPage.query.filter_by(user_id=user_id).first()
    resumes = (Resume.query.filter_by(user_id=user_id)
               .order_by(Resume.created_at.desc()).all())

    best, latest = None, None
    for resume in resumes:
        verdict = assess_resume_text(resume.parsed_text)
        verdict['resume_id'] = resume.id
        verdict['label'] = resume.label
        verdict['kind'] = 'linkedin' if resume.source == 'linkedin' else 'resume'
        if latest is None:
            latest = verdict
        if verdict['sufficient']:
            best = (resume, verdict)
            break

    manual = qualifying_career_entries(profile)
    manual_needed = setting('bio_ready_min_manual_roles')
    manual_ok = manual_needed > 0 and len(manual) >= manual_needed

    if best:
        source = best[1]['kind']
        source_text = best[0].parsed_text or ''
    elif manual_ok:
        source = 'manual'
        source_text = _manual_source_text(profile, manual)
    else:
        source, source_text = None, ''

    reasons = []
    if not source:
        if latest is None:
            reasons.append('No resume or LinkedIn profile has been added yet.')
        else:
            what = 'LinkedIn import' if latest['kind'] == 'linkedin' else 'resume'
            reasons.extend('Your %s: %s' % (what, r[0].lower() + r[1:])
                           for r in latest['reasons'])
        reasons.append(
            'Or add your career history yourself in Settings: %d of %d entries '
            'with an employer and a role.' % (len(manual), manual_needed))

    override = override_for(bio_page)
    return {
        'ready':        source is not None,
        'can_publish':  source is not None or override is not None,
        'source':       source,
        'override':     override,
        'reasons':      reasons,
        'resume':       ({k: latest[k] for k in ('resume_id', 'label', 'kind', 'chars',
                                                 'date_ranges', 'signal_count', 'sufficient',
                                                 'reasons')}
                         if latest else None),
        'manual_roles': len(manual),
        'manual_required': manual_needed,
        'checklist': [
            {'key': 'resume', 'label': 'Upload a resume or import your LinkedIn profile',
             'done': bool(best), 'href': '/resumes'},
            {'key': 'manual',
             'label': 'Or enter at least %d career history entries yourself' % manual_needed,
             'done': manual_ok, 'href': '/settings/profile#career'},
        ],
        'source_text':  source_text,
    }


def public(state):
    """The readiness dict minus the source text, for JSON responses."""
    return {k: v for k, v in state.items() if k != 'source_text'}


def not_ready_message(state):
    return ('Your bio page needs real source material before it can be published. '
            + ' '.join(state['reasons']))


# ---------------------------------------------------------------------------
# Admin review of pages already public
# ---------------------------------------------------------------------------

def publicly_visible(profile, bio_page):
    """Whether a stranger can read this person's bio at /u/<slug>.

    Two routes: a published BioPage, or the legacy profile page, which renders
    profile.bio whenever profile.is_published is set and no published BioPage
    exists. Unpublishing used to leave the legacy flag on.
    """
    from app.models.bio_page import BioPage
    if bio_page and bio_page.status == BioPage.STATUS_PUBLISHED:
        return True
    return bool(profile and profile.is_published)


def review_queue():
    """Every publicly visible bio that would fail the publish gate today."""
    from app.models.bio_page import BioPage
    from app.models.profile import UserProfile
    from app.models.user import User

    profiles = UserProfile.query.all()
    pages = {bp.user_id: bp for bp in BioPage.query.all()}
    users = {u.id: u for u in User.query.filter(
        User.id.in_([p.user_id for p in profiles])).all()} if profiles else {}

    flagged, kept = [], []
    for profile in profiles:
        user = users.get(profile.user_id)
        if not user or user.deleted_at:
            continue
        page = pages.get(profile.user_id)
        if not publicly_visible(profile, page):
            continue
        state = readiness(profile.user_id)
        if state['ready']:
            continue
        row = {
            'user_id':      profile.user_id,
            'name':         profile.display_name or user.full_name,
            'email':        user.email,
            'slug':         profile.username,
            'via':          ('bio page' if page and page.status == BioPage.STATUS_PUBLISHED
                             else 'legacy profile page'),
            'published_at': (page.published_at.isoformat()
                             if page and page.published_at else None),
            'bio_words':    len((profile.bio or '').split()),
            'reasons':      state['reasons'],
            'resume':       state['resume'],
            'manual_roles': state['manual_roles'],
            'override':     state['override'],
        }
        (kept if state['override'] else flagged).append(row)
    flagged.sort(key=lambda r: r['published_at'] or '', reverse=True)
    return {'flagged': flagged, 'kept': kept}


def keep_live(bio_page, admin_user, note=None):
    sections = dict(bio_page.sections or {})
    sections['readiness_override'] = {
        'by': admin_user.id,
        'by_name': admin_user.full_name,
        'at': datetime.utcnow().isoformat(),
        'note': (note or '').strip()[:500] or None,
    }
    bio_page.sections = sections


def clear_override(bio_page):
    sections = dict(bio_page.sections or {})
    sections.pop('readiness_override', None)
    bio_page.sections = sections
