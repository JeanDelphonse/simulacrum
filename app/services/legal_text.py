"""The one source for disclaimer, disclosure and claims wording (hard rule 2).

Templates call the Jinja global ``legal('key', **values)``; Python (agent
prompts, emails, the Layer 5 fallback) calls ``legal_text.get('key', **values)``.
Nothing else may carry this wording: tests fail on copies pasted elsewhere.

Every default below is a DRAFT for counsel (COUNSEL_REVIEW.md). Rows are seeded
with status 'draft'; an admin marks one 'counsel_approved' from
Admin → Legal text after sign-off. Code never sets the approved status, and
editing an approved text puts it back to draft.

Placeholders use str.format names ({sender_name}); a missing value renders as
an empty string rather than raising.
"""
from __future__ import annotations

import logging
import string
from datetime import datetime

logger = logging.getLogger(__name__)

# key -> (label for Admin, default body)
DEFAULTS = {
    'estimate_label': ('Next to every projected income range', 'Estimate, not a promise'),
    'estimate_basis_intro': (
        'Top of the "How we estimated this" panel',
        'This range is a model estimate based on the assumptions and sources below. It is not a '
        'forecast of what you will earn. Your results depend on your field, effort, market and many '
        'factors we cannot see.'),
    'base_rate_l3_l4': (
        'Layers 3 and 4 estimates only',
        'Results for courses, products, newsletters and similar offers vary widely. Most people who '
        'try earn less than the upper end of this range, and many earn little or nothing.'),
    'self_reported_label': ('Income a member recorded themselves',
                            'Self-reported by the member, not verified by SimiSI'),
    'typical_results': (
        'Under any testimonial that mentions money, clients or results',
        'Individual experience. Results vary and are not typical; most members earn less. '
        'Testimonials are from real members and were not paid for.'),
    'typical_results_member': (
        "Under a member's own bio-page testimonial that mentions results",
        'Individual experience; results vary.'),
    'illustrative_label': ('Case studies and sample runs',
                           "Illustrative case study: a composite example, not a real customer's results."),
    'share_roi_disclosure': ('Public share-ROI card',
                             'Self-reported by the member and not verified. Results vary; most members earn less.'),
    'l5_banner': (
        'Top of every Layer 5 result',
        'Education and planning only, not investment, tax or legal advice. SimiSI is not a '
        'registered investment adviser, broker-dealer, CPA or law firm.'),
    'l5_note_short': ('Short Layer 5 note (home page, integrations, settings)',
                      'Education and planning only. These agents explain options; they never trade or move money.'),
    'l5_referral_adviser': (
        'End of allocation, projection, fund-type and policy outputs',
        'Before acting on any of this, talk to a fee-only fiduciary financial adviser who can look '
        'at your full situation.'),
    'l5_referral_cpa': ('End of tax and entity outputs',
                        'Before filing or changing your entity, review this with a CPA or enrolled agent.'),
    'l5_referral_attorney': (
        'End of estate and entity outputs',
        'Wills, trusts, beneficiaries and entity documents should be prepared or reviewed by a '
        'licensed attorney in your state.'),
    'l5_agent_rules': (
        'Prompt rules for every Layer 5 agent (not shown to users)',
        'You provide general financial, tax and legal education, not personalised advice. Never name '
        'a security, fund, ticker, crypto asset or financial product to buy or sell. Never tell the '
        'user to buy, sell, or allocate a specific amount or percentage; describe scenarios as general '
        "illustrations ('a common approach is…', 'in a scenario where…'). Do not give tax-filing or "
        'legal advice; explain concepts and list questions to ask a professional. Do not promise or '
        'imply returns. End with this exact line: {referral}'),
    'l5_agent_rules_strict': (
        'Stricter Layer 5 rules for the one regeneration after a filter hit',
        'Your previous draft named a specific product or told the user to trade. Rewrite it as pure '
        'education: no fund, ETF, stock, ticker, crypto asset, brokerage or product name anywhere; no '
        'verbs telling the user to buy, sell, move or allocate money; no amounts or percentages '
        'presented as what the user should do. Describe categories and trade-offs only.'),
    'l5_fallback': (
        'Shown when a Layer 5 output breaks the rules twice',
        "We couldn't produce this planning document within our education-only rules. Try again, or "
        'take your questions to a fee-only fiduciary adviser, CPA or attorney.'),
    'l5_unavailable': ('Layer 5 switched off by Admin',
                       'Wealth planning agents are temporarily unavailable. Your existing documents are still here.'),
    'email_footer': (
        'Bottom of every outreach and campaign email',
        'Sent by {sender_name} via SimiSI · {sender_postal_address} · You received this because '
        '{sender_name} thought it relevant to your work. Unsubscribe: {unsubscribe_url} · or reply STOP.'),
    'email_reply_optout': ('Reply-to-opt-out line', 'Reply STOP to stop receiving emails from {sender_name}.'),
    'postal_address_required': (
        'Blocks sending until the member sets an address',
        'Add your mailing address in Settings before sending. The law requires a valid postal address '
        'in commercial email.'),
    'campaign_paused': (
        'Automatic campaign pause',
        'We paused this campaign: {bounce_rate}% of recent emails bounced and {complaint_rate}% were '
        'marked as spam, above the safe limits. Check your list, then resume.'),
    'import_source_blocked': (
        'Purchased or scraped list import',
        "We can't import purchased, rented or scraped lists. Import only people you have a "
        'relationship with or who agreed to hear from you.'),
    'linkedin_manual': ('LinkedIn drafts',
                        "SimiSI doesn't post or message on LinkedIn for you. Copy this draft and post or "
                        'send it yourself.'),
    # Founder Track (FOUNDER_PLAN.md). Drafts for counsel.
    'founder_refund_terms': ('Founder Track refund terms',
                             'Refundable within {days} days of payment.'),
    'founder_report_disclaimer': (
        'Founder readiness report and scores',
        'Scores describe what your documents show today. They are not a prediction of funding or of '
        'investor decisions. Each finding cites the passage it comes from, so you can check it.'),
    'founder_not_advice': (
        'Founder agent outputs (legal, tax, finance, fundraising)',
        'This is planning help, not legal, tax or investment advice. Check decisions about your company, '
        'its securities and its taxes with a qualified attorney or CPA.'),
    'startup_claims_note': (
        'Public startup page, next to traction and claims',
        'Information on this page is provided by the founders. Items marked founder-stated have not been '
        'checked against evidence. SimiSI does not verify, endorse or offer any investment.'),
    'investor_update_footer': (
        'Bottom of every investor update email',
        'Sent on behalf of {sender_name} by SimiSI. This update is information, not an offer to sell or a '
        'solicitation to buy securities. Unsubscribe: {unsubscribe_url}'),
}


class _Blank(dict):
    def __missing__(self, key):
        return ''


def _render(body: str, values: dict) -> str:
    try:
        return string.Formatter().vformat(body, (), _Blank(values))
    except (ValueError, IndexError):
        return body


def ensure_seeded() -> None:
    """Insert any missing default as a draft. Never touches an existing row."""
    from app.extensions import db
    from app.models.compliance import LegalText
    have = {k for (k,) in db.session.query(LegalText.key).all()}
    added = False
    for key, (_label, body) in DEFAULTS.items():
        if key not in have:
            db.session.add(LegalText(key=key, body=body, status=LegalText.STATUS_DRAFT))
            added = True
    if added:
        db.session.commit()


def get(key: str, **values) -> str:
    """The current wording for ``key`` (the stored row, else the draft default)."""
    if key not in DEFAULTS:
        raise KeyError('unknown legal text key %r' % key)
    body = DEFAULTS[key][1]
    try:
        from app.models.compliance import LegalText
        row = LegalText.query.filter_by(key=key).first()
        if row and row.body:
            body = row.body
    except Exception:            # table missing before the migration: use the draft
        logger.debug('legal_texts unavailable; using default for %s', key)
    return _render(body, values)


def all_rows() -> list:
    from app.models.compliance import LegalText
    ensure_seeded()
    rows = {r.key: r for r in LegalText.query.all()}
    return [{'key': k, 'label': DEFAULTS[k][0], 'body': rows[k].body, 'status': rows[k].status,
             'version': rows[k].version, 'updated_at': rows[k].updated_at,
             'is_default': rows[k].body == DEFAULTS[k][1]} for k in DEFAULTS if k in rows]


def update(key: str, body: str, admin_id: str) -> None:
    """Edit wording. Any edit makes the text a draft again (counsel re-reviews it)."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.compliance import LegalText
    if key not in DEFAULTS:
        raise KeyError(key)
    body = (body or '').strip()
    if not body:
        raise ValueError('Text cannot be empty.')
    ensure_seeded()
    row = LegalText.query.filter_by(key=key).first()
    old = row.body
    row.body, row.status = body, LegalText.STATUS_DRAFT
    row.version = (row.version or 1) + 1
    row.updated_by, row.updated_at = admin_id, datetime.utcnow()
    AuditLog.log('legal_text_updated', user_id=admin_id, resource_id=row.id,
                 metadata={'key': key, 'version': row.version, 'old': old[:500], 'new': body[:500]})
    db.session.commit()


def set_status(key: str, approved: bool, admin_id: str) -> None:
    """Admin-only: record that counsel approved (or withdrew approval of) a text."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.compliance import LegalText
    ensure_seeded()
    row = LegalText.query.filter_by(key=key).first()
    if not row:
        raise KeyError(key)
    row.status = LegalText.STATUS_APPROVED if approved else LegalText.STATUS_DRAFT
    row.updated_by, row.updated_at = admin_id, datetime.utcnow()
    AuditLog.log('legal_text_status', user_id=admin_id, resource_id=row.id,
                 metadata={'key': key, 'status': row.status, 'version': row.version})
    db.session.commit()
