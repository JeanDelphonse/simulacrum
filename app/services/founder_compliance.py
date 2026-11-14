"""Founder Track output rules (FOUNDER_PLAN.md sections 1.5 and 5).

Every founder agent output is checked before it is stored or shown:

  * negotiation or deal structuring (agents never negotiate, accept terms,
    commit spend or move money),
  * projections of investor returns, valuations or the odds of raising,
  * legal, tax or investment advice given as advice,
  * general solicitation of investors while the venture's offering exemption
    is 506(b) or not yet decided (only 506(c) allows it, and only with
    verified accredited investors),
  * the banned earnings-claim phrases in config/compliance_phrases.json
    (read through compliance_filter.banned_phrases, never copied here).

    issues = founder_compliance.check(text, layer, exemption='undecided')
    -> [{'rule': 'return_projection', 'excerpt': '...'}]
    text = founder_compliance.ensure_disclaimer(text)

The caller regenerates once with stricter_rules(issues) and falls back to
safe_note() when the second answer still breaks a rule. This is a product
guardrail, not legal review.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

RULE_LABELS = {
    'banned_phrase': 'earnings-claim wording',
    'negotiation': 'negotiating or accepting terms',
    'structuring': 'structuring a deal',
    'money_movement': 'committing spend or moving money',
    'return_projection': 'projecting investor returns or the odds of raising',
    'advice': 'legal, tax or investment advice',
    'general_solicitation': 'public solicitation of investors',
}

_NEGOTIATION = re.compile(
    r"\b(?:we|i)\s+(?:hereby\s+)?(?:accept|agree\s+to|approve|sign\s+off\s+on)\s+(?:your|the|these|this|their)\s+"
    r"(?:terms?|term\s+sheet|offer|valuation|cap|investment)\b"
    r"|\bcounter[- ]?offer\b"
    r"|\b(?:we|i)\s+(?:can|could|will|would)\s+(?:go|come)\s+(?:down|up)\s+to\b"
    r"|\b(?:our|my)\s+(?:final|best)\s+offer\b"
    r"|\b(?:let'?s|we\s+should)\s+negotiate\b", re.I)

_STRUCTURING = re.compile(
    r"\b(?:pre|post)[- ]money\s+valuation\s+(?:of|at|is|will\s+be)\s+\$?\d"
    r"|\bvaluation\s+cap\s+(?:of|at)\s+\$?\d"
    r"|\b(?:discount\s+rate|liquidation\s+preference|interest\s+rate)\s+(?:of|at)\s+\d"
    r"|\b(?:we|you)\s+(?:should|will)\s+(?:issue|offer)\s+(?:a\s+)?(?:safe|convertible\s+note|preferred\s+shares?|equity)\s+"
    r"(?:to|at|with)\b", re.I)

_MONEY = re.compile(
    r"\b(?:i|we)\s+(?:have\s+)?(?:wired|transferred|paid|sent\s+the\s+funds?|charged\s+your\s+card)\b"
    r"|\bwire\s+the\s+funds?\s+to\b"
    r"|\bsend\s+(?:the\s+)?(?:money|payment|funds?)\s+to\s+(?:this|our|the\s+following)\s+account\b", re.I)

_RETURNS = re.compile(
    r"\b\d+(?:\.\d+)?\s?x\s+(?:return|returns|multiple|on\s+(?:your|their)\s+(?:money|investment))\b"
    r"|\b(?:expected|projected|target|forecast)\s+(?:irr|roi|returns?)\b"
    r"|\birr\s+of\s+\d"
    r"|\binvestors?\s+(?:will|would|can\s+expect\s+to|are\s+likely\s+to)\s+(?:make|earn|get|see|double|triple)\b"
    r"|\byou\s+(?:will|would)\s+(?:make|earn|double|triple)\s+your\s+(?:money|investment)\b"
    r"|\b(?:will|is\s+going\s+to)\s+(?:be\s+worth|reach\s+a\s+valuation\s+of)\s+\$?\d"
    r"|\b\d{1,3}\s?%\s+(?:chance|likelihood|probability)\s+(?:of|that\s+you)\s+(?:raising|closing|getting\s+funded)\b"
    r"|\b(?:you|the\s+company)\s+(?:will|are\s+certain\s+to)\s+(?:raise|close\s+the\s+round|get\s+funded)\b", re.I)

_ADVICE = re.compile(
    r"\b(?:my|our|this\s+is)\s+(?:legal|tax|investment|securities)\s+advice\b"
    r"|\b(?:i|we)\s+(?:advise|recommend)\s+(?:that\s+)?(?:you|investors?)\s+(?:invest|buy|sell)\b"
    r"|\byou\s+(?:should|must)\s+(?:invest|buy\s+shares|sell\s+shares)\b"
    r"|\byou\s+do\s+not\s+need\s+(?:a\s+|an\s+)?(?:lawyer|attorney|accountant|cpa)\b", re.I)

# Language that offers the raise to the public. Under 506(b) (or before the
# exemption is decided) agents may reach only existing relationships and
# published application forms.
_SOLICITATION = re.compile(
    r"\b(?:post|share|announce|publish|broadcast|tweet)\s+(?:this|it|the\s+raise|our\s+raise|our\s+round|that\s+we\s+are\s+raising)"
    r"[^.\n]{0,40}\b(?:publicly|linkedin|twitter|x\.com|facebook|reddit|newsletter|social\s+media|everyone|your\s+network)\b"
    r"|\b(?:anyone|everyone|all\s+investors?)\s+(?:interested\s+in|who\s+wants\s+to|can)\s+invest\b"
    r"|\b(?:open|opening)\s+(?:our\s+|the\s+)?round\s+to\s+(?:the\s+public|everyone|anyone)\b"
    r"|\b(?:invest\s+in\s+us|now\s+accepting\s+investments?|investment\s+opportunity\s+open\s+to)\b"
    r"|\b(?:crowdfund(?:ing)?\s+campaign|public\s+offering)\b", re.I)

_PATTERNS = (('negotiation', _NEGOTIATION), ('structuring', _STRUCTURING), ('money_movement', _MONEY),
             ('return_projection', _RETURNS), ('advice', _ADVICE))

SOLICITATION_EXEMPTIONS = (None, '', '506b', 'undecided')


def _issue(rule: str, excerpt: str) -> dict:
    # 'kind' / 'match' are aliases other packages read.
    return {'rule': rule, 'label': RULE_LABELS[rule], 'excerpt': excerpt, 'kind': rule, 'match': excerpt}


def _excerpt(text: str, m) -> str:
    a, b = max(0, m.start() - 60), min(len(text), m.end() + 60)
    return ' '.join(text[a:b].split())[:300]


def check(text: str, layer=None, exemption=None) -> list:
    """[{'rule', 'label', 'excerpt', 'kind', 'match'}] for every founder rule the text breaks.
    exemption is the venture's recorded offering exemption (506b / 506c /
    undecided); None reads as undecided."""
    text = text or ''
    issues = []
    try:
        from app.services import compliance_filter
        for phrase in compliance_filter.banned_phrases(text):
            issues.append(_issue('banned_phrase', phrase))
    except Exception as exc:
        logger.error('banned-phrase check failed: %s', exc)
    for rule, rx in _PATTERNS:
        for m in rx.finditer(text):
            issues.append(_issue(rule, _excerpt(text, m)))
    if exemption in SOLICITATION_EXEMPTIONS:
        for m in _SOLICITATION.finditer(text):
            issues.append(_issue('general_solicitation', _excerpt(text, m)))
    return issues


def exemption_for(venture) -> str:
    """The venture's recorded exemption, or 'undecided'."""
    try:
        from app.models.founder import ComplianceProfile
        row = ComplianceProfile.query.filter_by(venture_id=venture.id).first()
        return row.exemption if row and row.exemption else 'undecided'
    except Exception:
        return 'undecided'


def stricter_rules(issues: list) -> str:
    """Extra instructions for the one regeneration after a failed check."""
    broken = sorted({i['rule'] for i in issues or []})
    lines = ['Your previous answer broke these SimiSI rules: %s.' % ', '.join(RULE_LABELS.get(r, r) for r in broken),
             'Rewrite it so that it:',
             '- does not negotiate, accept or propose deal terms, valuations, caps or discounts;',
             '- does not commit spend or ask anyone to move money;',
             '- does not project investor returns, valuations or the odds of raising money;',
             '- gives no legal, tax or investment advice (name the professional to ask instead);',
             '- does not invite the public to invest or suggest posting the raise publicly;',
             '- avoids promises about results or earnings.']
    flagged = [i['excerpt'] for i in issues or [] if i.get('excerpt')][:5]
    if flagged:
        lines.append('Passages that broke a rule: ' + ' | '.join('"%s"' % f[:160] for f in flagged))
    return '\n'.join(lines)


def safe_note(agent_label: str) -> str:
    return ('The %s agent drafted an answer that did not pass SimiSI\'s founder rules (no negotiating terms, '
            'no return projections, no legal, tax or investment advice, no public solicitation of investors), '
            'so it was withheld. Nothing was sent. Run the step again with more context, or complete it '
            'yourself and add your evidence.' % (agent_label or 'founder'))


def ensure_disclaimer(text: str, layer=None) -> str:
    """Append the founder_not_advice legal text once."""
    try:
        from app.services import legal_text
        note = legal_text.get('founder_not_advice')
    except Exception:
        note = ''
    if note and note not in (text or ''):
        return (text or '').rstrip() + '\n\n---\n' + note
    return text or ''


def log(user_id, agent_key: str, issues: list, outcome: str) -> None:
    """Record each broken rule as a compliance_events row (shared table)."""
    if not issues:
        return
    try:
        from app.services import compliance_filter
        compliance_filter.log_event(user_id, agent_key, [('fnd_' + i['rule'], i.get('excerpt') or '')
                                                         for i in issues], outcome)
    except Exception as exc:
        logger.error('founder compliance log failed: %s', exc)
