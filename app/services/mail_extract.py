"""Field extraction for Email-to-CRM (PRD-SIM-MAIL-001 "Extracting the 12
contact fields" and "How signatures are read").

Order of trust per field: signature, then header, then inference. Phones,
links and emails come from fixed rules; Claude structures only what rules
can't (name, title, company, location) — once per new sender or changed
signature. The signature text is used in memory and discarded; only the
extracted values and the message id are kept.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

logger = logging.getLogger(__name__)

# Confidence by source (contact_field_sources.confidence).
CONF = {'signature_rule': 0.95, 'signature_ai': 0.85, 'address_book': 0.8, 'header': 0.7,
        'domain': 0.55, 'address_guess': 0.3, 'inferred': 0.4}

_SUFFIXES = {'jr', 'jr.', 'sr', 'sr.', 'ii', 'iii', 'iv', 'phd', 'ph.d.', 'md', 'm.d.', 'mba', 'cpa',
             'esq', 'esq.', 'pmp', 'cfa'}
_QUOTE_MARKERS = [
    re.compile(r'^On .{3,200}(wrote|écrit|schrieb|escribió):\s*$', re.I),
    re.compile(r'^-{2,}\s*Original Message\s*-{2,}', re.I),
    re.compile(r'^-{2,}\s*Forwarded message\s*-{2,}', re.I),
    re.compile(r'^Begin forwarded message:', re.I),
    re.compile(r'^From:\s.+', re.I),
    re.compile(r'^_{8,}$'),
]
_SIG_DELIM = re.compile(r'^(--|—|__)\s*$')
_PHONE = re.compile(r'(?:(?P<label>mobile|cell|m|office|o|tel|t|phone|p|direct|d|work|w)\s*[:.]?\s*)?'
                    r'(?P<num>\+?\(?\d[\d\s().\-]{7,}\d)', re.I)
_LINKEDIN = re.compile(r'(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_\-%]+/?', re.I)
_PHONE_LABELS = {'m': 'mobile', 'mobile': 'mobile', 'cell': 'mobile', 'o': 'office', 'office': 'office',
                 'w': 'office', 'work': 'office', 'd': 'direct', 'direct': 'direct'}


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def _cap(s: str) -> str:
    return s if any(ch.isupper() for ch in s[1:]) else s[:1].upper() + s[1:].lower()


def split_name(display: str, email: str = '') -> tuple:
    """(first, last, source, confidence). Handles "Last, First", quotes and
    suffixes; falls back to guessing from the address (low confidence)."""
    name = re.sub(r'\s+', ' ', (display or '').strip().strip('"\'')).strip()
    name = re.sub(r'\(.*?\)', '', name).strip()
    if name and '@' not in name:
        if ',' in name:
            last, _, first = name.partition(',')
            if first.strip().lower().strip('.') in {x.strip('.') for x in _SUFFIXES}:
                name = last.strip()
            else:
                name = first.strip() + ' ' + last.strip()
        parts = [p for p in name.split(' ') if p and p.lower() not in _SUFFIXES]
        if parts:
            first = _cap(parts[0])
            last = ' '.join(_cap(p) for p in parts[1:])
            return first, last, 'header', CONF['header']
    local = (email or '').split('@')[0]
    bits = [b for b in re.split(r'[._\-]+', re.sub(r'\d+', '', local)) if len(b) > 1]
    if len(bits) >= 2:
        return _cap(bits[0]), _cap(bits[-1]), 'header', CONF['address_guess']
    if bits:
        return _cap(bits[0]), '', 'header', CONF['address_guess']
    return '', '', 'header', 0.0


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

def strip_quoted(text: str) -> str:
    """Drop quoted replies and forwarded text."""
    out = []
    for line in (text or '').replace('\r\n', '\n').split('\n'):
        s = line.strip()
        if any(m.match(s) for m in _QUOTE_MARKERS):
            break
        if s.startswith('>'):
            continue
        out.append(line.rstrip())
    return '\n'.join(out).strip()


def signature_window(text: str, max_lines: int = 15) -> list:
    """The last lines of the message (after a '-- ' delimiter if there is one)."""
    lines = [l.strip() for l in strip_quoted(text).split('\n')]
    for i in range(len(lines) - 1, -1, -1):
        if _SIG_DELIM.match(lines[i]):
            lines = lines[i + 1:]
            break
    lines = [l for l in lines if l]
    return lines[-max_lines:]


def signature_hash(lines: list) -> str:
    """Change key for the signature: its trailing lines only, so a different
    greeting or one-line body above an unchanged signature isn't a change."""
    return hashlib.sha256('\n'.join(lines[-6:]).encode('utf-8')).hexdigest()


def to_e164(raw: str, default_cc: str = '1') -> str | None:
    digits = re.sub(r'\D', '', raw or '')
    if (raw or '').strip().startswith('+'):
        return '+' + digits if 8 <= len(digits) <= 15 else None
    if digits.startswith('00') and 10 <= len(digits) <= 17:
        return '+' + digits[2:]
    if len(digits) == 10 and default_cc == '1':
        return '+1' + digits
    if len(digits) == 11 and digits.startswith('1'):
        return '+' + digits
    return None


def rule_fields(lines: list) -> dict:
    """Phones (with labels), LinkedIn URL — fixed rules, no model."""
    out = {'phones': []}
    for line in lines:
        for m in _PHONE.finditer(line):
            e164 = to_e164(m.group('num'))
            if e164 and e164 not in [p['e164'] for p in out['phones']]:
                label = _PHONE_LABELS.get((m.group('label') or '').lower())
                out['phones'].append({'e164': e164, 'label': label})
        m = _LINKEDIN.search(line)
        if m and 'linkedin_url' not in out:
            url = m.group(0).rstrip('/')
            out['linkedin_url'] = url if url.lower().startswith('http') else 'https://' + url
    return out


def looks_like_signature(lines: list) -> bool:
    """At least two short lines, one of which isn't plain prose."""
    short = [l for l in lines if len(l) <= 80]
    return len(short) >= 2 and any(re.search(r'[|•·@]|\d{3}|linkedin|^[A-Z][a-z]+ [A-Z]', l) for l in short)


_PROMPT = """Extract the sender's details from this email signature block. Use only what the text states.
Return JSON with these keys (null when absent): first_name, last_name, job_title, company, city, region, country.
Do not guess. Do not return any other text.

Sender address: {email}
Signature:
{sig}"""


def ai_fields(lines: list, email: str, client=None, model: str | None = None) -> dict:
    """Claude structures name / title / company / location from the signature
    window. Returns {} on any failure — extraction degrades, never blocks."""
    if not lines:
        return {}
    try:
        if client is None:
            import anthropic
            from flask import current_app
            client = anthropic.Anthropic(api_key=current_app.config['CLAUDE_API_KEY'])
        if model is None:
            from utils.model_router import MODELS, ModelTier
            model = MODELS[ModelTier.HAIKU]
        resp = client.messages.create(model=model, max_tokens=300, temperature=0,
                                      messages=[{'role': 'user', 'content': _PROMPT.format(
                                          email=email, sig='\n'.join(lines)[:2000])}])
        text = ''.join(getattr(b, 'text', '') for b in resp.content).strip()
        m = re.search(r'\{.*\}', text, re.S)
        data = json.loads(m.group(0)) if m else {}
        return {k: (str(v).strip()[:200] if v not in (None, '', 'null') else None)
                for k, v in data.items() if k in ('first_name', 'last_name', 'job_title', 'company',
                                                  'city', 'region', 'country')}
    except Exception as exc:
        logger.warning('signature extraction failed for %s: %s', email, exc)
        return {}


# ---------------------------------------------------------------------------
# Company and inference
# ---------------------------------------------------------------------------

def company_from_domain(domain: str, freemail: list) -> str | None:
    d = (domain or '').lower()
    if not d or d in freemail:
        return None
    parts = d.split('.')
    core = parts[-3] if len(parts) >= 3 and parts[-2] in ('co', 'com', 'org', 'net', 'ac', 'gov') else (
        parts[-2] if len(parts) >= 2 else parts[0])
    return core.replace('-', ' ').title() if core else None


_DEPARTMENTS = [
    (r'engineer|developer|software|devops|sre|cto|architect', 'Engineering'),
    (r'data|analytics|scientist|machine learning|\bml\b|\bai\b', 'Data & Analytics'),
    (r'product', 'Product'),
    (r'design|ux|ui\b|creative', 'Design'),
    (r'market|growth|brand|seo|content|cmo', 'Marketing'),
    (r'sales|account exec|business development|\bbd\b|revenue|cro', 'Sales'),
    (r'finance|cfo|controller|accounting|treasur', 'Finance'),
    (r'recruit|talent|people|human resources|\bhr\b|chro', 'People / HR'),
    (r'legal|counsel|attorney|lawyer', 'Legal'),
    (r'operations|\bops\b|coo|supply chain|logistics', 'Operations'),
    (r'program manag|project manag|\btpm\b|\bpmo\b|delivery', 'Program Management'),
    (r'customer success|support|service', 'Customer Success'),
    (r'ceo|founder|president|managing director|general manager', 'Executive'),
]


def infer_department(title: str) -> str | None:
    t = (title or '').lower()
    for pattern, dept in _DEPARTMENTS:
        if re.search(pattern, t):
            return dept
    return None


def infer_expertise(title: str) -> str | None:
    """A skill area from the job title, stripped of seniority words."""
    t = re.sub(r'\b(senior|sr\.?|junior|jr\.?|lead|principal|staff|head of|chief|vp|vice president|'
               r'director|manager|associate|assistant|of|global|regional|the)\b', ' ', title or '', flags=re.I)
    t = re.sub(r'\s+', ' ', re.sub(r'[,/&|]+', ' ', t)).strip(' -')
    return t[:200] if len(t) >= 3 else None


def infer_role(title: str, contact_domain: str, user_domains: set, freemail: list) -> str | None:
    """client | prospect | partner | recruiter | vendor | colleague | investor — or None.
    Always stored as Inferred until the user confirms."""
    t = (title or '').lower()
    d = (contact_domain or '').lower()
    if d and d in user_domains and d not in freemail:
        return 'colleague'
    if re.search(r'recruit|talent acquisition|sourcer|headhunt', t):
        return 'recruiter'
    if re.search(r'investor|venture|\bvc\b|general partner|limited partner|angel', t):
        return 'investor'
    return None
