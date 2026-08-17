"""SIM-PRD-CAL-001 §10 — the outcome feedback loop (flywheel).

Nightly, this groups verified outcome reports by cohort, compares them to the
current reference distribution, and records the divergence. Two rules govern
whether anything is allowed to change:

  Optimal-stopping gate  a cohort is not reweighted until it has at least
                         min_reports_to_reweight verified reports. Early reports
                         are recorded and shown, never acted on — don't chase
                         noise from the first few.
  Reweight switch        even past the gate, automatic reweighting only happens
                         when calibration_reweight_live is on. Default is off, so
                         the job's steady state is *observe and flag*.

A reweight is deliberately conservative in two ways. Reports are blended at
parity with reference observations rather than amplified, so 30 reports barely
move a 1,200-observation reference — which is the correct magnitude for 30 data
points. And any reweighted dataset is flipped back to needs_review, because its
numbers are no longer exactly what its cited source published and it must not
claim High confidence again until a human agrees with the blend.
"""
from __future__ import annotations

import logging
from datetime import datetime
from statistics import median

logger = logging.getLogger(__name__)


def reweight_is_live() -> bool:
    """Phase-4 switch (§14). Off means observe-and-flag only."""
    try:
        from app.models.platform_settings import PlatformSetting
        return (PlatformSetting.get('calibration_reweight_live', 'false') or 'false').lower() == 'true'
    except Exception:
        return False


def _verified_reports(agent_key: str = None, output_field: str = None,
                      cohort_hash: str = None) -> list:
    from app.models.calibration import OutcomeReport
    q = OutcomeReport.query.filter_by(is_verified=True, is_excluded=False)
    if agent_key:
        q = q.filter_by(agent_key=agent_key)
    if output_field:
        q = q.filter_by(output_field=output_field)
    if cohort_hash:
        q = q.filter_by(cohort_hash=cohort_hash)
    return q.all()


def _group_reports() -> dict:
    """Verified reports keyed by (agent_key, output_field, cohort_hash)."""
    groups = {}
    for report in _verified_reports():
        if not report.cohort_hash:
            continue
        key = (report.agent_key, report.output_field, report.cohort_hash)
        groups.setdefault(key, []).append(report)
    return groups


def evaluate_cohort(agent_key: str, output_field: str, cohort_hash: str,
                    reports: list = None) -> dict | None:
    """Compare one cohort's reported outcomes to its reference distribution.

    Returns a plain dict — no writes — so the admin drift monitor can render a
    live view without side effects, and run_drift_job can persist the same
    evaluation.
    """
    from app.models.calibration import (
        CalibrationConfig, ReferenceDataPoint, CalibrationAudit,
    )

    config = CalibrationConfig.query.filter_by(
        agent_key=agent_key, output_field=output_field,
    ).first()
    if not config or not config.dataset_id:
        return None

    if reports is None:
        reports = _verified_reports(agent_key, output_field, cohort_hash)
    if not reports:
        return None

    point = ReferenceDataPoint.query.filter_by(
        dataset_id=config.dataset_id, cohort_hash=cohort_hash,
    ).first()
    if not point:
        # Reports exist for a cohort we hold no reference row for. That is a
        # genuine signal — it says where our data coverage is missing — so it is
        # surfaced rather than silently dropped.
        return {
            'agent_key': agent_key,
            'output_field': output_field,
            'cohort_hash': cohort_hash,
            'cohort': reports[0].cohort_json or {},
            'dataset_id': config.dataset_id,
            'n_reports': len(reports),
            'ref_p50': None,
            'reported_p50': float(median(float(r.reported_value) for r in reports)),
            'drift_pct': None,
            'gate': config.min_reports_to_reweight,
            'gate_cleared': len(reports) >= config.min_reports_to_reweight,
            'threshold_pct': float(config.drift_threshold_pct),
            'exceeds_threshold': False,
            'action': CalibrationAudit.ACTION_FLAGGED,
            'notes': 'Reports received for a cohort with no reference row — '
                     'coverage gap, not drift.',
        }

    ref_p50 = float(point.p50)
    reported_p50 = float(median(float(r.reported_value) for r in reports))
    drift_pct = ((reported_p50 - ref_p50) / ref_p50 * 100.0) if ref_p50 else None

    n = len(reports)
    gate = int(config.min_reports_to_reweight or 30)
    gate_cleared = n >= gate
    threshold = float(config.drift_threshold_pct)
    exceeds = drift_pct is not None and abs(drift_pct) > threshold

    if not exceeds:
        action, notes = CalibrationAudit.ACTION_NONE, 'Within threshold.'
    elif not gate_cleared:
        action = CalibrationAudit.ACTION_GATED
        notes = ('Drift exceeds threshold but only {} of {} reports needed to '
                 'reweight — holding.'.format(n, gate))
    else:
        action = CalibrationAudit.ACTION_FLAGGED
        notes = 'Drift exceeds threshold and the reweight gate is cleared.'

    return {
        'agent_key': agent_key,
        'output_field': output_field,
        'cohort_hash': cohort_hash,
        'cohort': point.cohort_json or (reports[0].cohort_json or {}),
        'dataset_id': config.dataset_id,
        'point_id': point.id,
        'n_reports': n,
        'ref_p50': ref_p50,
        'ref_sample_size': point.sample_size,
        'reported_p50': reported_p50,
        'drift_pct': round(drift_pct, 2) if drift_pct is not None else None,
        'gate': gate,
        'gate_cleared': gate_cleared,
        'threshold_pct': threshold,
        'exceeds_threshold': exceeds,
        'action': action,
        'notes': notes,
    }


def evaluate_all(persist: bool = False) -> list:
    """Evaluate every cohort that has verified reports."""
    from app.extensions import db
    from app.models.calibration import CalibrationAudit
    from utils.id_gen import generate_id

    results = []
    for (agent_key, output_field, cohort_hash), reports in _group_reports().items():
        try:
            result = evaluate_cohort(agent_key, output_field, cohort_hash, reports)
        except Exception as exc:
            logger.warning(
                'Drift evaluation failed for %s.%s/%s: %s',
                agent_key, output_field, cohort_hash, exc,
            )
            continue
        if not result:
            continue
        results.append(result)

        if persist:
            db.session.add(CalibrationAudit(
                id=generate_id(),
                dataset_id=result.get('dataset_id'),
                agent_key=agent_key,
                output_field=output_field,
                cohort_hash=cohort_hash,
                cohort_json=result.get('cohort'),
                drift_pct=result.get('drift_pct'),
                n_reports=result.get('n_reports') or 0,
                ref_p50=result.get('ref_p50'),
                reported_p50=result.get('reported_p50'),
                action=result.get('action'),
                notes=(result.get('notes') or '')[:500],
            ))

    if persist:
        try:
            db.session.commit()
        except Exception as exc:
            logger.error('Could not persist calibration audit rows: %s', exc)
            db.session.rollback()

    results.sort(key=lambda r: abs(r.get('drift_pct') or 0), reverse=True)
    return results


def reweight_cohort(agent_key: str, output_field: str, cohort_hash: str,
                    force: bool = False) -> dict:
    """Blend verified reports into one cohort's reference distribution.

    force=True bypasses the optimal-stopping gate. It exists for an admin who has
    reviewed a cohort and accepts a thinner sample; the nightly job never sets it.
    """
    from app.extensions import db
    from app.models.calibration import (
        CalibrationAudit, ReferenceDataPoint, ReferenceDataset,
    )
    from app.services import calibration_service as cal
    from utils.id_gen import generate_id

    result = evaluate_cohort(agent_key, output_field, cohort_hash)
    if not result:
        return {'ok': False, 'error': 'no_evaluation',
                'message': 'No verified reports, or no calibration config for this field.'}
    if not result.get('point_id'):
        return {'ok': False, 'error': 'no_reference_row',
                'message': 'No reference row for this cohort — upload data for it first.'}
    if not result['gate_cleared'] and not force:
        return {'ok': False, 'error': 'gate_not_cleared',
                'message': 'Only {} of {} reports needed to reweight.'.format(
                    result['n_reports'], result['gate'])}

    point = ReferenceDataPoint.query.get(result['point_id'])
    if not point:
        return {'ok': False, 'error': 'no_reference_row'}

    n_ref = max(int(point.sample_size or 0), 1)
    n_rep = int(result['n_reports'])
    old_p50 = float(point.p50)
    reported_p50 = float(result['reported_p50'])

    # Parity blend. Our reports are not weighted above the published source, so a
    # small number of them moves the anchor only slightly — the conservative
    # behaviour the optimal-stopping principle asks for.
    new_p50 = (n_ref * old_p50 + n_rep * reported_p50) / (n_ref + n_rep)

    # Shift p10/p90 by the same proportion so the distribution's shape survives
    # the blend; reports give us a central tendency, not a new spread.
    scale = (new_p50 / old_p50) if old_p50 else 1.0
    point.p50 = new_p50
    if point.p10 is not None:
        point.p10 = float(point.p10) * scale
    if point.p90 is not None:
        point.p90 = float(point.p90) * scale
    if point.mean is not None:
        point.mean = float(point.mean) * scale
    point.sample_size = n_ref + n_rep

    # Plain ASCII: this string lands in logs, an audit column and an admin alert.
    note = 'Reweighted {} on {}: p50 {:.2f} -> {:.2f} from {} verified outcome report(s).'.format(
        cohort_hash[:8], datetime.utcnow().strftime('%Y-%m-%d'),
        old_p50, new_p50, n_rep,
    )

    dataset = ReferenceDataset.query.get(point.dataset_id)
    demoted = False
    if dataset is not None:
        # The dataset's numbers are no longer exactly what its cited source
        # published, so it goes back into review and cannot render High
        # confidence until an admin re-verifies it.
        if not dataset.needs_review:
            dataset.needs_review = True
            demoted = True
        existing = dataset.derivation_note or ''
        dataset.derivation_note = (existing + '\n' + note).strip()[:4000]

    db.session.add(CalibrationAudit(
        id=generate_id(),
        dataset_id=point.dataset_id,
        agent_key=agent_key,
        output_field=output_field,
        cohort_hash=cohort_hash,
        cohort_json=point.cohort_json,
        drift_pct=result.get('drift_pct'),
        n_reports=n_rep,
        ref_p50=old_p50,
        reported_p50=reported_p50,
        action=CalibrationAudit.ACTION_REWEIGHTED,
        notes=note[:500],
    ))

    try:
        db.session.commit()
    except Exception as exc:
        logger.error('Reweight failed for %s/%s: %s', agent_key, cohort_hash, exc)
        db.session.rollback()
        return {'ok': False, 'error': 'save_failed', 'message': str(exc)[:200]}

    cal.invalidate_cache()
    return {
        'ok': True,
        'old_p50': old_p50,
        'new_p50': new_p50,
        'n_reports': n_rep,
        'sample_size': point.sample_size,
        'dataset_returned_to_review': demoted,
        'message': note + (
            ' The dataset was returned to review status, so it will render at '
            'Moderate until you re-verify it.' if demoted else ''
        ),
    }


def run_drift_job(triggered_by: str = 'scheduler') -> dict:
    """Nightly entry point: evaluate all cohorts, persist audit, maybe reweight."""
    results = evaluate_all(persist=True)
    live = reweight_is_live()

    reweighted = []
    if live:
        for r in results:
            if r.get('exceeds_threshold') and r.get('gate_cleared') and r.get('point_id'):
                outcome = reweight_cohort(
                    r['agent_key'], r['output_field'], r['cohort_hash'],
                )
                if outcome.get('ok'):
                    reweighted.append({
                        'agent_key': r['agent_key'],
                        'output_field': r['output_field'],
                        'cohort_hash': r['cohort_hash'],
                        'old_p50': outcome['old_p50'],
                        'new_p50': outcome['new_p50'],
                    })

    summary = {
        'triggered_by': triggered_by,
        'cohorts_evaluated': len(results),
        'flagged': sum(1 for r in results if r.get('exceeds_threshold')),
        'gated': sum(1 for r in results
                     if r.get('exceeds_threshold') and not r.get('gate_cleared')),
        'reweight_live': live,
        'reweighted': reweighted,
    }
    logger.info('Calibration drift job: %s', summary)
    return summary
