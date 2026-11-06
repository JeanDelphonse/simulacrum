"""Where an imported list came from (COMPLIANCE_PLAN C4).

Every import declares its source. Purchased, rented and scraped lists are
refused: outreach goes only to people the member has a relationship with or
who agreed to hear from them. The declaration is kept on each contact.
"""
from __future__ import annotations

SOURCES = {
    'own_contacts': 'My own contacts or clients',
    'event_optin': 'Event or meetup attendees who agreed to be contacted',
    'referrals': 'Referrals or introductions',
    'subscribers': 'People who subscribed or asked to hear from me',
    'purchased': 'Purchased or rented list',
    'scraped': 'Scraped from websites or directories',
}
BLOCKED = {'purchased', 'scraped'}


def validate(key) -> str:
    """The label for an allowed source; ValueError otherwise."""
    from app.services import legal_text
    key = (key or '').strip()
    if key not in SOURCES:
        raise ValueError('Tell us where this list came from before importing it.')
    if key in BLOCKED:
        raise ValueError(legal_text.get('import_source_blocked'))
    return SOURCES[key]


def note(key) -> str:
    return ('List source: %s' % SOURCES[key])[:500]
