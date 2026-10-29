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
