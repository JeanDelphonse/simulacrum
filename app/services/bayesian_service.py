"""
Bayesian Signal Engine — Beta-Binomial posterior updates (SIM-PRD-INTEG-001 Section 10).

Every integration webhook calls update_posterior() to shift the orchestrator's
beliefs about which action types produce results for this simulation.

Each posterior is a Beta distribution tracked as pseudo-counts:
    evidence = weight * signal_value          (0..1 pseudo-observations)
    direction '+' → alpha += evidence         (successes)
    direction '-' → beta  += evidence         (failures)
    value = alpha / (alpha + beta)            (posterior mean, always in (0, 1))

Starting prior is Beta(2, 2) — mean 0.5, matching the orchestrator's yield
prior in layer6.py. Unlike the previous occurrence-count EMA, negative
evidence (bounces, declines, churn) pulls the posterior down and repeated
positive events face diminishing returns instead of drifting toward 1.

Posterior keys use the pattern: '<metric_name>:<action_type_or_qualifier>'
e.g. 'reply_rate:cold_email_campaign', 'booking_rate:discovery_call'
"""
from __future__ import annotations
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Default starting value for any new posterior (neutral prior)
_DEFAULT_PRIOR = 0.5

# Prior pseudo-counts — Beta(2, 2), mean 0.5, strength 4.
PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0


def update_posterior(
    simulation_id: str,
    posterior_key: str,
    signal_value: float,
    weight: float,
    direction: str,
) -> float:
    """
    Apply one Beta-Binomial update to a posterior and persist it.

    Returns the new posterior value (mean of the Beta distribution).
    signal_value should be in [0, 1] — magnitude of the observation.
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

    # Clamp inputs to [0, 1]
    signal_value = max(0.0, min(1.0, float(signal_value)))
    weight       = max(0.0, min(1.0, float(weight)))
    evidence     = weight * signal_value

    if record is None:
        current = _DEFAULT_PRIOR
        alpha, beta = PRIOR_ALPHA, PRIOR_BETA
    else:
        current = float(record.value)
        if record.alpha_count is not None and record.beta_count is not None:
            alpha, beta = float(record.alpha_count), float(record.beta_count)
        else:
            # Legacy row without counts (pre-migration or seeded prior):
            # treat its stored value as a prior of the same total strength.
            strength = PRIOR_ALPHA + PRIOR_BETA
            alpha = current * strength
            beta  = (1.0 - current) * strength

    if direction == '+':
        alpha += evidence
    else:
        beta += evidence

    new_value = alpha / (alpha + beta)

    if record is None:
        from utils.id_gen import generate_id
        record = BayesianPosterior(
            id=generate_id(),
            simulation_id=simulation_id,
            posterior_key=posterior_key,
            value=new_value,
            alpha_count=alpha,
            beta_count=beta,
            last_direction=direction,
            last_weight=round(weight, 3),
            update_count=1,
        )
        db.session.add(record)
    else:
        record.value          = new_value
        record.alpha_count    = alpha
        record.beta_count     = beta
        record.last_direction = direction
        record.last_weight    = round(weight, 3)
        record.update_count   = (record.update_count or 0) + 1
        record.updated_at     = datetime.utcnow()

    logger.debug(
        'Bayesian update: sim=%s key=%s %.4f→%.4f (w=%.2f %s a=%.2f b=%.2f)',
        simulation_id, posterior_key, current, new_value, weight, direction, alpha, beta,
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
    from app.extensions import db
    try:
        update_posterior(simulation_id, posterior_key, signal_value, weight, direction)
        db.session.flush()
    except Exception as exc:
        logger.warning('Bayesian signal dispatch failed: key=%s err=%s', posterior_key, exc)
        # Roll back so a failed flush (e.g. concurrent-insert unique violation)
        # doesn't poison the session for the caller's subsequent commits.
        try:
            db.session.rollback()
        except Exception:
            pass
