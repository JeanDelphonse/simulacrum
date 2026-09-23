"""PRD-SIM-OBS-001 — agent telemetry capture, rollups and retention.

This module is the single emit hook (PRD §7). It opens one `agent_runs` header
per agent invocation and collects the run's behaviour trace, tool calls and
artifact summaries; the console, calibration and the World view all read what it
writes. No other module writes those tables.

Three properties it must hold, in order of importance:

  1. Capture fails open. Every public function swallows its own errors. A
     telemetry failure must never cost a user the artifact they paid for, so
     nothing in here is allowed to raise into the agent pipeline.
  2. Capture never joins the caller's transaction. Writes go out on their own
     connection via engine.begin(), so a rolled-back agent run still leaves its
     telemetry, and a telemetry failure cannot poison the caller's session —
     which matters because the agent pipeline's session is routinely recycled
     around the long Claude call.
  3. Capture does not slow the run. Detail rows buffer in memory for the life of
     the run and flush in one transaction at the end. Only the header is written
     inline, because the live feed and the rate-limit gate need to see a run the
     moment it starts.

Every threshold — sampling, retention, redaction, token prices — is in
platform_settings under an obs_ prefix. See SETTINGS_SPEC.
"""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import delete, func, insert, update

from app.extensions import db
from app.models.observability import (
    AdminAccessAudit, AgentArtifact, AgentEvent, AgentRun, AgentRunTool,
    LAYER_CODES, LAYER_NAMES, ObservabilityFlag,
    STATUS_BLOCKED, STATUS_ERROR, STATUS_RUNNING, STATUS_SUCCESS,
)
from app.models.platform_settings import PlatformSetting
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin-configurable settings (PRD §7, §12 — "all thresholds admin-configurable")
# ---------------------------------------------------------------------------

# key -> (type, default, label, help). The admin Settings panel is generated
# from this, so adding a knob here is the only edit needed to expose it.
SETTINGS_SPEC = {
    'obs_capture_enabled': (
        'bool', True, 'Capture enabled',
        'Master switch for telemetry. Off means no run is recorded at all — '
        'guardrails stop seeing anything, so leave it on unless you are debugging.'),
    'obs_detail_sampling_pct': (
        'int', 100, 'Detail sampling (%)',
        'Share of runs that also capture the behaviour trace and tool I/O. The '
        'header is always written. A flagged run is always captured in full '
        'regardless of this setting.'),
    'obs_redact_pii': (
        'bool', True, 'Redact PII at capture',
        'Mask emails, phone numbers and long free text in captured parameters and '
        'tool summaries. Least-data capture is the default posture.'),
    'obs_retention_detail_days': (
        'int', 30, 'Run detail retention (days)',
        'How long tool I/O, behaviour traces and artifact summaries are kept. The '
        'run header is kept for the product lifecycle and is not affected.'),
    'obs_retention_flag_days': (
        'int', 396, 'Flag retention (days)',
        'How long guardrail flag and fire records are kept. 396 is 13 months.'),
    'obs_retention_audit_days': (
        'int', 730, 'Access-audit retention (days)',
        'How long the admin access-audit log is kept. 730 is 24 months.'),
    'obs_rollup_cache_seconds': (
        'int', 300, 'Rollup cache (seconds)',
        'Telemetry is write-heavy and the dashboard is read-heavy, so by-layer and '
        'per-agent rollups are cached for this long.'),
    'obs_guardrails_enabled': (
        'bool', True, 'Guardrails enabled',
        'Off means rules are never evaluated. Nothing is flagged and nothing is blocked.'),
    'obs_enforcement_enabled': (
        'bool', True, 'Enforcement enabled',
        'Off downgrades every block rule to warn, platform-wide. Use it to stage a '
        'new blocking rule without withholding anyone output.'),
    'obs_min_observations_to_block': (
        'int', 30, 'Observations before block',
        'The overfitting guard. The console warns before a rule is promoted to '
        'block on fewer evaluations than this — do not harden a rule around a '
        'handful of outlier runs.'),
    'obs_default_window_hours': (
        'int', 24, 'Default time window (hours)',
        'What the console shows when no time filter is chosen.'),
    'obs_runs_page_size': (
        'int', 50, 'Runs per page',
        'Rows in the run list per request.'),
    'obs_token_prices': (
        'str', '{"haiku":[1,5],"sonnet":[3,15],"opus":[15,75],"fable":[3,15]}',
        'Token prices (USD per million)',
        'JSON of tier -> [input, output] USD per million tokens, used only to turn '
        'captured token counts into the spend figure on the layer cards. Update it '
        'when list prices change; no price is hardcoded in code.'),
}

_TRUE = ('1', 'true', 'yes', 'on', 'True')


def setting(key):
    """Read one observability setting, coerced to its declared type."""
    kind, default = SETTINGS_SPEC[key][0], SETTINGS_SPEC[key][1]
    try:
        raw = PlatformSetting.get(key)
    except Exception:
        return default
    if raw is None or raw == '':
        return default
    if kind == 'bool':
        return str(raw) in _TRUE
    if kind == 'int':
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return str(raw)


def all_settings() -> list:
    """Every setting with its value and metadata, for the admin panel."""
    return [
        {
            'key': key, 'type': spec[0], 'value': setting(key),
            'default': spec[1], 'label': spec[2], 'help': spec[3],
        }
        for key, spec in SETTINGS_SPEC.items()
    ]


def save_setting(key, value, updated_by=None):
    """Validate and persist one setting. Returns the coerced value."""
    if key not in SETTINGS_SPEC:
        raise ValueError('Unknown setting: %s' % key)
    kind = SETTINGS_SPEC[key][0]
    label = SETTINGS_SPEC[key][2]

    if kind == 'bool':
        coerced = '1' if (value is True or str(value) in _TRUE) else '0'
    elif kind == 'int':
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError('%s must be a whole number.' % label)
        if number < 0:
            raise ValueError('%s cannot be negative.' % label)
        if key == 'obs_detail_sampling_pct' and number > 100:
            raise ValueError('Detail sampling is a percentage — 0 to 100.')
        if key == 'obs_retention_detail_days' and number < 1:
            raise ValueError('Run detail must be kept for at least one day.')
        if key == 'obs_runs_page_size' and not 1 <= number <= 500:
            raise ValueError('Runs per page must be between 1 and 500.')
        coerced = str(number)
    else:
        coerced = str(value or '').strip()
        if key == 'obs_token_prices':
            try:
                parsed = json.loads(coerced)
                assert isinstance(parsed, dict)
                for tier, pair in parsed.items():
                    assert isinstance(pair, (list, tuple)) and len(pair) == 2
                    float(pair[0]), float(pair[1])
            except Exception:
                raise ValueError(
                    'Token prices must be JSON like {"sonnet": [3, 15]} — '
                    'tier to [input, output] USD per million tokens.')

    PlatformSetting.set(key, coerced, updated_by=updated_by)
    invalidate()
    return setting(key)


# ---------------------------------------------------------------------------
# PII masking (PRD §12 — "raw PII is masked at capture")
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
# Bounded on both sides by a non-word, non-hyphen character so a model id or a
# build number — claude-haiku-4-5-20251001 — is not read as a phone number.
_PHONE_RE = re.compile(r'(?<![\w-])\+?\d[\d\s().-]{7,}\d(?![\w-])')
_SSN_RE = re.compile(r'(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)')
# Keys whose value is PII whatever it looks like, so pattern matching alone
# would not catch them (a street address, a date of birth, a full name).
_PII_KEYS = (
    'email', 'phone', 'mobile', 'address', 'street', 'postcode', 'zip',
    'ssn', 'dob', 'birth', 'passport', 'account_number', 'card',
)
_MAX_VALUE_CHARS = 200


def _mask_text(text: str) -> str:
    text = _EMAIL_RE.sub('[email]', text)
    text = _SSN_RE.sub('[ssn]', text)
    return _PHONE_RE.sub('[phone]', text)


def mask_value(value, key: str = '', redact: bool = True):
    """One captured value, reduced to the least data that is still useful.

    redact=False is the admin switching PII masking off; the length bound still
    applies, because the header is not a place to keep a resume.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return {k: mask_value(v, k, redact) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple)):
        return [mask_value(v, key, redact) for v in value[:20]]

    text = str(value)
    if redact:
        if any(token in (key or '').lower() for token in _PII_KEYS):
            return '[redacted]'
        text = _mask_text(text)
    if len(text) > _MAX_VALUE_CHARS:
        text = text[:_MAX_VALUE_CHARS] + '…'
    return text


def mask_params(params) -> dict:
    """The parameter dict as it is safe to persist on the shared header."""
    if not isinstance(params, dict):
        return {}
    redact = setting('obs_redact_pii')
    return {k: mask_value(v, k, redact) for k, v in list(params.items())[:40]}


def summarize(value, limit: int = 300) -> str:
    """A tool input/output summary: masked, collapsed and length-bounded."""
    if value is None:
        return ''
    redact = setting('obs_redact_pii')
    if isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(mask_value(value, '', redact), default=str)
        except Exception:
            text = str(value)
    else:
        text = str(value)
        if redact:
            text = _mask_text(text)
    text = ' '.join(text.split())
    return text[:limit] + '…' if len(text) > limit else text


# ---------------------------------------------------------------------------
# Low-level writes — always on their own connection, never the caller's session
# ---------------------------------------------------------------------------

def _execute(statements) -> bool:
    try:
        with db.engine.begin() as conn:
            for stmt in statements:
                conn.execute(stmt)
        return True
    except Exception as exc:
        logger.debug('observability write skipped: %s', exc)
        return False


def _scalar(stmt, default=None):
    try:
        with db.engine.connect() as conn:
            value = conn.execute(stmt).scalar()
        return default if value is None else value
    except Exception as exc:
        logger.debug('observability read skipped: %s', exc)
        return default


# The guardrail engine runs on the same pipeline thread and needs the same
# transaction isolation, so it shares these two rather than reimplementing them.
write_out = _execute
read_scalar = _scalar


# ---------------------------------------------------------------------------
# The run recorder
# ---------------------------------------------------------------------------

_local = threading.local()


class _Run(object):
    """The in-flight run. Detail buffers here and flushes once, at the end."""

    __slots__ = ('id', 'agent_key', 'layer', 'user_id', 'simulation_id',
                 'action_id', 'model', 'sampled', 'started', 'started_at',
                 'events', 'tools', 'artifacts', 'params',
                 'prompt_tokens', 'completion_tokens')

    def __init__(self, **kw):
        for slot in self.__slots__:
            setattr(self, slot, kw.get(slot))
        self.events = []
        self.tools = []
        self.artifacts = []

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


def current() -> object:
    """The run being recorded on this thread, or None."""
    return getattr(_local, 'run', None)


def layer_code(layer_number) -> str:
    """Normalise a layer to its L-code. Anything unrecognised reads as L6."""
    if isinstance(layer_number, str):
        code = layer_number.upper()
        return code if code in LAYER_CODES else 'L6'
    try:
        code = 'L%d' % int(layer_number)
    except (TypeError, ValueError):
        return 'L6'
    return code if code in LAYER_CODES else 'L6'


def start_run(agent_key, layer, user_id=None, simulation_id=None, action_id=None,
              params=None, model=None, dispatch_source=None):
    """Open the shared header. Returns the run, or None if capture is off."""
    # Clear first. A worker thread is reused across tasks, so a run left behind
    # by a previous task must not collect this one's trace if the open below
    # fails or capture is off.
    _local.run = None
    try:
        if not setting('obs_capture_enabled'):
            return None
        code = layer_code(layer)
        sampled = random.randint(1, 100) <= max(0, min(100, setting('obs_detail_sampling_pct')))
        run = _Run(
            id=generate_id(), agent_key=agent_key, layer=code, user_id=user_id,
            simulation_id=simulation_id, action_id=action_id, model=model,
            sampled=sampled, started=time.monotonic(), started_at=datetime.utcnow(),
            params=mask_params(params),
        )
        _execute([insert(AgentRun).values(
            id=run.id, user_id=user_id, simulation_id=simulation_id,
            action_id=action_id, agent_key=agent_key, layer=code,
            status=STATUS_RUNNING, model=model, params_json=run.params,
            dispatch_source=dispatch_source, is_sampled=sampled,
            flag_count=0, started_at=run.started_at,
        )])
        _local.run = run
        return run
    except Exception as exc:
        logger.debug('start_run skipped for %s: %s', agent_key, exc)
        return None


def record_event(event_type, message, **detail):
    """Append one step of the behaviour trace.

    Buffered whatever the sampling rate says, because whether the run turns out
    to be worth keeping in full is not known until it ends — a flagged or
    blocked run is always kept. The sampling decision is applied at the flush.
    """
    run = current()
    if run is None:
        return
    try:
        payload = {'message': summarize(message, 400)}
        payload.update({k: mask_value(v, k) for k, v in detail.items()})
        run.events.append((event_type, payload, datetime.utcnow()))
    except Exception:
        pass


def record_decision(message, **detail):
    record_event(AgentEvent.TYPE_DECISION, message, **detail)


def record_warning(message, **detail):
    record_event(AgentEvent.TYPE_WARNING, message, **detail)


def record_tool(tool_name, input_summary=None, output_summary=None,
                duration_ms=None, status=STATUS_SUCCESS):
    """Append one tool call to the trace. Buffered; see record_event."""
    run = current()
    if run is None:
        return
    try:
        run.tools.append({
            'tool_name': tool_name[:60],
            'input_summary': summarize(input_summary),
            'output_summary': summarize(output_summary),
            'duration_ms': duration_ms,
            'status': status,
        })
    except Exception:
        pass


@contextmanager
def tool_span(tool_name, input_summary=None, side_effecting: bool = False):
    """Time one tool call and record it, whatever happens inside.

    `side_effecting=True` moves the guardrail check to the tool-call boundary
    (PRD §9, carve-out two): an irreversible action — a real outbound send or an
    external write — has to be stopped before it commits, not after. The check
    raises ToolBlocked, which the caller is expected to treat as a failed send.
    """
    started = time.monotonic()
    span = {'output': None, 'status': STATUS_SUCCESS}

    if side_effecting:
        from app.services import guardrail_service
        guardrail_service.check_tool_call(tool_name, input_summary, current())

    try:
        yield span
    except Exception:
        span['status'] = STATUS_ERROR
        record_tool(tool_name, input_summary, span.get('output'),
                    int((time.monotonic() - started) * 1000), STATUS_ERROR)
        raise
    record_tool(tool_name, input_summary, span.get('output'),
                int((time.monotonic() - started) * 1000), span.get('status'))


def record_artifact(artifact_type, content=None, summary=None, content_ref=None):
    """Register a deliverable: its type, a structured summary, a reference.

    The content itself is not stored — it stays in the product's artifact store
    and is fetched on demand, audited (PRD §12). `content` is read only for its
    size and, if a guardrail later quarantines this artifact, to hold the single
    copy of an output that never reached the product store.
    """
    run = current()
    if run is None:
        return None
    try:
        record = {
            'id': generate_id(),
            'artifact_type': (artifact_type or 'Artifact')[:80],
            'summary_json': mask_value(summary or {}),
            'content_ref': content_ref,
            'size': len(content) if isinstance(content, str) else None,
            '_content': content if isinstance(content, str) else None,
        }
        run.artifacts.append(record)
        return record['id']
    except Exception:
        return None


def record_usage(model=None, prompt_tokens=None, completion_tokens=None):
    """Report the execution facts from inside the run, so the caller closing it
    does not have to thread the Claude response back out. Accumulates, because
    a two-pass agent bills more than once."""
    run = current()
    if run is None:
        return
    if model:
        run.model = model
    if prompt_tokens:
        run.prompt_tokens = (run.prompt_tokens or 0) + int(prompt_tokens)
    if completion_tokens:
        run.completion_tokens = (run.completion_tokens or 0) + int(completion_tokens)


def set_projected_value(value):
    """Stash the run's headline number on the header for calibration/the World."""
    run = current()
    if run is None:
        return
    try:
        run.params = run.params or {}
        run.params['_projected_value'] = float(value)
    except (TypeError, ValueError):
        pass


def finish_run(status=STATUS_SUCCESS, model=None, prompt_tokens=None,
               completion_tokens=None, error_code=None, force_detail=False):
    """Close the header and flush the buffered detail in one transaction."""
    run = current()
    if run is None:
        return
    _local.run = None
    try:
        flagged = any(t.get('status') == STATUS_BLOCKED for t in run.tools)
        projected = None
        if isinstance(run.params, dict) and '_projected_value' in run.params:
            projected = run.params.pop('_projected_value')

        statements = [update(AgentRun).where(AgentRun.id == run.id).values(
            status=status,
            model=model or run.model,
            prompt_tokens=prompt_tokens if prompt_tokens is not None else run.prompt_tokens,
            completion_tokens=(completion_tokens if completion_tokens is not None
                               else run.completion_tokens),
            latency_ms=run.elapsed_ms(),
            error_code=(str(error_code)[:80] if error_code else None),
            params_json=run.params,
            projected_value=projected,
            finished_at=datetime.utcnow(),
        )]

        # Here is where sampling is applied. A flagged or blocked run is kept in
        # full whatever the rate says — the sample we most need is the one that
        # misbehaved. Everything else was buffered in memory and is dropped.
        if run.sampled or force_detail or status == STATUS_BLOCKED or flagged:
            for index, tool in enumerate(run.tools):
                statements.append(insert(AgentRunTool).values(
                    id=generate_id(), run_id=run.id, sequence_index=index, **tool))
            for event_type, payload, ts in run.events:
                statements.append(insert(AgentEvent).values(
                    id=generate_id(), run_id=run.id, type=event_type,
                    payload_json=payload, ts=ts))

        for artifact in run.artifacts:
            held = bool(artifact.get('_quarantined'))
            statements.append(insert(AgentArtifact).values(
                id=artifact['id'], run_id=run.id, user_id=run.user_id,
                agent_key=run.agent_key, layer=run.layer,
                artifact_type=artifact['artifact_type'],
                summary_json=artifact['summary_json'],
                content_ref=artifact['content_ref'], size=artifact['size'],
                is_quarantined=held,
                # Only a withheld output is stored here. A delivered one lives
                # in the product store and is reached by content_ref (PRD §12).
                withheld_content=(artifact.get('_content') if held else None),
                created_at=datetime.utcnow(),
            ))

        _execute(statements)
        invalidate()
    except Exception as exc:
        logger.debug('finish_run skipped for %s: %s', run.agent_key, exc)


@contextmanager
def run_context(agent_key, layer, **kwargs):
    """Wrap an agent invocation. Records an error status if the run raises."""
    run = start_run(agent_key, layer, **kwargs)
    try:
        yield run
    except Exception as exc:
        if current() is not None:
            finish_run(status=STATUS_ERROR, error_code=exc.__class__.__name__)
        raise
    if current() is not None:
        finish_run(status=STATUS_SUCCESS)


def quarantine_artifacts(content, reason=''):
    """Withhold this run's outputs. Called by the guardrail engine.

    The artifact rows have not been written yet — they flush at finish_run — so
    this marks the buffered records and hands them the content, which is the
    only copy that will exist once delivery is withheld.
    """
    run = current()
    if run is None:
        return
    for artifact in run.artifacts:
        artifact['_quarantined'] = True
        artifact['_content'] = content
        artifact['summary_json'] = dict(artifact.get('summary_json') or {},
                                        withheld_reason=summarize(reason, 300))


def mark_flagged(run_id, count=1):
    """Bump the denormalized flag counter the runs list reads."""
    _execute([update(AgentRun).where(AgentRun.id == run_id).values(
        flag_count=AgentRun.flag_count + count)])
    invalidate()


# ---------------------------------------------------------------------------
# Access audit (PRD §12 — the admins are themselves observed)
# ---------------------------------------------------------------------------

def audit(admin_user_id, action, target_id=None, subject_user_id=None):
    """Record one cross-user read. Never silently skipped on the read path:
    the caller commits it with the response, so a failure here is a 500 rather
    than an unaudited access."""
    db.session.add(AdminAccessAudit(
        id=generate_id(), admin_user_id=admin_user_id, action=action,
        target_id=(str(target_id)[:120] if target_id else None),
        subject_user_id=subject_user_id,
    ))
    db.session.commit()


# ---------------------------------------------------------------------------
# Rollups — materialized and cached (PRD §10, Caching)
# ---------------------------------------------------------------------------

_cache = {}


def invalidate():
    _cache.clear()


def _cached(key, producer):
    ttl = setting('obs_rollup_cache_seconds')
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[1] < ttl:
        return hit[0]
    value = producer()
    _cache[key] = (value, now)
    return value


def _token_prices() -> dict:
    try:
        return json.loads(setting('obs_token_prices'))
    except Exception:
        return json.loads(SETTINGS_SPEC['obs_token_prices'][1])


def _tier_of(model_name: str) -> str:
    name = (model_name or '').lower()
    for tier in ('haiku', 'sonnet', 'opus', 'fable'):
        if tier in name:
            return tier
    return 'sonnet'


def estimate_spend(rows) -> float:
    """USD for (model, prompt_tokens, completion_tokens) triples."""
    prices = _token_prices()
    total = 0.0
    for model_name, prompt_tokens, completion_tokens in rows:
        pair = prices.get(_tier_of(model_name)) or [0, 0]
        total += (prompt_tokens or 0) / 1e6 * float(pair[0])
        total += (completion_tokens or 0) / 1e6 * float(pair[1])
    return round(total, 2)


def window_start(hours=None) -> datetime:
    hours = hours or setting('obs_default_window_hours')
    return datetime.utcnow() - timedelta(hours=int(hours))


def _agent_meta(agent_key: str) -> tuple:
    """(label, layer code) from the agent registry, falling back to the key."""
    try:
        from app.services.agent_registry import get_agent_meta
        meta = get_agent_meta(agent_key)
        if meta:
            return meta['label'], layer_code(meta.get('layer'))
    except Exception:
        pass
    return agent_key, None


def _rollup_rows(group_column, since, layer=None):
    """volume / errors / blocked / flagged / tokens / spend, grouped one way."""
    query = db.session.query(
        group_column,
        func.count(AgentRun.id),
        func.sum(db.case((AgentRun.status == STATUS_ERROR, 1), else_=0)),
        func.sum(db.case((AgentRun.status == STATUS_BLOCKED, 1), else_=0)),
        func.sum(db.case((AgentRun.flag_count > 0, 1), else_=0)),
        func.coalesce(func.sum(AgentRun.prompt_tokens), 0),
        func.coalesce(func.sum(AgentRun.completion_tokens), 0),
    ).filter(AgentRun.started_at >= since)
    if layer:
        query = query.filter(AgentRun.layer == layer)
    return query.group_by(group_column).all()


def _spend_by_group(group_column, since, layer=None) -> dict:
    """Spend needs the model split, which the single grouped query loses."""
    query = db.session.query(
        group_column, AgentRun.model,
        func.coalesce(func.sum(AgentRun.prompt_tokens), 0),
        func.coalesce(func.sum(AgentRun.completion_tokens), 0),
    ).filter(AgentRun.started_at >= since)
    if layer:
        query = query.filter(AgentRun.layer == layer)
    buckets = {}
    for key, model_name, prompt_tokens, completion_tokens in query.group_by(
            group_column, AgentRun.model).all():
        buckets.setdefault(key, []).append((model_name, prompt_tokens, completion_tokens))
    return {key: estimate_spend(rows) for key, rows in buckets.items()}


def _top_values(column, since, layer=None, limit=3, model=AgentRunTool):
    query = db.session.query(column, func.count()).filter(model.created_at >= since)
    if layer is not None and hasattr(model, 'layer'):
        query = query.filter(model.layer == layer)
    elif layer is not None:
        query = (query.join(AgentRun, AgentRun.id == model.run_id)
                      .filter(AgentRun.layer == layer))
    rows = query.group_by(column).order_by(func.count().desc()).limit(limit).all()
    return [value for value, _ in rows]


def layer_rollups(hours=None) -> list:
    """The six overview cards. Cached — this is the console's landing query."""
    hours = int(hours or setting('obs_default_window_hours'))

    def produce():
        since = window_start(hours)
        stats = {row[0]: row for row in _rollup_rows(AgentRun.layer, since)}
        spend = _spend_by_group(AgentRun.layer, since)
        cards = []
        for code in LAYER_CODES:
            row = stats.get(code)
            runs = row[1] if row else 0
            errors = int(row[2] or 0) if row else 0
            blocked = int(row[3] or 0) if row else 0
            flagged = int(row[4] or 0) if row else 0
            cards.append({
                'layer': code,
                'layer_name': LAYER_NAMES[code],
                'runs': runs,
                'errors': errors,
                'blocked': blocked,
                'flagged': flagged,
                'error_rate': round(100.0 * errors / runs, 1) if runs else 0.0,
                'flag_rate': round(100.0 * flagged / runs, 1) if runs else 0.0,
                'tokens': int((row[5] or 0) + (row[6] or 0)) if row else 0,
                'spend': spend.get(code, 0.0),
                'top_tools': _top_values(AgentRunTool.tool_name, since, code),
                'top_artifacts': _top_values(AgentArtifact.artifact_type, since, code,
                                             model=AgentArtifact),
            })
        return cards

    return _cached('layers:%d' % hours, produce)


def agent_rollups(layer=None, hours=None) -> list:
    """Per-agent rollups, optionally within one layer."""
    hours = int(hours or setting('obs_default_window_hours'))

    def produce():
        since = window_start(hours)
        spend = _spend_by_group(AgentRun.agent_key, since, layer)
        out = []
        for row in _rollup_rows(AgentRun.agent_key, since, layer):
            key, runs = row[0], row[1]
            errors, blocked, flagged = int(row[2] or 0), int(row[3] or 0), int(row[4] or 0)
            label, agent_layer = _agent_meta(key)
            out.append({
                'agent_key': key,
                'agent_label': label,
                # The list can be unfiltered, so each row names its own layer
                # rather than relying on the filter for context.
                'layer': layer or agent_layer,
                'runs': runs,
                'errors': errors,
                'blocked': blocked,
                'flagged': flagged,
                'error_rate': round(100.0 * errors / runs, 1) if runs else 0.0,
                'flag_rate': round(100.0 * flagged / runs, 1) if runs else 0.0,
                'tokens': int((row[5] or 0) + (row[6] or 0)),
                'spend': spend.get(key, 0.0),
            })
        out.sort(key=lambda a: a['runs'], reverse=True)
        return out

    return _cached('agents:%s:%d' % (layer or 'all', hours), produce)


# ---------------------------------------------------------------------------
# Retention (PRD §12) — the off-peak purge
# ---------------------------------------------------------------------------

def purge_expired() -> dict:
    """Drop everything past its own retention window. Returns what it removed.

    Three independent clocks: run detail is short (it is the sensitive part),
    flags are kept 13 months so a rule's effect can be read across a year, and
    the access audit is kept 24 months because it is the compliance record.
    The run header is never purged here — it is on the product lifecycle and is
    shared with calibration and the World.
    """
    now = datetime.utcnow()
    detail_before = now - timedelta(days=setting('obs_retention_detail_days'))
    flag_before = now - timedelta(days=setting('obs_retention_flag_days'))
    audit_before = now - timedelta(days=setting('obs_retention_audit_days'))

    removed = {}
    for label, model, column, cutoff in (
        ('events', AgentEvent, AgentEvent.ts, detail_before),
        ('tools', AgentRunTool, AgentRunTool.created_at, detail_before),
        ('artifacts', AgentArtifact, AgentArtifact.created_at, detail_before),
        ('flags', ObservabilityFlag, ObservabilityFlag.created_at, flag_before),
        ('access_audit', AdminAccessAudit, AdminAccessAudit.ts, audit_before),
    ):
        try:
            count = db.session.query(func.count()).select_from(model).filter(
                column < cutoff).scalar() or 0
            if count:
                db.session.execute(delete(model).where(column < cutoff))
                db.session.commit()
            removed[label] = int(count)
        except Exception as exc:
            db.session.rollback()
            logger.warning('observability purge of %s failed: %s', label, exc)
            removed[label] = 0

    invalidate()
    return removed
