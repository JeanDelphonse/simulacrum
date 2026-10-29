"""Agent Observability & Governance routes — PRD-SIM-OBS-001 §8.

Observe:
  GET  /api/admin/observability/layers            by-layer rollups
  GET  /api/admin/observability/agents?layer=     per-agent rollups
  GET  /api/admin/observability/runs              filtered run list
  GET  /api/admin/observability/runs/<id>         full detail (audited)
  GET  /api/admin/observability/artifacts/<id>    artifact content (audited)
  GET  /api/admin/observability/flags             everything a rule flagged
  GET  /api/admin/observability/stream            SSE live run feed
  GET  /api/admin/observability/export            CSV of the current filter (audited)
  GET  /api/admin/observability/meta              filter vocabularies
  GET  /api/admin/observability/audit             the admin access-audit log

Govern:
  GET/POST        /api/admin/guardrails/rules
  PUT/DELETE      /api/admin/guardrails/rules/<id>
  GET/POST        /api/admin/guardrails/policies
  PUT/DELETE      /api/admin/guardrails/policies/<id>
  POST            /api/admin/observability/artifacts/<id>/release
  GET/PUT         /api/admin/observability/settings
  POST            /api/admin/observability/purge

Every route is Platform Admin only, and every route that reveals one user's run
or artifact writes an admin_access_audit row before it answers (PRD §12).
"""
from __future__ import annotations

import csv
import io
import logging
import time
import traceback
from datetime import datetime
from functools import wraps

from flask import Response, jsonify, request, stream_with_context
from flask_login import current_user, login_required

from app.blueprints.observability import observability_bp
from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.observability import (
    ACTION_BLOCK, AdminAccessAudit, AgentArtifact, AgentEvent, AgentRun,
    AgentRunTool, GuardrailPolicy, GuardrailRule, LAYER_CODES, LAYER_NAMES,
    ObservabilityFlag, RULE_TYPES, STATUS_BLOCKED, STATUS_ERROR, STATUS_SUCCESS,
)
from app.models.user import User
from app.services import guardrail_service as guardrails
from app.services import observability_service as obs
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


# Tables this console owns. If one is absent the deploy step was missed, which
# is a setup problem with an obvious fix, not a server fault.
_OWNED_TABLES = (
    'agent_runs', 'agent_run_tools', 'agent_events', 'agent_artifacts',
    'guardrail_rules', 'guardrail_policies', 'observability_flags',
    'admin_access_audit',
)
_MIGRATION = 'sql/050_observability.sql'


def _missing_table(exc) -> str:
    """The name of one of our tables the database says is absent, or ''.

    MySQL raises 1146 "Table '...' doesn't exist"; SQLite says "no such table".
    Both land here as a DBAPI error, so match on the text of either.
    """
    text = str(getattr(exc, 'orig', exc)).lower()
    if "doesn't exist" not in text and 'no such table' not in text:
        return ''
    for table in _OWNED_TABLES:
        if table in text:
            return table
    return ''


@observability_bp.errorhandler(Exception)
def _observability_failed(exc):
    """Answer a failure in this console with something an admin can act on.

    The app-wide handler turns everything into "Internal server error", which
    for this blueprint hides the one failure mode that is actually common: the
    deploy ran the code without running the migration.
    """
    from werkzeug.exceptions import HTTPException
    if isinstance(exc, HTTPException):
        return exc

    table = _missing_table(exc)
    if table:
        logger.error('Observability console is missing table %s — %s has not '
                     'been run on this database.', table, _MIGRATION)
        return jsonify({
            'error': ('This console is not set up on this database yet: the '
                      'table "%s" is missing. Run %s, then reload.'
                      % (table, _MIGRATION)),
            'setup_required': True,
            'missing_table': table,
            'migration': _MIGRATION,
        }), 503

    logger.error('Observability console error:\n%s', traceback.format_exc())
    return jsonify({'error': 'Internal server error'}), 500


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _hours() -> int:
    try:
        value = int(request.args.get('hours') or obs.setting('obs_default_window_hours'))
    except (TypeError, ValueError):
        value = obs.setting('obs_default_window_hours')
    return max(1, min(value, 24 * 400))


def _user_labels(user_ids) -> dict:
    """id -> a short display handle. One query, never one per row."""
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    rows = User.query.filter(User.id.in_(ids)).all()
    return {u.id: (u.email or '').split('@')[0] or u.full_name or u.id for u in rows}


def _agent_labels(agent_keys) -> dict:
    from app.services.agent_registry import get_agent_meta
    labels = {}
    for key in {k for k in agent_keys if k}:
        meta = get_agent_meta(key)
        labels[key] = meta['label'] if meta else key
    return labels


def _filtered_runs():
    """The run query behind the list, the export and the live feed."""
    query = AgentRun.query.filter(AgentRun.started_at >= obs.window_start(_hours()))

    layer = (request.args.get('layer') or '').strip().upper()
    if layer in LAYER_CODES:
        query = query.filter(AgentRun.layer == layer)

    agent = (request.args.get('agent') or '').strip()
    if agent:
        query = query.filter(AgentRun.agent_key == agent)

    model = (request.args.get('model') or '').strip()
    if model:
        query = query.filter(AgentRun.model == model)

    user_id = (request.args.get('user_id') or '').strip()
    if user_id:
        query = query.filter(AgentRun.user_id == user_id)

    status = (request.args.get('status') or '').strip()
    if status == 'flagged':
        query = query.filter(AgentRun.flag_count > 0)
    elif status in (STATUS_SUCCESS, STATUS_ERROR, STATUS_BLOCKED, 'running'):
        query = query.filter(AgentRun.status == status)

    return query.order_by(AgentRun.started_at.desc())


def _serialize_runs(rows) -> list:
    labels = _user_labels(r.user_id for r in rows)
    agents = _agent_labels(r.agent_key for r in rows)
    return [r.to_dict(user_label=labels.get(r.user_id),
                      agent_label=agents.get(r.agent_key)) for r in rows]


# ---------------------------------------------------------------------------
# Observe
# ---------------------------------------------------------------------------

@observability_bp.route('/api/admin/observability/meta', methods=['GET'])
@login_required
@admin_required
def get_meta():
    """The vocabularies the console's filters and the rule drawer are built from."""
    from app.services.agent_registry import get_all_agents
    agents = [{
        'action_type': a['action_type'],
        'label': a['label'],
        'layer': 'L%d' % a['layer'],
    } for a in get_all_agents()]
    agents.sort(key=lambda a: (a['layer'], a['label']))

    models = [m for (m,) in db.session.query(AgentRun.model)
              .filter(AgentRun.model.isnot(None)).distinct().all()]
    tools = [t for (t,) in db.session.query(AgentRunTool.tool_name).distinct().all()]

    return jsonify({
        'layers': [{'code': c, 'name': LAYER_NAMES[c]} for c in LAYER_CODES],
        'agents': agents,
        'models': sorted(models),
        'tools': sorted(set(tools) | set(guardrails.SIDE_EFFECTING_TOOLS)),
        'rule_types': list(RULE_TYPES),
        'side_effecting_tools': list(guardrails.SIDE_EFFECTING_TOOLS),
        'default_window_hours': obs.setting('obs_default_window_hours'),
        'enforcement_enabled': obs.setting('obs_enforcement_enabled'),
        'guardrails_enabled': obs.setting('obs_guardrails_enabled'),
        'capture_enabled': obs.setting('obs_capture_enabled'),
    }), 200


@observability_bp.route('/api/admin/observability/layers', methods=['GET'])
@login_required
@admin_required
def get_layers():
    hours = _hours()
    cards = obs.layer_rollups(hours)
    open_flags = ObservabilityFlag.query.filter(
        ObservabilityFlag.created_at >= obs.window_start(hours)).count()
    return jsonify({
        'hours': hours,
        'layers': cards,
        'totals': {
            'runs': sum(c['runs'] for c in cards),
            'errors': sum(c['errors'] for c in cards),
            'flagged': sum(c['flagged'] for c in cards),
            'blocked': sum(c['blocked'] for c in cards),
            'spend': round(sum(c['spend'] for c in cards), 2),
            'flags': open_flags,
        },
    }), 200


@observability_bp.route('/api/admin/observability/agents', methods=['GET'])
@login_required
@admin_required
def get_agents():
    layer = (request.args.get('layer') or '').strip().upper()
    layer = layer if layer in LAYER_CODES else None
    return jsonify({'layer': layer, 'agents': obs.agent_rollups(layer, _hours())}), 200


@observability_bp.route('/api/admin/observability/runs', methods=['GET'])
@login_required
@admin_required
def list_runs():
    page_size = obs.setting('obs_runs_page_size')
    try:
        page = max(1, int(request.args.get('page') or 1))
    except (TypeError, ValueError):
        page = 1

    query = _filtered_runs()
    total = query.count()
    rows = query.limit(page_size).offset((page - 1) * page_size).all()
    return jsonify({
        'runs': _serialize_runs(rows),
        'page': page,
        'page_size': page_size,
        'total': total,
        'has_more': page * page_size < total,
    }), 200


@observability_bp.route('/api/admin/observability/runs/<run_id>', methods=['GET'])
@login_required
@admin_required
def get_run(run_id):
    """The full record. Reveals one user's activity, so it is audited first."""
    run = AgentRun.query.get(run_id)
    if not run:
        return jsonify({'error': 'Run not found'}), 404

    obs.audit(current_user.id, AdminAccessAudit.VIEW_RUN, run_id, run.user_id)

    events = (AgentEvent.query.filter_by(run_id=run_id)
              .order_by(AgentEvent.ts.asc()).all())
    tools = (AgentRunTool.query.filter_by(run_id=run_id)
             .order_by(AgentRunTool.sequence_index.asc()).all())
    artifacts = AgentArtifact.query.filter_by(run_id=run_id).all()
    flags = (ObservabilityFlag.query.filter_by(run_id=run_id)
             .order_by(ObservabilityFlag.created_at.asc()).all())

    labels = _user_labels([run.user_id])
    detail = run.to_dict(user_label=labels.get(run.user_id),
                         agent_label=_agent_labels([run.agent_key]).get(run.agent_key))
    detail.update({
        'params': run.params_json or {},
        'trace': [e.to_dict(run.started_at) for e in events],
        'tools': [t.to_dict() for t in tools],
        'artifacts': [a.to_dict() for a in artifacts],
        'flags': [f.to_dict() for f in flags],
        # An unsampled run has a header but no trace; say so rather than
        # rendering three empty panes.
        'detail_captured': bool(run.is_sampled or events or tools),
    })
    return jsonify(detail), 200


@observability_bp.route('/api/admin/observability/artifacts/<artifact_id>', methods=['GET'])
@login_required
@admin_required
def get_artifact(artifact_id):
    """Fetch an artifact's content on demand. Always writes an audit row.

    A delivered artifact is not stored here — it is read back through
    content_ref from the product's own store, so there is exactly one copy.
    A quarantined artifact is the exception: it never reached that store.
    """
    artifact = AgentArtifact.query.get(artifact_id)
    if not artifact:
        return jsonify({'error': 'Artifact not found'}), 404

    obs.audit(current_user.id, AdminAccessAudit.VIEW_ARTIFACT, artifact_id, artifact.user_id)

    payload = artifact.to_dict()
    ref = artifact.content_ref or ''
    content, source = None, None
    if artifact.is_quarantined and artifact.withheld_content:
        content, source = artifact.withheld_content, 'quarantine'
    elif ref.startswith('agent_action:'):
        from app.models.agent_action import AgentAction
        action = AgentAction.query.get(ref.split(':', 1)[1])
        if action:
            content, source = action.artifact, 'product_store'
    elif ref.startswith('resume:'):
        # Free-tier artifacts (expertise zones) live on the resume, which is
        # the product store for them — same rule, different table.
        import json as _json
        from app.models.resume import Resume
        resume = Resume.query.get(ref.split(':', 1)[1])
        if resume and resume.expertise_zones:
            content = _json.dumps(resume.expertise_zones, indent=2)
            source = 'product_store'
    payload['content'] = content
    payload['content_source'] = source
    return jsonify(payload), 200


@observability_bp.route('/api/admin/observability/artifacts/<artifact_id>/release',
                        methods=['POST'])
@login_required
@admin_required
def release_artifact(artifact_id):
    """Clear a quarantine and deliver the withheld output to its owner."""
    artifact = AgentArtifact.query.get(artifact_id)
    if not artifact:
        return jsonify({'error': 'Artifact not found'}), 404
    if not artifact.is_quarantined:
        return jsonify({'error': 'This artifact is not being withheld.'}), 400

    content = artifact.withheld_content
    ref = artifact.content_ref or ''
    written = False
    if content and ref.startswith('agent_action:'):
        from app.models.agent_action import AgentAction
        from app.models.artifact import ArtifactVersion
        action_id = ref.split(':', 1)[1]
        action = AgentAction.query.get(action_id)
        if action:
            action.artifact = content
            version = ArtifactVersion.query.filter_by(
                action_id=action_id, is_current=True).first()
            if version:
                # Sanctioned exception to original-immutability: releasing a
                # quarantined run restores the agent's own withheld output.
                version._allow_original_write = True
                version.content = content
            written = True

    artifact.is_quarantined = False
    artifact.released_at = datetime.utcnow()
    artifact.released_by = current_user.id
    # Once released the content lives in the product store again, so this copy
    # goes away — the quarantine row is not a second artifact store.
    artifact.withheld_content = None if written else content
    AuditLog.log('observability_artifact_released', user_id=current_user.id,
                 resource_id=artifact_id,
                 metadata={'delivered': written, 'content_ref': ref})
    db.session.commit()

    obs.audit(current_user.id, AdminAccessAudit.RELEASE_ARTIFACT,
              artifact_id, artifact.user_id)
    return jsonify({'artifact': artifact.to_dict(), 'delivered': written}), 200


@observability_bp.route('/api/admin/observability/flags', methods=['GET'])
@login_required
@admin_required
def list_flags():
    query = ObservabilityFlag.query.filter(
        ObservabilityFlag.created_at >= obs.window_start(_hours()))

    layer = (request.args.get('layer') or '').strip().upper()
    if layer in LAYER_CODES:
        query = query.filter(ObservabilityFlag.layer == layer)
    severity = (request.args.get('severity') or '').strip()
    if severity:
        query = query.filter(ObservabilityFlag.severity == severity)
    rule_id = (request.args.get('rule_id') or '').strip()
    if rule_id:
        query = query.filter(ObservabilityFlag.rule_id == rule_id)

    rows = query.order_by(ObservabilityFlag.created_at.desc()).limit(200).all()
    labels = _user_labels(f.user_id for f in rows)
    agents = _agent_labels(f.agent_key for f in rows)
    return jsonify({'flags': [
        f.to_dict(user_label=labels.get(f.user_id),
                  agent_label=agents.get(f.agent_key)) for f in rows
    ]}), 200


@observability_bp.route('/api/admin/observability/audit', methods=['GET'])
@login_required
@admin_required
def list_access_audit():
    """Who looked at whose activity. Reading it is not itself a cross-user read."""
    rows = (AdminAccessAudit.query
            .order_by(AdminAccessAudit.ts.desc()).limit(200).all())
    labels = _user_labels([r.admin_user_id for r in rows] +
                          [r.subject_user_id for r in rows])
    return jsonify({'entries': [
        r.to_dict(admin_label=labels.get(r.admin_user_id),
                  subject_label=labels.get(r.subject_user_id)) for r in rows
    ]}), 200


@observability_bp.route('/api/admin/observability/export', methods=['GET'])
@login_required
@admin_required
def export_runs():
    """CSV of the current filter. An export is a bulk cross-user read, audited."""
    rows = _filtered_runs().limit(5000).all()
    obs.audit(current_user.id, AdminAccessAudit.EXPORT,
              'runs?%s' % request.query_string.decode('utf-8', 'ignore')[:100])

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    columns = ['run_id', 'started_at', 'layer', 'agent_key', 'user_id', 'status',
               'flag_count', 'model', 'prompt_tokens', 'completion_tokens',
               'latency_ms', 'error_code']
    writer.writerow(columns)
    for run in rows:
        record = run.to_dict()
        writer.writerow([record.get(c) for c in columns])

    stamp = datetime.utcnow().strftime('%Y%m%d-%H%M')
    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition':
                 'attachment; filename=agent-runs-%s.csv' % stamp},
    )


@observability_bp.route('/api/admin/observability/stream', methods=['GET'])
@login_required
@admin_required
def stream_runs():
    """Live run feed.

    Bounded on purpose. A long-lived SSE connection holds a Passenger worker for
    its whole life, so this one closes itself after a couple of minutes and the
    console reconnects — the same trade the simulation poller makes.
    """
    from app.blueprints.simulations.sse import sse_event, sse_keepalive

    since = datetime.utcnow()
    deadline = time.monotonic() + 120

    def generate():
        cursor = since
        while time.monotonic() < deadline:
            try:
                rows = (AgentRun.query
                        .filter(AgentRun.started_at > cursor)
                        .order_by(AgentRun.started_at.asc()).limit(50).all())
                if rows:
                    cursor = rows[-1].started_at
                    for run in rows:
                        yield sse_event('run_started', {'run': run.to_dict()})
                finished = (AgentRun.query
                            .filter(AgentRun.finished_at > since)
                            .order_by(AgentRun.finished_at.desc()).limit(20).all())
                for run in finished:
                    yield sse_event('run_finished', {'run': run.to_dict()})
            except Exception as exc:
                logger.debug('observability stream tick failed: %s', exc)
            try:
                yield sse_keepalive()
            except GeneratorExit:
                return
            time.sleep(5)
        yield sse_event('reconnect', {})

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                 'Connection': 'keep-alive'},
    )


# ---------------------------------------------------------------------------
# Govern — rules
# ---------------------------------------------------------------------------

@observability_bp.route('/api/admin/guardrails/rules', methods=['GET'])
@login_required
@admin_required
def list_rules():
    rules = GuardrailRule.query.order_by(GuardrailRule.updated_at.desc()).all()
    since = obs.window_start(24 * 7)
    fired = dict(db.session.query(
        ObservabilityFlag.rule_id, db.func.count(ObservabilityFlag.id),
    ).filter(ObservabilityFlag.created_at >= since)
     .group_by(ObservabilityFlag.rule_id).all())

    payload = []
    for rule in rules:
        record = rule.to_dict()
        record['fired_7d'] = int(fired.get(rule.id, 0))
        record['promotion_warning'] = guardrails.promotion_warning(rule, ACTION_BLOCK)
        payload.append(record)
    return jsonify({'rules': payload,
                    'enforcement_enabled': obs.setting('obs_enforcement_enabled')}), 200


@observability_bp.route('/api/admin/guardrails/rules', methods=['POST'])
@login_required
@admin_required
def create_rule():
    data = request.get_json(silent=True) or {}
    try:
        clean = guardrails.validate_rule(data)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    rule = GuardrailRule(
        id=generate_id(),
        definition_json=clean.pop('definition'),
        created_by=current_user.id,
        **clean,
    )
    db.session.add(rule)
    AuditLog.log('guardrail_rule_created', user_id=current_user.id,
                 resource_id=rule.id,
                 metadata={'name': rule.name, 'action': rule.action,
                           'scope': rule.scope_label})
    db.session.commit()
    obs.invalidate()
    return jsonify({'rule': rule.to_dict()}), 201


@observability_bp.route('/api/admin/guardrails/rules/<rule_id>', methods=['PUT'])
@login_required
@admin_required
def update_rule(rule_id):
    rule = GuardrailRule.query.get(rule_id)
    if not rule:
        return jsonify({'error': 'Rule not found'}), 404

    data = request.get_json(silent=True) or {}
    # A toggle is the common edit; take it without demanding the whole rule.
    if set(data) <= {'is_enabled'}:
        rule.is_enabled = bool(data.get('is_enabled'))
        AuditLog.log('guardrail_rule_toggled', user_id=current_user.id,
                     resource_id=rule.id,
                     metadata={'name': rule.name, 'enabled': rule.is_enabled})
        db.session.commit()
        return jsonify({'rule': rule.to_dict()}), 200

    merged = dict(rule.to_dict(), **data)
    try:
        clean = guardrails.validate_rule(merged, existing=rule)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    was = rule.action
    rule.definition_json = clean.pop('definition')
    for key, value in clean.items():
        setattr(rule, key, value)
    AuditLog.log('guardrail_rule_updated', user_id=current_user.id,
                 resource_id=rule.id,
                 metadata={'name': rule.name, 'action_from': was, 'action_to': rule.action})
    db.session.commit()
    obs.invalidate()
    return jsonify({'rule': rule.to_dict(),
                    'warning': guardrails.promotion_warning(rule, rule.action)}), 200


@observability_bp.route('/api/admin/guardrails/rules/<rule_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_rule(rule_id):
    rule = GuardrailRule.query.get(rule_id)
    if not rule:
        return jsonify({'error': 'Rule not found'}), 404
    # The flags it raised outlive it (their rule_id nulls out), so the history
    # of what was governed survives deleting the rule.
    AuditLog.log('guardrail_rule_deleted', user_id=current_user.id,
                 resource_id=rule.id, metadata={'name': rule.name})
    db.session.delete(rule)
    db.session.commit()
    obs.invalidate()
    return jsonify({'deleted': rule_id}), 200


# ---------------------------------------------------------------------------
# Govern — policies
# ---------------------------------------------------------------------------

@observability_bp.route('/api/admin/guardrails/policies', methods=['GET'])
@login_required
@admin_required
def list_policies():
    policies = GuardrailPolicy.query.order_by(GuardrailPolicy.created_at.desc()).all()
    return jsonify({'policies': [p.to_dict() for p in policies]}), 200


@observability_bp.route('/api/admin/guardrails/policies', methods=['POST'])
@login_required
@admin_required
def create_policy():
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'A policy needs a name.'}), 400
    if GuardrailPolicy.query.filter_by(name=name).first():
        return jsonify({'error': 'A policy named "%s" already exists.' % name}), 400

    rule_ids = [str(r) for r in (data.get('rule_ids') or [])]
    policy = GuardrailPolicy(
        id=generate_id(), name=name,
        description=(str(data.get('description') or '').strip() or None),
        scope=str(data.get('scope') or 'global'),
        scope_ref=(str(data.get('scope_ref') or '').strip() or None),
        rule_ids_json=rule_ids,
        is_active=data.get('is_active', True) not in (False, 'false', '0', 0),
        created_by=current_user.id,
    )
    db.session.add(policy)
    AuditLog.log('guardrail_policy_created', user_id=current_user.id,
                 resource_id=policy.id, metadata={'name': name, 'rules': len(rule_ids)})
    db.session.commit()
    return jsonify({'policy': policy.to_dict()}), 201


@observability_bp.route('/api/admin/guardrails/policies/<policy_id>', methods=['PUT'])
@login_required
@admin_required
def update_policy(policy_id):
    policy = GuardrailPolicy.query.get(policy_id)
    if not policy:
        return jsonify({'error': 'Policy not found'}), 404
    data = request.get_json(silent=True) or {}

    if 'name' in data:
        policy.name = str(data['name']).strip() or policy.name
    if 'description' in data:
        policy.description = str(data['description'] or '').strip() or None
    if 'rule_ids' in data:
        policy.rule_ids_json = [str(r) for r in (data.get('rule_ids') or [])]
    if 'is_active' in data:
        policy.is_active = data['is_active'] not in (False, 'false', '0', 0)
        # Activating a policy is the point of bundling: it switches its rules on
        # as a unit, and deactivating switches them off again.
        if policy.rule_ids:
            GuardrailRule.query.filter(GuardrailRule.id.in_(policy.rule_ids)).update(
                {'is_enabled': policy.is_active}, synchronize_session=False)

    AuditLog.log('guardrail_policy_updated', user_id=current_user.id,
                 resource_id=policy.id,
                 metadata={'name': policy.name, 'active': policy.is_active})
    db.session.commit()
    obs.invalidate()
    return jsonify({'policy': policy.to_dict()}), 200


@observability_bp.route('/api/admin/guardrails/policies/<policy_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_policy(policy_id):
    policy = GuardrailPolicy.query.get(policy_id)
    if not policy:
        return jsonify({'error': 'Policy not found'}), 404
    AuditLog.log('guardrail_policy_deleted', user_id=current_user.id,
                 resource_id=policy.id, metadata={'name': policy.name})
    db.session.delete(policy)
    db.session.commit()
    return jsonify({'deleted': policy_id}), 200


# ---------------------------------------------------------------------------
# Settings & retention
# ---------------------------------------------------------------------------

@observability_bp.route('/api/admin/observability/settings', methods=['GET'])
@login_required
@admin_required
def get_settings():
    return jsonify({'settings': obs.all_settings()}), 200


@observability_bp.route('/api/admin/observability/settings', methods=['PUT'])
@login_required
@admin_required
def put_settings():
    payload = (request.get_json(silent=True) or {}).get('settings')
    if not isinstance(payload, dict):
        return jsonify({'error': 'Send a settings object.'}), 400
    try:
        for key, value in payload.items():
            obs.save_setting(key, value, updated_by=current_user.id)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    AuditLog.log('observability_settings_updated', user_id=current_user.id,
                 metadata={'keys': sorted(payload)})
    db.session.commit()
    return jsonify({'settings': obs.all_settings()}), 200


@observability_bp.route('/api/admin/observability/purge', methods=['POST'])
@login_required
@admin_required
def purge_now():
    """Run the retention purge on demand. The scheduler does this nightly."""
    removed = obs.purge_expired()
    AuditLog.log('observability_purge', user_id=current_user.id, metadata=removed)
    db.session.commit()
    return jsonify({'removed': removed}), 200
