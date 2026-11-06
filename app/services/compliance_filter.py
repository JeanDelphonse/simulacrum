"""Layer 5 output filter (COMPLIANCE_PLAN B3).

Before a Layer 5 artifact is shown or saved it is checked for named securities
(tickers, cash-tags), fund families, brokerages and crypto assets, and for
imperative trade language ("buy…", "you should allocate $…"). Education is
fine: "a common approach is…", "in a scenario where…".

On a hit the caller regenerates once with the stricter rules; on a second hit
it saves the safe fallback message instead. Every hit is a compliance_events
row. The word lists live in config/compliance_phrases.json.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

_CFG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'compliance_phrases.json')
_cfg_cache: dict = {}

_CASHTAG = re.compile(r'(?<![\w$])\$[A-Z]{1,5}\b')
_TRADE_VERB = r'(buy|sell|short|purchase|allocate|invest|put|move|transfer|rebalance)'
_AMOUNT = r'(\$\s?\d[\d,]*(\.\d+)?|\d+(\.\d+)?\s?%|\d+\s+shares?\b|shares?\s+of\b)'
# An instruction: at the start of a sentence or list item, or after "you should".
_IMPERATIVE = re.compile(
    r'(?:^|[.!?:]\s+|\n[ \t>*\-\d.)]*)' + _TRADE_VERB + r'\b[^.\n]{0,60}?' + _AMOUNT, re.I)
_ADVICE = re.compile(
    r'\b(you should|you must|you need to|i recommend( that you)?|we recommend( that you)?|i suggest( you)?)\s+'
    + _TRADE_VERB + r'\b', re.I)


def _cfg() -> dict:
    if not _cfg_cache:
        with io.open(os.path.abspath(_CFG_PATH), encoding='utf-8') as fh:
            _cfg_cache.update(json.load(fh))
    return _cfg_cache


def check_l5(text: str) -> list:
    """[(rule, excerpt)] for every education-only rule the text breaks."""
    text = text or ''
    hits = []

    def add(rule, m):
        a, b = max(0, m.start() - 60), min(len(text), m.end() + 60)
        hits.append((rule, ' '.join(text[a:b].split())[:300]))

    for m in _CASHTAG.finditer(text):
        add('cashtag', m)
    tickers = _cfg().get('l5_tickers', [])
    if tickers:
        for m in re.finditer(r'(?<![\w.$])(' + '|'.join(re.escape(t) for t in tickers) + r')(?![\w])', text):
            add('ticker', m)
    names = _cfg().get('l5_products', [])
    if names:
        for m in re.finditer(r'(?<![\w])(' + '|'.join(re.escape(n) for n in names) + r')(?![\w])', text, re.I):
            add('named_product', m)
    for m in _IMPERATIVE.finditer(text):
        add('trade_instruction', m)
    for m in _ADVICE.finditer(text):
        add('trade_instruction', m)
    return hits


def log_event(user_id, agent: str, hits: list, outcome: str) -> None:
    """One compliance_events row per breach (rule and a short excerpt)."""
    from app.extensions import db
    from app.models.compliance import ComplianceEvent
    try:
        seen = set()
        for rule, excerpt in hits:
            if rule in seen:
                continue
            seen.add(rule)
            db.session.add(ComplianceEvent(user_id=user_id, agent=agent, rule=rule, excerpt=excerpt[:300],
                                           outcome=outcome))
        db.session.commit()
    except Exception:
        logger.exception('compliance event not recorded')
        db.session.rollback()


def ensure_referral(text: str, referral: str) -> str:
    """Every Layer 5 output ends with its referral line."""
    if referral and referral not in (text or ''):
        return (text or '').rstrip() + '\n\n---\n*' + referral + '*'
    return text
