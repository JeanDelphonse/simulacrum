"""
Bayesian Signal Engine — EMA posterior updates (SIM-PRD-INTEG-001 Section 10).

Every integration webhook calls update_posterior() to shift the orchestrator's
beliefs about which action types produce results for this simulation.

Formula (Section 10.1):
    alpha = weight * 0.3
    if direction == '+': new = alpha * signal_value + (1 - alpha) * current
    else:                new = alpha * (1 - signal_value) + (1 - alpha) * current

Posterior keys use the pattern: '<metric_name>:<action_type_or_qualifier>'
e.g. 'reply_rate:cold_email_campaign', 'booking_rate:discovery_call'
"""
from __future__ import annotations
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Default starting value for any new posterior (neutral prior)
_DEFAULT_PRIOR = 0.5


def update_posterior(
    simulation_id: str,
    posterior_key: str,
    signal_value: float,
    weight: float,
    direction: str,
) -> float:
    """
    Apply one Bayesian EMA update to a posterior and persist it.

    Returns the new posterior value.
    signal_value should be in [0, 1] — a rate or proportion.
    weight is per the PRD taxonomy (e.g. 0.5 for email_replied).
    direction is '+' (positive signal) or '-' (negative signal).
    """
    from app.models.bayesian import BayesianPosterior
    from app.extensions import db

    posterior_key = posterior_key[:200]

    record = BayesianPosterior.query.filter_by(
        simulation_id=simulation_id,
        posterior_key=posterior_key,
    ).first()

    if record is None:
        current = _DEFAULT_PRIOR
    else:
        current = float(record.value)

    # Clamp signal_value to [0, 1]
    signal_value = max(0.0, min(1.0, float(signal_value)))
    weight       = max(0.0, min(1.0, float(weight)))

    alpha = weight * 0.3

    if direction == '+':
        new_value = alpha * signal_value + (1.0 - alpha) * current
    else:
        new_value = alpha * (1.0 - signal_value) + (1.0 - alpha) * current

    new_value = max(0.0, min(1.0, new_value))

    if record is None:
        from utils.id_gen import generate_id
        record = BayesianPosterior(
            id=generate_id(),
            simulation_id=simulation_id,
            posterior_key=posterior_key,
            value=new_value,
            last_direction=direction,
            last_weight=round(weight, 3),
            update_count=1,
        )
        db.session.add(record)
    else:
        record.value          = new_value
        record.last_direction = direction
        record.last_weight    = round(weight, 3)
        record.update_count   = (record.update_count or 0) + 1
        record.updated_at     = datetime.utcnow()

    logger.debug(
        'Bayesian update: sim=%s key=%s %.4f→%.4f (w=%.2f %s)',
        simulation_id, posterior_key, current, new_value, weight, direction,
    )
    return new_value


def get_posterior(simulation_id: str, posterior_key: str) -> float:
    """Return current posterior value or _DEFAULT_PRIOR if not yet set."""
    from app.models.bayesian import BayesianPosterior
    record = BayesianPosterior.query.filter_by(
        simulation_id=simulation_id,
        posterior_key=posterior_key,
    ).first()
    return float(record.value) if record else _DEFAULT_PRIOR


def dispatch_signal(simulation_id: str | None, posterior_key: str,
                    signal_value: float, weight: float, direction: str) -> None:
    """
    Convenience wrapper: update_posterior with null-safety and session commit.
    Used from webhook handlers where we always want best-effort persistence.
    """
    if not simulation_id:
        return
    try:
        from app.extensions import db
        update_posterior(simulation_id, posterior_key, signal_value, weight, direction)
        db.session.flush()
    except Exception as exc:
        logger.warning('Bayesian signal dispatch failed: key=%s err=%s', posterior_key, exc)
