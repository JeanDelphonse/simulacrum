from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from flask import jsonify, request
from flask_login import current_user, login_required
from sqlalchemy import func

from app.blueprints.income import income_bp
from app.extensions import db
from app.models.income import LayerIncomeRecord
from app.models.simulation import Simulation
from utils.id_gen import generate_id

logger = logging.getLogger(__name__)

_EMA_ALPHA = 0.3  # Bayesian EMA smoothing factor for outcome updates


def _check_sim(sim_id: str):
    """Return (sim, None, None) or (None, error_response, code)."""
    sim = Simulation.query.filter_by(id=sim_id, user_id=current_user.id).first()
    if not sim:
        return None, jsonify({'error': 'Simulation not found'}), 404
    return sim, None, None


def _update_layer_outcome(sim_id: str, layer_number: int, reporting_month: str):
    """Recalculate layer6_outcomes row for this layer/month from all non-void records."""
    from app.models.layer6 import Layer6Outcome

    total = db.session.query(func.sum(LayerIncomeRecord.amount)).filter_by(
        simulation_id=sim_id,
        layer_number=layer_number,
        is_void=False,
    ).filter(
        func.date_format(LayerIncomeRecord.income_date, '%Y-%m') == reporting_month
    ).scalar() or Decimal('0')

    count = LayerIncomeRecord.query.filter_by(
        simulation_id=sim_id,
        layer_number=layer_number,
        is_void=False,
    ).filter(
        func.date_format(LayerIncomeRecord.income_date, '%Y-%m') == reporting_month
    ).count()

    last_rec = LayerIncomeRecord.query.filter_by(
        simulation_id=sim_id,
        layer_number=layer_number,
        is_void=False,
    ).filter(
        func.date_format(LayerIncomeRecord.income_date, '%Y-%m') == reporting_month
    ).order_by(LayerIncomeRecord.income_date.desc()).first()

    outcome = Layer6Outcome.query.filter_by(
        simulation_id=sim_id,
        layer_number=layer_number,
        reporting_month=reporting_month,
    ).first()

    if outcome is None:
        outcome = Layer6Outcome(
            id=generate_id(),
            simulation_id=sim_id,
            layer_number=layer_number,
            reporting_month=reporting_month,
            actual_income=total,
            projected_income=total,
        )
        db.session.add(outcome)
    else:
        # EMA update: blend new confirmed total with prior projected income
        prior_projected = float(outcome.projected_income or 0)
        new_projected = _EMA_ALPHA * float(total) + (1 - _EMA_ALPHA) * prior_projected
        outcome.actual_income = total
        outcome.projected_income = Decimal(str(round(new_projected, 2)))
        outcome.variance = total - outcome.projected_income

    # Update confirmed columns if they exist
    try:
        outcome.actual_income_confirmed = total
        outcome.income_record_count = count
        if last_rec:
            outcome.last_income_date = last_rec.income_date
    except Exception:
        pass  # columns not yet migrated


# ── List & Create ─────────────────────────────────────────────────────────────

@income_bp.route('/<sim_id>/income', methods=['GET'])
@login_required
def list_income(sim_id: str):
    sim, err, code = _check_sim(sim_id)
    if err:
        return err, code

    layer = request.args.get('layer', type=int)
    include_void = request.args.get('include_void', '0') == '1'
    limit = min(request.args.get('limit', 100, type=int), 500)

    q = LayerIncomeRecord.query.filter_by(simulation_id=sim_id)
    if layer:
        q = q.filter_by(layer_number=layer)
    if not include_void:
        q = q.filter_by(is_void=False)
    records = q.order_by(LayerIncomeRecord.income_date.desc(),
                         LayerIncomeRecord.created_at.desc()).limit(limit).all()

    return jsonify([r.to_dict() for r in records])


@income_bp.route('/<sim_id>/income', methods=['POST'])
@login_required
def record_income(sim_id: str):
    sim, err, code = _check_sim(sim_id)
    if err:
        return err, code

    data = request.get_json(force=True) or {}

    try:
        amount = Decimal(str(data.get('amount', 0)))
    except Exception:
        return jsonify({'error': 'Invalid amount'}), 400

    if amount <= 0:
        return jsonify({'error': 'Amount must be greater than zero'}), 400

    layer_number = data.get('layer_number')
    if layer_number not in (1, 2, 3, 4, 5):
        return jsonify({'error': 'layer_number must be 1–5'}), 400

    raw_date = data.get('income_date')
    try:
        income_date = date.fromisoformat(raw_date) if raw_date else date.today()
    except ValueError:
        return jsonify({'error': 'Invalid income_date format (YYYY-MM-DD)'}), 400

    record = LayerIncomeRecord(
        id=generate_id(),
        simulation_id=sim_id,
        layer_number=layer_number,
        action_id=data.get('action_id'),
        action_type=data.get('action_type'),
        amount=amount,
        currency=data.get('currency', 'USD').upper()[:3],
        income_date=income_date,
        source=data.get('source', LayerIncomeRecord.SOURCE_MANUAL),
        source_ref=data.get('source_ref'),
        description=data.get('description'),
        recorded_by=current_user.id,
    )
    db.session.add(record)
    db.session.flush()

    reporting_month = income_date.strftime('%Y-%m')
    _update_layer_outcome(sim_id, layer_number, reporting_month)
    db.session.commit()

    # Notify user of income recorded (best-effort)
    try:
        from app.services.notification_service import send_notification as _sn
        _amt = f'${float(amount):,.2f}'.rstrip('0').rstrip('.')
        _sn(
            user_id=current_user.id,
            notification_type='income',
            title=f'+ {_amt} confirmed — Layer {layer_number}',
            body=(
                f'{record.source} income captured: {_amt} '
                f'({record.income_type if hasattr(record, "income_type") else record.source}).'
            ),
            cta_url=f'/simulations/{sim_id}/income',
            cta_label='View income →',
            simulation_id=sim_id,
        )
    except Exception as _ne:
        logger.warning('Income notification failed: %s', _ne)

    return jsonify(record.to_dict()), 201


# ── Void a record ─────────────────────────────────────────────────────────────

@income_bp.route('/<sim_id>/income/<record_id>', methods=['DELETE'])
@login_required
def void_income(sim_id: str, record_id: str):
    sim, err, code = _check_sim(sim_id)
    if err:
        return err, code

    record = LayerIncomeRecord.query.filter_by(
        id=record_id, simulation_id=sim_id
    ).first_or_404()

    if record.is_void:
        return jsonify({'error': 'Record already voided'}), 409

    record.is_void = True
    record.voided_by_id = current_user.id
    reporting_month = record.income_date.strftime('%Y-%m')
    _update_layer_outcome(sim_id, record.layer_number, reporting_month)
    db.session.commit()

    return jsonify({'ok': True})


# ── Summary by layer ──────────────────────────────────────────────────────────

@income_bp.route('/<sim_id>/income/summary', methods=['GET'])
@login_required
def income_summary(sim_id: str):
    sim, err, code = _check_sim(sim_id)
    if err:
        return err, code

    rows = db.session.query(
        LayerIncomeRecord.layer_number,
        func.sum(LayerIncomeRecord.amount).label('total'),
        func.count(LayerIncomeRecord.id).label('count'),
        func.max(LayerIncomeRecord.income_date).label('last_date'),
    ).filter_by(
        simulation_id=sim_id,
        is_void=False,
    ).group_by(LayerIncomeRecord.layer_number).all()

    by_layer: dict[str, dict] = {}
    grand_total = Decimal('0')
    for r in rows:
        t = float(r.total or 0)
        grand_total += Decimal(str(t))
        by_layer[str(r.layer_number)] = {
            'layer_number': r.layer_number,
            'total':        t,
            'count':        r.count,
            'last_date':    r.last_date.isoformat() if r.last_date else None,
        }

    return jsonify({
        'by_layer':    by_layer,
        'grand_total': float(grand_total),
    })
