"""SIM-PRD-CAL-001 §13 — Calibration Layer API.

User surface:
    GET  /api/artifacts/<action_id>/calibration   calibrated fields for one artifact
    GET  /api/calibration/<run_id>                methodology detail for the drawer
    POST /api/outcomes                            submit a real-world outcome

Admin surface (§12.8):
    /api/admin/calibration/datasets[/<id>]        dataset CRUD, CSV upload, refresh
    /api/admin/calibration/configs[/<id>]         per-agent field binding + thresholds
    /api/admin/calibration/drift                  drift review, reweight / flag
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime

from flask import jsonify, request
from flask_login import current_user, login_required

from app.blueprints.calibration import calibration_bp
from app.extensions import db
from app.models.agent_action import AgentAction
from app.models.calibration import (
    CalibrationAudit, CalibrationConfig, CalibrationRun, OutcomeReport,
    ReferenceDataPoint, ReferenceDataset,
)
from app.models.simulation import Simulation
from app.services import calibration_service as cal
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)


def _admin_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


def _can_read_sim(sim) -> bool:
    """Owner or accepted, unrevoked collaborator — mirrors artifact_view's rule."""
    if not sim:
        return False
    if sim.user_id == current_user.id:
        return True
    from app.models.collaboration import Collaboration
    return bool(
        Collaboration.query.filter_by(
            simulation_id=sim.id, invitee_email=current_user.email,
        ).filter(
            Collaboration.accepted_at.isnot(None),
            Collaboration.revoked_at.is_(None),
        ).first()
    )


def _owns_run(run) -> bool:
    """A run is readable by anyone who can read its simulation."""
    return _can_read_sim(Simulation.query.get(run.simulation_id))


def _months_since(then: datetime | None) -> int | None:
    if not then:
        return None
    delta = datetime.utcnow() - then
    return max(0, int(delta.days // 30))


# The confidence band is drawn on a track wider than the range itself, so the
# shaded region reads as a region *within* a space of possibilities rather than
# filling the whole widget — which is what makes a wide band look less certain
# than a narrow one (§12.3). This is the padding on each side, as a fraction of
# the range's own width.
_BAND_TRACK_PAD = 0.35


def _band_geometry(low: float, mid: float, high: float, raw: float) -> dict:
    """Tick and shaded-region positions along the band track, as percentages.

    The raw tick is clamped into the track: when calibration moved the estimate
    further than the padded domain, pinning the tick to the edge still reads
    correctly ("the model was well outside this range") and its numeric label
    carries the exact value.
    """
    span = high - low
    if span <= 0:
        return {'low_pct': 25.0, 'mid_pct': 50.0, 'high_pct': 75.0,
                'raw_pct': 50.0, 'raw_clamped': False}

    domain_low = low - _BAND_TRACK_PAD * span
    domain_span = span * (1 + 2 * _BAND_TRACK_PAD)

    def pct(value):
        return (value - domain_low) / domain_span * 100.0

    raw_pct = pct(raw)
    return {
        'low_pct': round(pct(low), 2),
        'mid_pct': round(pct(mid), 2),
        'high_pct': round(pct(high), 2),
        'raw_pct': round(min(100.0, max(0.0, raw_pct)), 2),
        'raw_clamped': raw_pct < 0 or raw_pct > 100,
    }


def _card_payload(run) -> dict:
    """One calibrated result card, ready to render (§12.2)."""
    payload = run.to_dict(include_methodology=True)
    payload['display'] = {
        'low': cal.format_value(payload['low'], run.unit),
        'mid': cal.format_value(payload['mid'], run.unit),
        'high': cal.format_value(payload['high'], run.unit),
        'raw': cal.format_value(payload['raw'], run.unit),
        'ref_p50': (
            cal.format_value(payload['ref_p50'], run.unit)
            if payload.get('ref_p50') is not None else None
        ),
    }
    payload['band'] = _band_geometry(
        payload['low'], payload['mid'], payload['high'], payload['raw'],
    )

    from app.services.calibration_cohort import describe_cohort
    payload['cohort_label'] = describe_cohort(payload.get('cohort') or {})
    payload['tier_hint'] = cal.tier_upgrade_hint(run)

    report = OutcomeReport.query.filter_by(
        user_id=current_user.id, run_id=run.id,
    ).first() if current_user.is_authenticated else None
    payload['outcome'] = {
        'open': cal.outcomes_open(),
        'reported': report.to_dict() if report else None,
    }
    return payload


# ---------------------------------------------------------------------------
# User API
# ---------------------------------------------------------------------------

@calibration_bp.route('/api/artifacts/<action_id>/calibration', methods=['GET'])
@login_required
def api_artifact_calibration(action_id):
    """Calibrated fields for one artifact — what the card renders."""
    action = AgentAction.query.get(action_id)
    if not action:
        return jsonify({'error': 'not_found'}), 404
    sim = Simulation.query.get(action.simulation_id)
    # Authorise on the simulation, not on a run: an artifact with no calibrated
    # fields must return an empty list to a collaborator, not a 403.
    if not _can_read_sim(sim):
        return jsonify({'error': 'forbidden'}), 403

    runs = cal.runs_for_action(action_id)
    return jsonify({
        'action_id': action_id,
        'enabled': cal.is_enabled(),
        'fields': [_card_payload(r) for r in runs],
    })


@calibration_bp.route('/api/calibration/<run_id>', methods=['GET'])
@login_required
def api_calibration_detail(run_id):
    """Stored calibration detail for the methodology drawer (§12.5)."""
    run = CalibrationRun.query.get(run_id)
    if not run:
        return jsonify({'error': 'not_found'}), 404
    if not _owns_run(run):
        return jsonify({'error': 'forbidden'}), 403
    return jsonify(_card_payload(run))


@calibration_bp.route('/api/simulations/<sim_id>/calibration', methods=['GET'])
@login_required
def api_simulation_calibration(sim_id):
    """Compact calibrated summary across a simulation, for the detail page."""
    sim = Simulation.query.get(sim_id)
    if not sim:
        return jsonify({'error': 'not_found'}), 404
    if not _can_read_sim(sim) and not current_user.is_admin:
        return jsonify({'error': 'forbidden'}), 403

    runs = cal.runs_for_simulation(sim_id)
    fields = []
    for r in runs:
        item = r.to_dict(include_methodology=False)
        item['display'] = {
            'low': cal.format_value(item['low'], r.unit),
            'high': cal.format_value(item['high'], r.unit),
            'mid': cal.format_value(item['mid'], r.unit),
        }
        item['action_id'] = r.action_id
        fields.append(item)
    return jsonify({'simulation_id': sim_id, 'enabled': cal.is_enabled(), 'fields': fields})


@calibration_bp.route('/api/outcomes', methods=['POST'])
@login_required
def api_submit_outcome():
    """Submit a real-world outcome against a calibration run (§10, §12.7).

    Reports are private: stored against the user, surfaced only in aggregate.
    Re-submitting updates the existing report rather than double-counting the
    same person's outcome in a cohort.
    """
    if not cal.outcomes_open():
        return jsonify({'error': 'outcome_reporting_closed'}), 403

    body = request.get_json(silent=True) or {}
    run_id = (body.get('run_id') or '').strip()
    run = CalibrationRun.query.get(run_id) if run_id else None
    if not run:
        return jsonify({'error': 'run_not_found'}), 404
    if not _owns_run(run):
        return jsonify({'error': 'forbidden'}), 403

    value = cal.coerce_number(body.get('reported_value'))
    if value is None or value <= 0:
        return jsonify({'error': 'invalid_value',
                        'message': 'Enter the actual number you achieved.'}), 400

    note = (body.get('note') or '').strip()[:500] or None

    report = OutcomeReport.query.filter_by(
        user_id=current_user.id, run_id=run.id,
    ).first()
    created = report is None
    if created:
        report = OutcomeReport(
            id=generate_id(),
            user_id=current_user.id,
            run_id=run.id,
            simulation_id=run.simulation_id,
            agent_key=run.agent_key,
            output_field=run.output_field,
            cohort_json=run.cohort_json,
            cohort_hash=run.cohort_hash,
        )
        db.session.add(report)

    report.reported_value = value
    report.months_elapsed = _months_since(run.created_at)
    report.note = note
    report.reported_at = datetime.utcnow()

    try:
        db.session.commit()
    except Exception as exc:
        logger.error('Outcome report save failed for run %s: %s', run.id, exc)
        db.session.rollback()
        return jsonify({'error': 'save_failed'}), 500

    return jsonify({
        'ok': True,
        'created': created,
        'report': report.to_dict(),
        # §12.7 — a warm confirmation that frames the contribution honestly.
        'message': (
            'Thank you — recorded privately. Your number now helps sharpen this '
            'forecast for other people in your situation. It is never shown to '
            'anyone individually.'
        ),
    }), (201 if created else 200)


# ---------------------------------------------------------------------------
# Admin — datasets
# ---------------------------------------------------------------------------

_DATASET_FIELDS = (
    'layer', 'name', 'source', 'source_url', 'unit', 'geography_scope',
    'credibility_tier', 'as_of_label', 'refresh_cadence_days',
    'derivation_note',
)


@calibration_bp.route('/api/admin/calibration/datasets', methods=['GET'])
@login_required
@_admin_required
def admin_list_datasets():
    rows = ReferenceDataset.query.order_by(
        ReferenceDataset.layer, ReferenceDataset.name,
    ).all()
    return jsonify([d.to_dict() for d in rows])


@calibration_bp.route('/api/admin/calibration/datasets', methods=['POST'])
@login_required
@_admin_required
def admin_create_dataset():
    body = request.get_json(silent=True) or {}
    if not (body.get('name') and body.get('source') and body.get('layer')):
        return jsonify({'error': 'name, source and layer are required'}), 400

    dataset = ReferenceDataset(id=generate_id())
    for field in _DATASET_FIELDS:
        if body.get(field) is not None:
            setattr(dataset, field, body[field])
    # A newly created dataset is unverified by definition, so it cannot render
    # High confidence until an admin explicitly reviews it.
    dataset.needs_review = True
    dataset.last_refreshed = date.today()
    db.session.add(dataset)
    db.session.commit()
    cal.invalidate_cache()
    return jsonify(dataset.to_dict()), 201


@calibration_bp.route('/api/admin/calibration/datasets/<dataset_id>', methods=['PUT'])
@login_required
@_admin_required
def admin_update_dataset(dataset_id):
    dataset = ReferenceDataset.query.get(dataset_id)
    if not dataset:
        return jsonify({'error': 'not_found'}), 404

    body = request.get_json(silent=True) or {}
    for field in _DATASET_FIELDS:
        if field in body:
            setattr(dataset, field, body[field])
    for flag in ('is_active', 'needs_review'):
        if flag in body:
            setattr(dataset, flag, bool(body[flag]))
    if body.get('mark_refreshed'):
        dataset.last_refreshed = date.today()

    db.session.commit()
    cal.invalidate_cache()   # R5 — stale cached posteriors must not survive a refresh
    return jsonify(dataset.to_dict())


@calibration_bp.route('/api/admin/calibration/datasets/<dataset_id>', methods=['DELETE'])
@login_required
@_admin_required
def admin_delete_dataset(dataset_id):
    dataset = ReferenceDataset.query.get(dataset_id)
    if not dataset:
        return jsonify({'error': 'not_found'}), 404

    bound = CalibrationConfig.query.filter_by(dataset_id=dataset_id).count()
    if bound and not request.args.get('force'):
        return jsonify({
            'error': 'dataset_in_use',
            'message': '{} agent config(s) are bound to this dataset. Deactivate '
                       'it instead, or pass ?force=1.'.format(bound),
        }), 409

    # Unbind rather than orphan: a config pointing at a deleted dataset would
    # render raw silently, which is harder to notice than an explicit unbind.
    CalibrationConfig.query.filter_by(dataset_id=dataset_id).update({'dataset_id': None})
    ReferenceDataPoint.query.filter_by(dataset_id=dataset_id).delete()
    db.session.delete(dataset)
    db.session.commit()
    cal.invalidate_cache()
    return jsonify({'ok': True})


@calibration_bp.route('/api/admin/calibration/datasets/<dataset_id>/points', methods=['GET'])
@login_required
@_admin_required
def admin_list_points(dataset_id):
    rows = ReferenceDataPoint.query.filter_by(dataset_id=dataset_id).order_by(
        ReferenceDataPoint.sample_size.desc(),
    ).limit(500).all()
    return jsonify([p.to_dict() for p in rows])


_CSV_HELP = (
    'Expected header: soc_group,metro,seniority,p10,p50,p90,mean,sample_size,as_of_date. '
    'Only p50 is required. metro defaults to "national" and seniority to "any".'
)


@calibration_bp.route('/api/admin/calibration/datasets/<dataset_id>/upload', methods=['POST'])
@login_required
@_admin_required
def admin_upload_points(dataset_id):
    """Upload reference rows as CSV (§12.8).

    Upserts on (dataset, cohort) so re-uploading a corrected extract replaces
    rows rather than duplicating them.
    """
    dataset = ReferenceDataset.query.get(dataset_id)
    if not dataset:
        return jsonify({'error': 'not_found'}), 404

    upload = request.files.get('file')
    raw = None
    if upload:
        raw = upload.read().decode('utf-8-sig', errors='replace')
    else:
        raw = (request.get_json(silent=True) or {}).get('csv')
    if not raw or not raw.strip():
        return jsonify({'error': 'no_csv', 'message': _CSV_HELP}), 400

    from app.services.calibration_cohort import (
        METRO_ANY, SENIORITY_ANY, cohort_hash,
    )

    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or 'p50' not in [f.strip().lower() for f in reader.fieldnames]:
        return jsonify({'error': 'bad_header', 'message': _CSV_HELP}), 400

    def _num(row, key):
        val = (row.get(key) or '').strip().replace(',', '').replace('$', '')
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            return None

    created = updated = skipped = 0
    errors = []
    for line_no, row in enumerate(reader, start=2):
        row = {(k or '').strip().lower(): v for k, v in row.items()}
        p50 = _num(row, 'p50')
        soc = (row.get('soc_group') or '').strip()
        if p50 is None or not soc:
            skipped += 1
            if len(errors) < 10:
                errors.append('line {}: soc_group and p50 are both required'.format(line_no))
            continue

        cohort = {
            'soc_group': soc,
            'metro': (row.get('metro') or '').strip() or METRO_ANY,
            'seniority': (row.get('seniority') or '').strip() or SENIORITY_ANY,
        }
        as_of = None
        raw_date = (row.get('as_of_date') or '').strip()
        if raw_date:
            try:
                as_of = datetime.strptime(raw_date[:10], '%Y-%m-%d').date()
            except ValueError:
                if len(errors) < 10:
                    errors.append('line {}: as_of_date must be YYYY-MM-DD'.format(line_no))

        digest = cohort_hash(cohort)
        point = ReferenceDataPoint.query.filter_by(
            dataset_id=dataset_id, cohort_hash=digest,
        ).first()
        if point is None:
            point = ReferenceDataPoint(
                id=generate_id(), dataset_id=dataset_id, cohort_hash=digest,
            )
            db.session.add(point)
            created += 1
        else:
            updated += 1

        point.cohort_json = cohort
        point.p10 = _num(row, 'p10')
        point.p50 = p50
        point.p90 = _num(row, 'p90')
        point.mean = _num(row, 'mean')
        point.sample_size = int(_num(row, 'sample_size') or 0)
        point.as_of_date = as_of

    dataset.last_refreshed = date.today()
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('Reference upload failed for dataset %s: %s', dataset_id, exc)
        return jsonify({'error': 'save_failed', 'message': str(exc)[:200]}), 500

    cal.invalidate_cache()
    return jsonify({
        'ok': True, 'created': created, 'updated': updated,
        'skipped': skipped, 'errors': errors,
    })


# ---------------------------------------------------------------------------
# Admin — per-agent configs
# ---------------------------------------------------------------------------

_CONFIG_TEXT_FIELDS = (
    'agent_key', 'layer', 'dataset_id', 'output_field', 'field_label',
    'unit', 'method',
)
_CONFIG_NUM_FIELDS = (
    'sigma_model_pct', 'min_sample_high', 'min_sample_moderate',
    'band_floor_pct', 'drift_threshold_pct', 'min_reports_to_reweight',
)


@calibration_bp.route('/api/admin/calibration/configs', methods=['GET'])
@login_required
@_admin_required
def admin_list_configs():
    rows = CalibrationConfig.query.order_by(
        CalibrationConfig.layer, CalibrationConfig.agent_key,
        CalibrationConfig.output_field,
    ).all()
    datasets = {d.id: d for d in ReferenceDataset.query.all()}
    out = []
    for c in rows:
        item = c.to_dict()
        dataset = datasets.get(c.dataset_id)
        item['dataset_name'] = dataset.name if dataset else None
        item['dataset_unit'] = dataset.unit if dataset else None
        # A unit mismatch means the engine will refuse this binding at run time
        # (see calibrate_value). Surface it here so it is fixable rather than a
        # silent no-op discovered only in the logs.
        item['unit_mismatch'] = bool(dataset and dataset.unit != c.unit)
        item['run_count'] = CalibrationRun.query.filter_by(
            agent_key=c.agent_key, output_field=c.output_field,
        ).count()
        out.append(item)
    return jsonify(out)


@calibration_bp.route('/api/admin/calibration/configs', methods=['POST'])
@login_required
@_admin_required
def admin_create_config():
    body = request.get_json(silent=True) or {}
    for required in ('agent_key', 'output_field', 'field_label', 'layer'):
        if not body.get(required):
            return jsonify({'error': '{} is required'.format(required)}), 400

    from app.services.agent_registry import resolve_alias
    agent_key = resolve_alias(body['agent_key'])
    if CalibrationConfig.query.filter_by(
        agent_key=agent_key, output_field=body['output_field'],
    ).first():
        return jsonify({'error': 'duplicate',
                        'message': 'This agent already has a config for that field.'}), 409

    config = CalibrationConfig(id=generate_id())
    for field in _CONFIG_TEXT_FIELDS:
        if body.get(field) is not None:
            setattr(config, field, body[field])
    config.agent_key = agent_key
    for field in _CONFIG_NUM_FIELDS:
        if body.get(field) is not None:
            setattr(config, field, body[field])
    if isinstance(body.get('percentiles'), list) and len(body['percentiles']) == 3:
        config.percentiles_json = body['percentiles']
    config.is_enabled = bool(body.get('is_enabled', True))

    db.session.add(config)
    db.session.commit()
    cal.invalidate_cache()
    return jsonify(config.to_dict()), 201


@calibration_bp.route('/api/admin/calibration/configs/<config_id>', methods=['PUT'])
@login_required
@_admin_required
def admin_update_config(config_id):
    config = CalibrationConfig.query.get(config_id)
    if not config:
        return jsonify({'error': 'not_found'}), 404

    body = request.get_json(silent=True) or {}
    for field in _CONFIG_TEXT_FIELDS:
        if field in body:
            setattr(config, field, body[field] or None)
    for field in _CONFIG_NUM_FIELDS:
        if field in body and body[field] is not None:
            setattr(config, field, body[field])
    if isinstance(body.get('percentiles'), list) and len(body['percentiles']) == 3:
        config.percentiles_json = body['percentiles']
    if 'is_enabled' in body:
        config.is_enabled = bool(body['is_enabled'])

    # min_sample_high below min_sample_moderate would make High unreachable while
    # silently leaving the thin-data guard armed — reject rather than accept an
    # incoherent config.
    if config.min_sample_high < config.min_sample_moderate:
        db.session.rollback()
        return jsonify({
            'error': 'invalid_thresholds',
            'message': 'min_sample_high must be at least min_sample_moderate.',
        }), 400

    db.session.commit()
    cal.invalidate_cache()
    return jsonify(config.to_dict())


@calibration_bp.route('/api/admin/calibration/configs/<config_id>', methods=['DELETE'])
@login_required
@_admin_required
def admin_delete_config(config_id):
    config = CalibrationConfig.query.get(config_id)
    if not config:
        return jsonify({'error': 'not_found'}), 404
    db.session.delete(config)
    db.session.commit()
    cal.invalidate_cache()
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# Admin — drift monitor
# ---------------------------------------------------------------------------

@calibration_bp.route('/api/admin/calibration/drift', methods=['GET'])
@login_required
@_admin_required
def admin_drift():
    """Live drift evaluation plus the recent audit trail (§12.8)."""
    from app.services.calibration_drift import evaluate_all, reweight_is_live

    limit = min(int(request.args.get('limit', 100)), 500)
    history = CalibrationAudit.query.order_by(
        CalibrationAudit.created_at.desc(),
    ).limit(limit).all()

    return jsonify({
        'reweight_live': reweight_is_live(),
        'cohorts': evaluate_all(persist=False),
        'history': [a.to_dict() for a in history],
    })


@calibration_bp.route('/api/admin/calibration/drift/run', methods=['POST'])
@login_required
@_admin_required
def admin_drift_run():
    """Run the drift evaluation now and persist the audit rows."""
    from app.services.calibration_drift import run_drift_job
    return jsonify(run_drift_job(triggered_by='admin'))


@calibration_bp.route('/api/admin/calibration/drift/reweight', methods=['POST'])
@login_required
@_admin_required
def admin_drift_reweight():
    """Apply a one-click reweight to a single cohort (§12.8).

    Explicit and per-cohort: this is available even while the automatic nightly
    reweight is switched off, because an admin acting on a reviewed cohort is a
    different risk from the job acting on all of them unattended.
    """
    body = request.get_json(silent=True) or {}
    cohort_hash = (body.get('cohort_hash') or '').strip()
    agent_key = (body.get('agent_key') or '').strip()
    output_field = (body.get('output_field') or '').strip()
    if not (cohort_hash and agent_key and output_field):
        return jsonify({'error': 'cohort_hash, agent_key and output_field are required'}), 400

    from app.services.calibration_drift import reweight_cohort
    result = reweight_cohort(agent_key, output_field, cohort_hash, force=bool(body.get('force')))
    status = 200 if result.get('ok') else 409
    return jsonify(result), status


@calibration_bp.route('/api/admin/calibration/outcomes', methods=['GET'])
@login_required
@_admin_required
def admin_list_outcomes():
    """Outcome reports for verification. Individual values stay admin-only."""
    q = OutcomeReport.query
    if request.args.get('agent_key'):
        q = q.filter_by(agent_key=request.args['agent_key'])
    if request.args.get('unverified') == '1':
        q = q.filter_by(is_verified=False)
    rows = q.order_by(OutcomeReport.reported_at.desc()).limit(300).all()
    return jsonify([r.to_dict() for r in rows])


@calibration_bp.route('/api/admin/calibration/outcomes/<report_id>', methods=['PUT'])
@login_required
@_admin_required
def admin_update_outcome(report_id):
    """Verify or exclude a report. Only verified, non-excluded reports count."""
    report = OutcomeReport.query.get(report_id)
    if not report:
        return jsonify({'error': 'not_found'}), 404
    body = request.get_json(silent=True) or {}
    if 'is_verified' in body:
        report.is_verified = bool(body['is_verified'])
    if 'is_excluded' in body:
        report.is_excluded = bool(body['is_excluded'])
    db.session.commit()
    return jsonify(report.to_dict())
