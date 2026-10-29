"""Safe artifact actions (EXEC_PLAN P1) — no side effects outside SimiSI.

  refine()        AI rewrite of a markdown artifact; returns a proposal only.
                  Accepting it saves a new 'agent_refine' version.
  render_html()   artifact → sanitized HTML (markdown, tables, campaign JSON)
  export_pdf()    artifact → PDF bytes (PyMuPDF Story; no system libraries)
  duplicate()     new artifact copied from the current version
  agent_update()  the parked agent re-run version, if one is waiting

None of these call an integration, email anyone or post anything.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# Campaign artifacts hold per-prospect send state; copying them would produce a
# second campaign aimed at people the first one already claimed.
NO_DUPLICATE_AGENTS = frozenset({'cold_email_campaign', 'consulting_outreach'})
REFINE_MAX_CHARS = 60000


class ArtifactActionError(Exception):
    """A user-facing refusal (bad input, unsupported artifact)."""


def is_structured(content: str) -> bool:
    return bool(content) and content.lstrip().startswith('{')


# ---------------------------------------------------------------------------
# Refine
# ---------------------------------------------------------------------------

_REFINE_SYSTEM = (
    'You revise a professional artifact for its owner. Apply the instruction and keep '
    'everything else as it is: structure, facts, numbers, names and tone unless the '
    'instruction says otherwise. Never invent facts, people, prices or statistics. '
    'Return the complete revised artifact in Markdown, with no preamble or commentary.'
)


def refine(content: str, instruction: str, user_id: str = None, simulation_id: str = None,
           client=None) -> str:
    """Proposed revision of ``content`` per ``instruction``. Nothing is saved."""
    instruction = (instruction or '').strip()
    if not instruction:
        raise ArtifactActionError('Describe how the artifact should change.')
    if len(instruction) > 2000:
        raise ArtifactActionError('Keep the instruction under 2,000 characters.')
    if not content or not content.strip():
        raise ArtifactActionError('This artifact is empty.')
    if is_structured(content):
        raise ArtifactActionError('Campaign artifacts are edited email by email, not refined as a whole.')
    if len(content) > REFINE_MAX_CHARS:
        raise ArtifactActionError('This artifact is too long to refine in one pass.')

    from app.models.ai_interaction import AIInteraction
    from app.services import claude
    from utils.model_router import get_model

    model = get_model('artifact_refine')
    client = client or claude._client()
    response = client.messages.create(
        model=model, max_tokens=16384, system=_REFINE_SYSTEM,
        messages=[{'role': 'user', 'content': (
            'INSTRUCTION:\n%s\n\nARTIFACT:\n%s' % (instruction, content))}],
    )
    claude._log_interaction(AIInteraction.TYPE_AGENT_ACTION, user_id, simulation_id,
                            response.usage, model=model)
    text = (response.content[0].text if response.content else '').strip()
    if not text:
        raise ArtifactActionError('The refine step returned nothing — try again.')
    if response.stop_reason == 'max_tokens':
        raise ArtifactActionError('The revision was too long and got cut off — try a narrower instruction.')
    return text


# ---------------------------------------------------------------------------
# HTML / PDF
# ---------------------------------------------------------------------------

_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')


def _cells(line: str) -> list:
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|'):
        line = line[:-1]
    return [c.strip() for c in line.split('|')]


def _markdown_html(text: str) -> str:
    """Markdown → HTML via the blog's escape-first renderer, plus pipe tables
    (which it deliberately omits). Cell text is escaped the same way."""
    from app.services import blog_markdown
    lines = (text or '').replace('\r\n', '\n').split('\n')
    out, buf, i = [], [], 0
    while i < len(lines):
        if ('|' in lines[i] and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1])):
            if buf:
                out.append(blog_markdown.render('\n'.join(buf)))
                buf = []
            head = _cells(lines[i])
            i += 2
            rows = []
            while i < len(lines) and '|' in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            cell = lambda c: blog_markdown._inline(blog_markdown._escape(c))  # noqa: E731
            out.append('<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>' % (
                ''.join('<th>%s</th>' % cell(c) for c in head),
                ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % cell(c) for c in r) for r in rows)))
            continue
        buf.append(lines[i])
        i += 1
    if buf:
        out.append(blog_markdown.render('\n'.join(buf)))
    return '\n'.join(out)


def _campaign_html(data: dict) -> str:
    esc = _html.escape
    parts = []
    prospects = data.get('prospects') or []
    if not prospects:
        reason = (data.get('research_summary') or {}).get('empty_reason') or data.get('empty_reason')
        return '<p>%s</p>' % esc(reason or 'No prospects in this campaign.')
    for p in prospects:
        name = esc(('%s %s' % (p.get('first_name') or '', p.get('last_name') or '')).strip())
        meta = esc(' · '.join(x for x in (p.get('job_title'), p.get('company_name')) if x))
        parts.append('<h3>%s</h3><p class="meta">%s</p>' % (name, meta))
        steps = p.get('sequence') or ([p['email_draft']] if p.get('email_draft') else [])
        for n, step in enumerate(steps, 1):
            parts.append('<p class="step">Email %d — <b>%s</b></p><p>%s</p>' % (
                n, esc(step.get('subject') or ''),
                esc(step.get('body') or '').replace('\n', '<br>')))
    return '\n'.join(parts)


def render_html(content: str) -> str:
    if is_structured(content):
        try:
            data, _ = json.JSONDecoder().raw_decode(content.strip())
            if isinstance(data, dict):
                return _campaign_html(data)
        except Exception:
            pass
    return _markdown_html(content)


_PDF_CSS = """
* { font-family: sans-serif; }
body { font-size: 10.5pt; line-height: 1.45; color: #1f2937; }
h1 { font-size: 18pt; margin: 0 0 4pt 0; color: #0f172a; }
h2 { font-size: 14pt; margin: 12pt 0 4pt 0; color: #0f172a; }
h3 { font-size: 12pt; margin: 10pt 0 3pt 0; color: #0f172a; }
h4 { font-size: 11pt; margin: 8pt 0 2pt 0; }
p.sub { color: #64748b; font-size: 9pt; margin: 0 0 12pt 0; }
p.meta { color: #64748b; font-size: 9pt; margin: 0 0 4pt 0; }
p.step { margin: 6pt 0 2pt 0; }
table { border-collapse: collapse; margin: 6pt 0; }
th, td { border: 1px solid #cbd5e1; padding: 3pt 5pt; font-size: 9.5pt; vertical-align: top; }
th { background-color: #f1f5f9; }
blockquote { color: #475569; margin-left: 10pt; }
pre, code { font-family: monospace; font-size: 9pt; }
"""


def export_pdf(title: str, content: str, subtitle: str = '') -> bytes:
    """Letter-size PDF of the artifact (PyMuPDF Story — already a dependency,
    needs no system libraries on the host)."""
    import fitz

    body = ('<h1>%s</h1><p class="sub">%s</p>%s' % (
        _html.escape(title or 'Artifact'), _html.escape(subtitle or ''), render_html(content or '')))
    story = fitz.Story(html=body, user_css=_PDF_CSS)
    buf = __import__('io').BytesIO()
    writer = fitz.DocumentWriter(buf)
    page = fitz.paper_rect('letter')
    where = page + (54, 54, -54, -54)
    more = True
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Duplicate / parked agent update
# ---------------------------------------------------------------------------

def duplicate(action, user_id: str):
    """A new artifact (agent_actions row + version 1) copied from ``action``'s
    current version. The original is untouched; no hooks, sends or deploys run."""
    from app.extensions import db
    from app.models.agent_action import AgentAction
    from app.models.artifact import ArtifactVersion
    from app.services.agent_registry import resolve_alias
    from utils.id_gen import generate_id

    if resolve_alias(action.action_type) in NO_DUPLICATE_AGENTS:
        raise ArtifactActionError('Campaigns can\'t be duplicated — re-run the agent for a new campaign.')
    current = ArtifactVersion.current_for(action.id)
    content = current.content if current else action.artifact
    if not content:
        raise ArtifactActionError('This artifact is empty.')

    now = datetime.utcnow()
    copy = AgentAction(id=generate_id(), simulation_id=action.simulation_id,
                       layer_number=action.layer_number, action_type=action.action_type,
                       status=AgentAction.STATUS_COMPLETE, artifact=content,
                       created_by=user_id, completed_at=now)
    copy.user_inputs = action.user_inputs or {}
    db.session.add(copy)
    db.session.flush()
    db.session.add(ArtifactVersion(
        id=generate_id(), action_id=copy.id, simulation_id=action.simulation_id,
        layer_number=action.layer_number, action_type=action.action_type,
        version_number=1, version_label='v1 — %s' % now.strftime('%b %d'),
        content=content, file_type=(current.file_type if current else 'text'),
        edited_by=ArtifactVersion.EDITED_BY_USER,
        edit_summary='Duplicated from %s v%s' % (action.id, current.version_number if current else 1),
        edited_at=now, is_current=True, created_by='user',
    ))
    from app.models.audit_log import AuditLog
    AuditLog.log('artifact_duplicated', user_id=user_id, resource_id=copy.id,
                 metadata={'source_action_id': action.id})
    db.session.commit()
    return copy


def agent_update(action_id: str):
    """The newest agent-generated version that is newer than the current one
    and was parked because the current version holds the user's edits."""
    from app.models.artifact import ArtifactVersion
    current = ArtifactVersion.current_for(action_id)
    if current is None or current.is_agent_original:
        return None
    newer = (ArtifactVersion.query
             .filter(ArtifactVersion.action_id == action_id,
                     ArtifactVersion.version_number > current.version_number,
                     ArtifactVersion.is_current.is_(False))
             .order_by(ArtifactVersion.version_number.desc()).all())
    return next((v for v in newer if v.is_agent_original), None)


# ---------------------------------------------------------------------------
# Verb log (EXEC_PLAN P2)
# ---------------------------------------------------------------------------

def log_action(artifact_id: str, verb: str, actor_id: str = None, status: str = 'ok',
               result: dict = None, unit_id: str = None, commit: bool = True) -> None:
    """Record a verb applied to an artifact. Never raises: logging must not
    cost the user the action they took."""
    try:
        from app.extensions import db
        from app.models.artifact_exec import ArtifactAction
        db.session.add(ArtifactAction(
            artifact_id=artifact_id, unit_id=unit_id, verb=verb, actor_id=actor_id,
            status=status, result_ref=json.dumps(result)[:4000] if result else None))
        if commit:
            db.session.commit()
    except Exception as exc:
        logger.warning('artifact action log failed (%s %s): %s', verb, artifact_id, exc)
        try:
            from app.extensions import db
            db.session.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Send to contacts — DRAFTS ONLY (EXEC_PLAN P2). Nothing here sends.
# ---------------------------------------------------------------------------

DEFAULT_MAX_DRAFT_RECIPIENTS = 50


def max_draft_recipients() -> int:
    """Admin-configurable cap per prepare run (platform setting
    ``exec_draft_max_recipients``)."""
    try:
        from app.models.platform_settings import PlatformSetting
        return max(1, int(PlatformSetting.get('exec_draft_max_recipients',
                                              DEFAULT_MAX_DRAFT_RECIPIENTS)))
    except Exception:
        return DEFAULT_MAX_DRAFT_RECIPIENTS


def send_refusal(action) -> str | None:
    """Why this artifact can't use send-to-contacts, or None if it can."""
    from app.services import agent_execution
    if not agent_execution.supports(action.action_type, 'send_to_contacts'):
        return 'This artifact does not support sending to contacts.'
    from app.models.artifact import ArtifactVersion
    current = ArtifactVersion.current_for(action.id)
    if is_structured(current.content if current else action.artifact):
        return 'Campaign artifacts already have their own recipients and send controls.'
    return None


def contact_candidates(action, user_id: str, query: str = '', limit: int = 200) -> list:
    """The user's contacts for the picker, each marked eligible or with the
    reason it can't receive this artifact. Archived contacts are left out."""
    from app.models.contact import Contact
    from app.models.outreach_email import EmailSuppression
    from app.services import recipient_ledger
    from app.services.contact_lookup import is_placeholder_contact

    q = Contact.query.filter(Contact.user_id == user_id, Contact.is_archived.is_(False))
    query = (query or '').strip()
    if query:
        like = '%' + query.replace('%', '').replace('_', '') + '%'
        q = q.filter((Contact.first_name.ilike(like)) | (Contact.last_name.ilike(like)) |
                     (Contact.email.ilike(like)) | (Contact.company_name.ilike(like)))
    contacts = q.order_by(Contact.first_name.asc()).limit(limit).all()
    emails = [c.email.lower() for c in contacts if c.email]
    suppressed = set()
    for i in range(0, len(emails), 500):
        suppressed |= {r.email.lower() for r in EmailSuppression.query.filter(
            EmailSuppression.email.in_(emails[i:i + 500])).all()}
    used = recipient_ledger.used_keys(action.simulation_id, action.action_type)

    out = []
    for c in contacts:
        reason = None
        if not c.email:
            reason = 'No email address'
        elif c.do_not_contact:
            reason = 'Do not contact'
        elif c.email.lower() in suppressed:
            reason = 'Unsubscribed or bounced'
        elif is_placeholder_contact(c.first_name, c.last_name, c.company_name, c.email):
            reason = 'Placeholder, not a real person'
        elif recipient_ledger.recipient_key(c.email) in used:
            reason = 'Already contacted by this agent'
        out.append({'id': c.id, 'name': ('%s %s' % (c.first_name or '', c.last_name or '')).strip(),
                    'email': c.email, 'company': c.company_name, 'title': c.job_title,
                    'stage': c.pipeline_stage, 'eligible': reason is None, 'reason': reason})
    return out


_MERGE_BRACKETS = {
    'first name': 'first_name', 'firstname': 'first_name', 'name': 'first_name',
    'last name': 'last_name', 'lastname': 'last_name', 'full name': 'full_name',
    'company': 'company', 'company name': 'company', 'organization': 'company',
    'title': 'title', 'job title': 'title', 'email': 'email',
}
_CURLY_RE = re.compile(r'\{\{\s*([a-z_]+)\s*\}\}', re.I)
_BRACKET_RE = re.compile(r'\[([A-Za-z][A-Za-z ]{1,30})\]')


def merge_fields(text: str, contact) -> tuple:
    """Fill {{first_name}} / [First Name]-style fields for one contact.
    Returns (text, unresolved). Unresolved placeholders are shown in the
    preview so the user can fix them before anything could ever be sent."""
    values = {
        'first_name': contact.first_name or '', 'last_name': contact.last_name or '',
        'full_name': ('%s %s' % (contact.first_name or '', contact.last_name or '')).strip(),
        'company': contact.company_name or '', 'title': contact.job_title or '',
        'email': contact.email or '',
    }
    unresolved = []

    def curly(m):
        key = m.group(1).lower()
        if values.get(key):
            return values[key]
        unresolved.append(m.group(0))
        return m.group(0)

    def bracket(m):
        key = _MERGE_BRACKETS.get(m.group(1).strip().lower())
        if key and values.get(key):
            return values[key]
        unresolved.append(m.group(0))
        return m.group(0)

    out = _BRACKET_RE.sub(bracket, _CURLY_RE.sub(curly, text or ''))
    return out, sorted(set(unresolved))


def prepare_send_drafts(action, user_id: str, contact_ids: list, subject: str, body: str) -> dict:
    """Per-recipient DRAFTS with merge fields resolved, stored as artifact
    units. Nothing is sent, queued or scheduled."""
    from app.extensions import db
    from app.models.artifact_exec import ArtifactUnit
    from app.models.contact import Contact
    from utils.id_gen import generate_id

    refusal = send_refusal(action)
    if refusal:
        raise ArtifactActionError(refusal)
    subject = (subject or '').strip()
    body = (body or '').strip()
    if not subject:
        raise ArtifactActionError('Add a subject line.')
    if not body:
        raise ArtifactActionError('The message body is empty.')
    ids = list(dict.fromkeys(i for i in (contact_ids or []) if i))
    if not ids:
        raise ArtifactActionError('Pick at least one contact.')
    cap = max_draft_recipients()
    if len(ids) > cap:
        raise ArtifactActionError('Pick at most %d contacts per batch.' % cap)

    contacts = {c.id: c for c in Contact.query.filter(Contact.id.in_(ids),
                                                       Contact.user_id == user_id).all()}
    eligibility = {c['id']: c for c in contact_candidates(action, user_id, limit=100000)}
    batch_id = generate_id()
    drafts, skipped = [], []
    for n, cid in enumerate(ids):
        info, contact = eligibility.get(cid), contacts.get(cid)
        if contact is None or info is None:
            skipped.append({'id': cid, 'reason': 'Not one of your contacts'})
            continue
        if not info['eligible']:
            skipped.append({'id': cid, 'name': info['name'], 'reason': info['reason']})
            continue
        subj, s_missing = merge_fields(subject, contact)
        text, b_missing = merge_fields(body, contact)
        unit = ArtifactUnit(id=generate_id(), artifact_id=action.id, batch_id=batch_id, seq_index=n,
                            kind=ArtifactUnit.KIND_VARIANT, contact_id=cid, subject=subj[:300],
                            body_ref=text, status=ArtifactUnit.STATUS_DRAFT)
        missing = sorted(set(s_missing + b_missing))
        if missing:
            unit.warnings = ['Unresolved: ' + ', '.join(missing)]
        db.session.add(unit)
        drafts.append((unit, info))
    db.session.flush()
    log_action(action.id, 'prepare_send', actor_id=user_id, commit=False,
               result={'batch_id': batch_id, 'drafts': len(drafts), 'skipped': len(skipped)})
    db.session.commit()
    return {
        'batch_id': batch_id,
        'drafts': [dict(u.to_dict(), to=info['email'], name=info['name']) for u, info in drafts],
        'skipped': skipped,
        'sent': 0,
    }


def list_drafts(action_id: str) -> list:
    from app.models.artifact_exec import ArtifactUnit
    from app.models.contact import Contact
    units = (ArtifactUnit.query.filter_by(artifact_id=action_id, kind=ArtifactUnit.KIND_VARIANT,
                                          status=ArtifactUnit.STATUS_DRAFT)
             .order_by(ArtifactUnit.created_at, ArtifactUnit.seq_index).all())
    people = {c.id: c for c in Contact.query.filter(
        Contact.id.in_([u.contact_id for u in units if u.contact_id])).all()} if units else {}
    out = []
    for u in units:
        c = people.get(u.contact_id)
        out.append(dict(u.to_dict(), to=c.email if c else None,
                        name=('%s %s' % (c.first_name or '', c.last_name or '')).strip() if c else ''))
    return out


def discard_drafts(action_id: str, user_id: str, batch_id: str = None) -> int:
    from app.extensions import db
    from app.models.artifact_exec import ArtifactUnit
    q = ArtifactUnit.query.filter_by(artifact_id=action_id, status=ArtifactUnit.STATUS_DRAFT)
    if batch_id:
        q = q.filter_by(batch_id=batch_id)
    n = q.update({'status': ArtifactUnit.STATUS_DISCARDED}, synchronize_session=False)
    log_action(action_id, 'discard_drafts', actor_id=user_id, commit=False,
               result={'batch_id': batch_id, 'discarded': n})
    db.session.commit()
    return n
