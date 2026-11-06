"""Claims helpers (COMPLIANCE_PLAN A5)."""
from __future__ import annotations

import re

# A testimonial that talks about money, clients or results (any figure counts:
# "50 emails a day", "3 clients") needs the typical-results disclosure from
# the legal-text source.
_RESULTS = re.compile(
    r"(\d|\d\s?(k|K)\b|\bdollars?\b|\bincome\b|\brevenue\b|\bearn(ed|ing|s)?\b|\bprofit|"
    r"\bsales?\b|\bpaid\b|\bclients?\b|\bcustomers?\b|\bleads?\b|\bdeals?\b|\bbooked\b|\bhired\b|"
    r"\bjob offer|\bresults?\b|\broi\b|\b\d+\s?(x|%)\b)", re.I)


def mentions_results(text) -> bool:
    return bool(text) and bool(_RESULTS.search(str(text)))
