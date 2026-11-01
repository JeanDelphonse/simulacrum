"""Real-correspondent score for a sender (PRD-SIM-MAIL-001 "Identifying real
(non-spam) contacts"). Pure functions, no I/O.

Bayesian lens: start from a prior (the provider's own classification when it
has one, else even odds) and add each signal's weight in log-odds, then map to
0-100. A small, fixed set of understandable signals (overfitting lens): no
per-user learning in phase 1.

Hard exclusions skip a sender outright, whatever the score: spam/junk folder,
automated senders, Auto-Submitted / Precedence bulk mail, mailing-list mail from
someone the user never wrote to, and known marketing-platform domains.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

WEIGHTS = {
    'user_sent_to': 3.0,          # strongest sign: the user wrote to them -> Warm
    'back_and_forth': 1.0,        # replied in a thread / both directions
    'user_cc': 1.0,
    'in_address_book': 1.5,
    'provider_interacted': 2.0,   # Gmail "Other contacts": people the user interacted with
    'provider_personal': 1.0,     # Gmail Personal / Outlook Focused
    'signature_person': 1.0,
    'name_matches_address': 0.6,
    'provider_promotional': -1.5,  # Promotions / Social / Updates / Forums / Other
    'inbound_single_unanswered': -1.0,
    'many_recipients': -1.2,
    'brand_display_name': -1.5,
    'auth_failed': -3.0,
}
REASON_TEXT = {
    'user_sent_to': 'You have emailed them',
    'back_and_forth': 'Back-and-forth conversation',
    'user_cc': 'You copied them on mail',
    'in_address_book': 'In your saved contacts',
    'provider_interacted': 'Gmail lists them as someone you interacted with',
    'provider_personal': 'Your mail provider files them as personal',
    'signature_person': 'Their signature names a person or title',
    'name_matches_address': 'Name matches the address',
    'provider_promotional': 'Your mail provider files them as promotional',
    'inbound_single_unanswered': 'One inbound message, never answered',
    'many_recipients': 'Sent to many recipients at once',
    'brand_display_name': 'The sender name is a brand, not a person',
    'auth_failed': 'Failed sender authentication (SPF/DKIM/DMARC)',
}

_BRAND_WORDS = re.compile(
    r'\b(team|support|newsletter|news|updates?|notifications?|alerts?|billing|inc|llc|ltd|corp|'
    r'store|shop|official|digest|weekly|daily|hq|app|service|services|customer|care|marketing)\b', re.I)


@dataclass
class SenderFacts:
    email: str
    display_name: str = ''
    in_spam_folder: bool = False
    automated_headers: bool = False        # Auto-Submitted != no, or Precedence bulk/list/junk
    list_headers: bool = False             # List-Unsubscribe / List-Id seen
    user_sent_to: bool = False
    user_cc: bool = False
    received_count: int = 0
    max_recipients: int = 0
    in_address_book: bool = False
    provider_interacted: bool = False
    provider_personal: bool = False
    provider_promotional: bool = False
    signature_person: bool = False
    auth_failed: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class Verdict:
    score: int
    tier: str | None
    decision: str            # import | review | skip
    reasons: list


def local_part(email: str) -> str:
    return (email or '').split('@')[0].lower()


def domain_of(email: str) -> str:
    return (email or '').rsplit('@', 1)[-1].lower() if '@' in (email or '') else ''


def is_automated_address(email: str, patterns: list) -> bool:
    lp = local_part(email)
    return any(lp == p or lp.startswith(p + '-') or lp.startswith(p + '.') or lp.startswith(p + '+')
               or lp.startswith(p + '_') or (len(p) > 6 and p in lp) for p in patterns)


def is_marketing_domain(email: str, domains: list) -> bool:
    d = domain_of(email)
    return any(d == m or d.endswith('.' + m) for m in domains)


def is_role_address(email: str, prefixes: list) -> bool:
    lp = re.split(r'[+.]', local_part(email))[0]
    return lp in prefixes


def looks_like_person(display_name: str) -> bool:
    name = (display_name or '').strip().strip('"\'')
    if not name or '@' in name or _BRAND_WORDS.search(name):
        return False
    parts = [p for p in re.split(r'[\s,]+', name) if p]
    return 1 <= len(parts) <= 4 and all(re.match(r"^[A-Za-zÀ-ÖØ-öø-ÿ'’.\-]+$", p) for p in parts)


def name_matches_address(display_name: str, email: str) -> bool:
    lp = re.sub(r'[^a-z]', '', local_part(email))
    parts = [re.sub(r'[^a-z]', '', p.lower()) for p in re.split(r'[\s,]+', display_name or '') if p]
    parts = [p for p in parts if len(p) >= 2]
    return bool(lp) and any(p in lp for p in parts)


def score_sender(f: SenderFacts, *, automated_patterns: list, marketing_domains: list,
                 role_prefixes: list, import_at: int = 70, review_at: int = 40,
                 prior_logit: float = 0.0) -> Verdict:
    # Hard exclusions: never imported.
    if f.in_spam_folder:
        return Verdict(0, None, 'skip', ['In your spam or junk folder'])
    if is_automated_address(f.email, automated_patterns):
        return Verdict(0, None, 'skip', ['Automated sender address'])
    if f.automated_headers and not f.user_sent_to:
        return Verdict(0, None, 'skip', ['Automated or bulk mail headers'])
    if f.list_headers and not f.user_sent_to:
        return Verdict(0, None, 'skip', ['Mailing-list or marketing mail you never replied to'])
    if is_marketing_domain(f.email, marketing_domains) and not f.user_sent_to:
        return Verdict(0, None, 'skip', ['Sent through an email-marketing platform'])

    fired = []
    if f.user_sent_to:
        fired.append('user_sent_to')
    if f.user_sent_to and f.received_count > 0:
        fired.append('back_and_forth')
    if f.user_cc and not f.user_sent_to:
        fired.append('user_cc')
    if f.in_address_book:
        fired.append('in_address_book')
    if f.provider_interacted:
        fired.append('provider_interacted')
    if f.provider_personal:
        fired.append('provider_personal')
    if f.provider_promotional:
        fired.append('provider_promotional')
    if f.signature_person:
        fired.append('signature_person')
    if looks_like_person(f.display_name) and name_matches_address(f.display_name, f.email):
        fired.append('name_matches_address')
    if (not f.user_sent_to and not f.user_cc and not f.in_address_book and not f.provider_interacted
            and f.received_count <= 1):
        fired.append('inbound_single_unanswered')
    if f.max_recipients >= 10 and not f.user_sent_to:
        fired.append('many_recipients')
    if f.display_name and not looks_like_person(f.display_name) and _BRAND_WORDS.search(f.display_name):
        fired.append('brand_display_name')
    if f.auth_failed:
        fired.append('auth_failed')

    logit = prior_logit + sum(WEIGHTS[k] for k in fired)
    score = int(round(100 / (1 + math.exp(-logit))))
    role = is_role_address(f.email, role_prefixes)
    tier = 'role' if role else ('warm' if f.user_sent_to or f.user_cc else 'known')
    decision = 'import' if score >= import_at else ('review' if score >= review_at else 'skip')
    return Verdict(score, tier, decision, [REASON_TEXT[k] for k in fired])
