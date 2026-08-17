"""SIM-PRD-CAL-001 §9 — the Calibration Engine.

Combines an agent's raw estimate (the prior) with a matched reference
distribution (the likelihood) into a precision-weighted posterior, reported as
low / mid / high at configured percentiles with a confidence tier and a citation.

The engine's job is as much to *refuse* as to compute. Three guards exist purely
so a thin or mismatched source can never masquerade as precision:

  thin-data guard   below min_sample_moderate the reference is not allowed to
                    move the estimate at all; the raw value is returned with a
                    widened band and a Directional tier (§9, §12.6).
  credibility cap   a tier can never exceed what the dataset's own credibility
                    tier allows — Tier C never renders High (§8).
  proxy demotion    if the cohort had to widen to a different occupation's data,
                    the tier drops a step even when the sample is dense.

Naming note: cal_service.py in this package is the Cal.com integration. This
module is the Calibration Layer and shares nothing with it.
"""
from __future__ import annotations

import logging
import math
import time
from statistics import NormalDist

logger = logging.getLogger(__name__)

from app.models.calibration import (
    TIER_HIGH, TIER_MODERATE, TIER_DIRECTIONAL, TIER_ORDER,
)

_ND = NormalDist()

# Reference rows always carry p10/p50/p90, so the implied σ of the reference
# distribution is its 10-90 span divided by the corresponding z-span.
_Z10, _Z90 = _ND.inv_cdf(0.10), _ND.inv_cdf(0.90)
_Z_SPAN_10_90 = _Z90 - _Z10          # ≈ 2.5631

# Used only when a reference row is missing its p10/p90 (a p50-only source).
# Deliberately generous: an unknown spread should not read as a tight one.
DEFAULT_REF_SPREAD_PCT = 40.0

# Thin-data guard: how much wider than the model's own spread the returned band
# is drawn. Not a threshold users tune per agent — it is the fixed visual cost of
# admitting we don't have the data, so it lives here rather than in the config
# table alongside the genuine per-agent knobs.
THIN_DATA_WIDEN = 1.6

# Absolute ceiling on half-width as a % of the midpoint. Without it a
# near-zero sample can produce a range so wide it stops being information.
MAX_BAND_PCT = 75.0

# Reference lookups are memoized per (dataset, requested cohort_hash): the cache
# remembers which rung of the specificity ladder won, so a repeat lookup costs one
# indexed fetch instead of walking up to five. Reference data changes on a cadence
# of months (§8), so a short in-process TTL is well inside tolerance.
# invalidate_cache() clears it synchronously for admin edits in the same worker;
# other workers age out within the TTL.
_CACHE_TTL_SECONDS = 900
_ref_cache: dict = {}


def invalidate_cache() -> None:
    """Drop memoized reference lookups. Called on dataset write/refresh (R5)."""
    _ref_cache.clear()


# ── Settings ─────────────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Master switch (PRD §12.8). Defaults to on if the setting is absent."""
    try:
        from app.models.platform_settings import PlatformSetting
        return (PlatformSetting.get('calibration_enabled', 'true') or 'true').lower() == 'true'
    except Exception:
        return True


def outcomes_open() -> bool:
    """Whether the outcome-reporting widget accepts submissions (§10)."""
    try:
        from app.models.platform_settings import PlatformSetting
        return (PlatformSetting.get('calibration_outcomes_open', 'true') or 'true').lower() == 'true'
    except Exception:
        return True


def get_configs_for_agent(agent_key: str) -> list:
    """Enabled calibration configs for one agent, alias-resolved."""
    from app.models.calibration import CalibrationConfig
    from app.services.agent_registry import resolve_alias
    try:
        canonical = resolve_alias(agent_key)
    except Exception:
        canonical = agent_key
    return CalibrationConfig.query.filter_by(
        agent_key=canonical, is_enabled=True,
    ).all()


def agent_has_calibration(agent_key: str) -> bool:
    """True when this agent has at least one enabled, dataset-bound config."""
    if not is_enabled():
        return False
    try:
        return any(c.dataset_id for c in get_configs_for_agent(agent_key))
    except Exception:
        return False


# ── The METRICS block: getting a number out of a markdown artifact ───────────
# Agents in this codebase emit free-form markdown, so there is no structured
# numeric field for the engine to read. Rather than force 49 agents onto a JSON
# schema, calibrated agents are asked to append a trailing machine-readable block
# — the same convention already used for <!--CONTACTS ... CONTACTS-->. One API
# call, no added cost, and the artifact the user reads stays untouched markdown.

_UNIT_GUIDANCE = {
    'usd': 'a plain US dollar amount, digits only (e.g. 409104)',
    'usd_hour': 'a US dollar hourly rate, digits only (e.g. 185)',
    'usd_year': 'a US dollar annual amount, digits only (e.g. 142000)',
    'pct': 'a percentage as a number out of 100, digits only (e.g. 2.4 for 2.4%)',
    'ratio': 'a plain multiplier, digits only (e.g. 1.8)',
}


def metrics_prompt_block(agent_key: str) -> str:
    """The instruction appended to a calibrated agent's prompt, or ''.

    Built from the live CalibrationConfig rows, so binding a new field in the
    admin console changes what agents are asked for without a code change.
    """
    if not is_enabled():
        return ''
    try:
        configs = [c for c in get_configs_for_agent(agent_key) if c.dataset_id]
    except Exception as exc:
        logger.warning('Could not build METRICS prompt for %s: %s', agent_key, exc)
        return ''
    if not configs:
        return ''

    lines = []
    example = {}
    for c in configs:
        lines.append('- "{}": {} — {}'.format(
            c.output_field, c.field_label,
            _UNIT_GUIDANCE.get(c.unit, 'a plain number, digits only'),
        ))
        example[c.output_field] = 0
    import json as _json

    return (
        '\n\n---\nIMPORTANT: After your main response, append this exact block '
        '(mandatory output):\n'
        '<!--METRICS\n'
        + _json.dumps(example)
        + '\nMETRICS-->\n'
        'Replace each 0 with your single best point estimate for that field:\n'
        + '\n'.join(lines)
        + '\nGive one number per field — no ranges, no currency symbols, no '
        'thousands separators, no commentary. These estimates are compared '
        'against real market data and shown to the user as a calibrated range, '
        'so estimate honestly rather than optimistically. If you genuinely '
        'cannot estimate a field, omit that key entirely rather than guessing.'
    )


def extract_metrics_block(artifact: str):
    """Split a trailing METRICS block off an artifact.

    Returns (clean_artifact, metrics_dict). A missing or malformed block is not
    an error — it just means nothing calibrates for this artifact, and the user
    still gets their full artifact. There is deliberately no LLM retry here: an
    uncalibrated field is a visible, honest outcome, whereas a second inference
    call to recover a nice-to-have number is not worth the latency.
    """
    import re as _re
    import json as _json

    if not artifact:
        return artifact, {}

    match = _re.search(r'<!--METRICS\s*(.*?)\s*METRICS-->', artifact, _re.DOTALL)
    if not match:
        return artifact, {}

    clean = (artifact[:match.start()].rstrip() + artifact[match.end():]).strip()

    try:
        data = _json.loads(match.group(1).strip())
    except Exception as exc:
        logger.warning('METRICS block is not valid JSON — skipping calibration: %s', exc)
        return clean, {}

    if not isinstance(data, dict):
        logger.warning('METRICS block parsed to %s, expected an object', type(data).__name__)
        return clean, {}

    metrics = {}
    for key, value in data.items():
        number = coerce_number(value)
        # A model that couldn't estimate a field sometimes emits the 0 from the
        # template rather than omitting the key. Treat 0 as "no estimate" — a
        # calibrated range around zero is meaningless for every unit we support.
        if number is not None and number != 0:
            metrics[str(key)[:60]] = number
    return clean, metrics


# ── Reference lookup ─────────────────────────────────────────────────────────

def lookup_reference(dataset_id: str, cohort: dict):
    """Walk the cohort specificity ladder and return the first matching row.

    Returns (ReferenceDataPoint, match_level) or (None, 'none'). Each rung is a
    single indexed equality probe on cohort_hash, so a miss costs a handful of
    cheap queries rather than a JSON scan.
    """
    from app.models.calibration import ReferenceDataPoint
    from app.services.calibration_cohort import candidate_ladder, cohort_hash

    cache_key = (dataset_id, cohort_hash(cohort))
    hit = _ref_cache.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL_SECONDS:
        point_id, level = hit[1], hit[2]
        if point_id is None:
            return None, 'none'
        point = ReferenceDataPoint.query.get(point_id)
        if point:
            return point, level

    for candidate, level in candidate_ladder(cohort):
        point = ReferenceDataPoint.query.filter_by(
            dataset_id=dataset_id, cohort_hash=cohort_hash(candidate),
        ).first()
        if point:
            _ref_cache[cache_key] = (time.time(), point.id, level)
            return point, level

    _ref_cache[cache_key] = (time.time(), None, 'none')
    return None, 'none'


# ── Tiering ──────────────────────────────────────────────────────────────────

def _demote(tier: str, steps: int = 1) -> str:
    """Lower a tier by `steps`, floored at Directional."""
    idx = TIER_ORDER.index(tier) if tier in TIER_ORDER else 0
    return TIER_ORDER[max(0, idx - steps)]

def _cap(tier: str, ceiling: str) -> str:
    """Lower a tier to a ceiling. Never raises a tier."""
    if tier not in TIER_ORDER or ceiling not in TIER_ORDER:
        return tier
    return tier if TIER_ORDER.index(tier) <= TIER_ORDER.index(ceiling) else ceiling


def derive_tier(sample_size: int, config, dataset, match_level: str,
                thin_data: bool) -> str:
    """Confidence tier from data density, dataset credibility and match quality."""
    if thin_data:
        return TIER_DIRECTIONAL

    if sample_size >= (config.min_sample_high or 300):
        tier = TIER_HIGH
    elif sample_size >= (config.min_sample_moderate or 40):
        tier = TIER_MODERATE
    else:
        tier = TIER_DIRECTIONAL

    # A proxy match means another occupation's distribution stood in for this
    # one. Dense data about the wrong population is not high confidence.
    if match_level == 'proxy':
        tier = _demote(tier)

    if dataset is not None:
        tier = _cap(tier, dataset.tier_cap)
    return tier


def tier_upgrade_hint(run) -> str:
    """What would raise this tier — the tappable chip's explanation (§12.4)."""
    from app.models.calibration import ReferenceDataset
    if run.confidence_tier == TIER_HIGH:
        return 'This is our strongest tier — dense data matched to your situation.'

    reasons = []
    if run.thin_data_guard:
        reasons.append(
            'we do not yet have enough data for your exact situation, so this '
            'is the model estimate with an honest wide band'
        )
    elif run.match_level == 'proxy':
        reasons.append('we had to use a broader occupation group as a stand-in')
    elif run.match_level == 'relaxed':
        reasons.append('we used national rather than local data for your area')

    dataset = ReferenceDataset.query.get(run.dataset_id) if run.dataset_id else None
    if dataset is not None:
        if dataset.needs_review:
            reasons.append('this dataset is pending verification')
        elif dataset.credibility_tier == ReferenceDataset.TIER_C:
            reasons.append('the underlying source is a modelled proxy rather than a survey')

    reasons.append(
        'reporting what actually happened to you later sharpens this range for '
        'people in your situation'
    )
    return 'Why not higher: ' + '; '.join(reasons) + '.'


# ── The posterior ────────────────────────────────────────────────────────────

def _clamp_unit(value: float, unit: str) -> float:
    """Keep a value inside what its unit can physically mean."""
    if unit == 'pct':
        return max(0.0, min(100.0, value))
    if unit == 'ratio':
        return max(0.0, value)
    return max(0.0, value)          # money is never negative here


def compute_posterior(raw_value: float, config, point, dataset,
                      match_level: str) -> dict:
    """Precision-weighted Bayesian blend of the agent estimate and reference data.

    Returns a dict of low/mid/high, the tier, and the intermediate quantities the
    methodology drawer needs. Pure — no DB writes, no session use — so it is
    directly testable and safe to call from anywhere.
    """
    unit = config.unit or 'usd'
    p_low, p_mid, p_high = config.percentiles
    z_low = _ND.inv_cdf(p_low / 100.0)
    z_mid = _ND.inv_cdf(p_mid / 100.0)
    z_high = _ND.inv_cdf(p_high / 100.0)

    raw = abs(float(raw_value))
    sigma_model = max((float(config.sigma_model_pct) / 100.0) * raw, 1e-9)

    n = int(point.sample_size or 0) if point is not None else 0
    min_moderate = int(config.min_sample_moderate or 40)
    min_high = int(config.min_sample_high or 300)

    # ── Thin-data guard (§9) ────────────────────────────────────────────────
    # No match, or a sample too small to trust: the reference gets no vote. We
    # return the model's own estimate, widened, and say so. An honest wide range
    # beats a falsely precise narrow one.
    thin_data = point is None or n < min_moderate
    if thin_data:
        mid = raw
        sigma_post = sigma_model * THIN_DATA_WIDEN
        ref_p50 = float(point.p50) if point is not None else None
    else:
        p10 = float(point.p10) if point.p10 is not None else None
        p90 = float(point.p90) if point.p90 is not None else None
        ref_p50 = float(point.p50)

        if p10 is not None and p90 is not None and p90 > p10:
            sigma_ref = (p90 - p10) / _Z_SPAN_10_90
        else:
            sigma_ref = (DEFAULT_REF_SPREAD_PCT / 100.0) * max(ref_p50, 1e-9)
        sigma_ref = max(sigma_ref, 1e-9)

        # Sample-size inflation. At n == min_sample_high the reference σ is
        # widened by √2; as n grows the factor decays to 1; as n shrinks toward
        # the guard threshold it grows, so a sparse source both moves the
        # estimate less and produces a wider band. One formula covers both
        # halves of "thin data → wide band".
        sigma_ref *= math.sqrt(1.0 + (min_high / max(n, 1)))

        tau_model = 1.0 / (sigma_model ** 2)
        tau_ref = 1.0 / (sigma_ref ** 2)
        mid = (tau_model * raw + tau_ref * ref_p50) / (tau_model + tau_ref)
        sigma_post = math.sqrt(1.0 / (tau_model + tau_ref))

    low = mid + z_low * sigma_post
    high = mid + z_high * sigma_post
    if p_mid != 50.0:
        mid = mid + z_mid * sigma_post

    # ── Band floor: never render false precision (§12.3) ────────────────────
    half = (high - low) / 2.0
    floor_half = (float(config.band_floor_pct) / 100.0) * abs(mid)
    if half < floor_half:
        low, high = mid - floor_half, mid + floor_half
        half = floor_half

    # ── Band ceiling: a range this wide would stop being information ────────
    ceil_half = (MAX_BAND_PCT / 100.0) * abs(mid)
    if ceil_half > 0 and half > ceil_half:
        low, high = mid - ceil_half, mid + ceil_half

    low = _clamp_unit(low, unit)
    high = _clamp_unit(high, unit)
    mid = _clamp_unit(mid, unit)
    if low > high:
        low, high = high, low
    mid = min(max(mid, low), high)

    tier = derive_tier(n, config, dataset, match_level, thin_data)

    return {
        'raw': float(raw_value),
        'low': low,
        'mid': mid,
        'high': high,
        'tier': tier,
        'thin_data_guard': thin_data,
        'match_level': match_level,
        'ref_p50': ref_p50,
        'ref_sample_size': n,
        'sigma_model': sigma_model,
        'sigma_post': sigma_post,
        'rationale': _rationale(float(raw_value), mid, thin_data, match_level, n),
    }


def _rationale(raw: float, mid: float, thin_data: bool,
               match_level: str, n: int) -> str:
    """One line for the drawer: how far calibration moved the estimate, and why."""
    if thin_data:
        return (
            'Reference data for this cohort was too thin to anchor against '
            '({} matching observations), so the model estimate is shown with a '
            'widened band rather than falsely narrowed.'.format(n)
        )
    if raw <= 0:
        return 'Calibrated against the reference distribution for this cohort.'

    delta_pct = (mid - raw) / abs(raw) * 100.0
    if abs(delta_pct) < 1.0:
        movement = 'confirmed the model estimate (moved less than 1%)'
    else:
        movement = 'pulled the model estimate {} ~{:.0f}%'.format(
            'up' if delta_pct > 0 else 'down', abs(delta_pct),
        )
    suffix = {
        'proxy': ' Matched on a broader occupation group, so treat the anchor as indicative.',
        'relaxed': ' Matched on national rather than local data.',
    }.get(match_level, '')
    return 'Calibration {} toward market data (n = {:,}).{}'.format(movement, n, suffix)


# ── Persisting a calibrated field ────────────────────────────────────────────

def calibrate_value(config, raw_value: float, cohort: dict) -> dict | None:
    """Calibrate one numeric value. Returns the posterior dict, or None if the
    config has no dataset bound (in which case the field renders raw)."""
    from app.models.calibration import ReferenceDataset

    if not config.dataset_id:
        return None
    dataset = ReferenceDataset.query.get(config.dataset_id)
    if dataset is None or not dataset.is_active:
        return None

    # Unit guard. A cohort row is found by occupation, not by what the field means,
    # so a config bound to the wrong dataset would happily anchor a speaking fee to
    # annual wage data and render it as High confidence. Disagreeing units are the
    # cheapest reliable signal of that mis-binding, and R1 says mismatched data is
    # worse than none — so refuse, and let the field render raw.
    if config.unit != dataset.unit:
        logger.warning(
            'Calibration skipped for %s.%s: config unit %r does not match dataset '
            '%s unit %r — rebind the field or fix the dataset unit.',
            config.agent_key, config.output_field, config.unit,
            dataset.name, dataset.unit,
        )
        return None

    point, match_level = lookup_reference(config.dataset_id, cohort)
    result = compute_posterior(raw_value, config, point, dataset, match_level)
    result['dataset'] = dataset
    result['point'] = point
    return result


def record_run(config, result: dict, cohort: dict, simulation_id: str,
               action_id: str = None, version_number: int = None):
    """Persist a CalibrationRun. Caller owns the commit."""
    from app.extensions import db
    from app.models.calibration import CalibrationRun
    from app.services.calibration_cohort import cohort_hash
    from utils.id_gen import generate_id

    point = result.get('point')

    # Record the cohort that was actually *matched*, not the one requested. They
    # differ whenever the ladder relaxed a dimension, and the matched cohort is
    # the one that matters downstream: the methodology drawer should name the data
    # we really used, and the drift job has to find the same reference row again
    # to compare reported outcomes against. Falls back to the requested cohort
    # when nothing matched, so the row is never left without one.
    matched_cohort = dict(point.cohort_json) if (point is not None and point.cohort_json) \
        else dict(cohort)

    # Carry the human-readable occupation title across from the requested cohort so
    # the drawer can say "Management Analysts" rather than "13-1111" — but only when
    # the occupation actually matched. On a proxy match the reference row is a
    # different (broader) occupation, and labelling it with the user's job title
    # would misrepresent whose data we used.
    if cohort.get('soc_title') and matched_cohort.get('soc_group') == cohort.get('soc_group'):
        matched_cohort['soc_title'] = cohort['soc_title']

    run = CalibrationRun(
        id=generate_id(),
        simulation_id=simulation_id,
        action_id=action_id,
        version_number=version_number,
        agent_key=config.agent_key,
        output_field=config.output_field,
        field_label=config.field_label,
        unit=config.unit,
        raw_value=result['raw'],
        cal_low=result['low'],
        cal_mid=result['mid'],
        cal_high=result['high'],
        confidence_tier=result['tier'],
        dataset_id=config.dataset_id,
        method=config.method,
        cohort_json=matched_cohort,
        cohort_hash=(point.cohort_hash if point is not None
                     else cohort_hash(matched_cohort)),
        ref_p50=result.get('ref_p50'),
        ref_sample_size=result.get('ref_sample_size'),
        ref_as_of=point.as_of_date if point is not None else None,
        match_level=result.get('match_level'),
        thin_data_guard=bool(result.get('thin_data_guard')),
        rationale=(result.get('rationale') or '')[:400],
    )
    db.session.add(run)
    return run


def calibrate_metrics(metrics: dict, agent_key: str, simulation_id: str,
                      action_id: str = None, version_number: int = None) -> list:
    """Calibrate every configured field an agent emitted, persisting one run each.

    `metrics` maps output_field → numeric value, as parsed from the artifact's
    METRICS block. Fields with no enabled config are ignored; a field whose
    config has no dataset bound is skipped (it renders raw). Never raises — a
    calibration failure must never cost the user their artifact.
    """
    from app.extensions import db
    from app.services.calibration_cohort import resolve_cohort

    if not metrics or not is_enabled():
        return []

    configs = [c for c in get_configs_for_agent(agent_key) if c.output_field in metrics]
    if not configs:
        return []

    cohort = resolve_cohort(simulation_id)
    runs = []
    for config in configs:
        try:
            raw = coerce_number(metrics.get(config.output_field))
            # A non-positive estimate is meaningless for every unit the layer
            # supports, and a band drawn around zero or a negative would render
            # nonsense. Skip rather than calibrate it.
            if raw is None or raw <= 0:
                continue
            result = calibrate_value(config, raw, cohort)
            if result is None:
                continue
            runs.append(record_run(
                config, result, cohort, simulation_id, action_id, version_number,
            ))
        except Exception as exc:
            logger.warning(
                'Calibration failed for %s.%s (sim=%s): %s',
                agent_key, config.output_field, simulation_id, exc,
            )

    if runs:
        try:
            db.session.commit()
            logger.info(
                'Calibrated %d field(s) for %s action=%s', len(runs), agent_key, action_id,
            )
        except Exception as exc:
            logger.error('Could not persist calibration runs for %s: %s', action_id, exc)
            try:
                db.session.rollback()
            except Exception:
                pass
            return []
    return runs


def stamp_version(action_id: str, version_number: int) -> None:
    """Attach a version number to runs created during this execution.

    Calibration happens inside execute_agent_action, which runs before the
    caller knows which ArtifactVersion it will become. Both version-creating
    paths (tasks/agent.py and services/layer6.py) call this immediately after
    they commit the version, so the run is traceable to the exact artifact
    revision the user saw. Best-effort: an unstamped run is still correct, just
    less precisely attributed.
    """
    if not action_id or version_number is None:
        return
    from app.extensions import db
    from app.models.calibration import CalibrationRun
    try:
        updated = CalibrationRun.query.filter_by(
            action_id=action_id, version_number=None,
        ).update({'version_number': version_number})
        if updated:
            db.session.commit()
    except Exception as exc:
        logger.debug('Could not stamp calibration runs for %s: %s', action_id, exc)
        try:
            db.session.rollback()
        except Exception:
            pass


def coerce_number(value):
    """Parse a metric value that may arrive as '$185/hr', '12.5%', or 185.

    Public because the outcome-report endpoint parses user-typed values with the
    same tolerance the METRICS parser applies to model-typed ones.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    cleaned = ''.join(ch for ch in text if ch.isdigit() or ch in '.-')
    # A stray trailing '-' or '.' (e.g. from a range like "150-200") would make
    # float() throw; take the leading number in that case.
    try:
        return float(cleaned)
    except ValueError:
        import re
        m = re.search(r'-?\d+(?:\.\d+)?', cleaned)
        return float(m.group()) if m else None


# ── Read paths for the UI ────────────────────────────────────────────────────

def _config_order() -> dict:
    """(agent_key, output_field) → display rank, from the configs themselves.

    Configs are created in priority order — the Rate Card's rate before the annual
    projection that depends on it (§11, scheduling) — so reusing that order gives
    the card a stable, meaningful sequence instead of whatever order the runs
    happen to come back in.

    Ordered by created_at, not by id: ids are random 9-char strings, so ordering by
    them would be stable but meaningless for anything an admin adds later.
    """
    from app.models.calibration import CalibrationConfig
    rows = CalibrationConfig.query.order_by(
        CalibrationConfig.layer, CalibrationConfig.created_at, CalibrationConfig.id,
    ).all()
    return {(c.agent_key, c.output_field): i for i, c in enumerate(rows)}


def _latest_per_field(rows, key) -> list:
    """Collapse runs to the newest per key, then order them for display.

    An artifact re-run creates fresh runs for the same fields; the card must show
    only the current ones, so superseded runs are dropped here rather than
    filtered in the template.
    """
    latest = {}
    for r in rows:
        latest.setdefault(key(r), r)
    order = _config_order()
    return sorted(
        latest.values(),
        key=lambda r: (order.get((r.agent_key, r.output_field), 10 ** 6),
                       r.output_field),
    )


def runs_for_action(action_id: str) -> list:
    """Current calibration run per field for one artifact, in config order."""
    from app.models.calibration import CalibrationRun
    rows = CalibrationRun.query.filter_by(action_id=action_id).order_by(
        CalibrationRun.created_at.desc(), CalibrationRun.id.desc(),
    ).all()
    return _latest_per_field(rows, lambda r: r.output_field)


def runs_for_simulation(simulation_id: str) -> list:
    """Current calibration run per (agent, field) across a whole simulation."""
    from app.models.calibration import CalibrationRun
    rows = CalibrationRun.query.filter_by(simulation_id=simulation_id).order_by(
        CalibrationRun.created_at.desc(), CalibrationRun.id.desc(),
    ).all()
    return _latest_per_field(rows, lambda r: (r.agent_key, r.output_field))


def format_value(value: float, unit: str) -> str:
    """Compact display form used in the headline range: $372k, 12.5%, 1.8x."""
    if value is None:
        return '—'
    if unit == 'pct':
        return '{:.1f}%'.format(value)
    if unit == 'ratio':
        return '{:.2f}x'.format(value)

    suffix = '/hr' if unit == 'usd_hour' else ''
    magnitude = abs(value)
    if magnitude >= 1_000_000:
        return '${:.1f}M{}'.format(value / 1_000_000, suffix)
    if magnitude >= 10_000:
        return '${:,.0f}k{}'.format(value / 1_000, suffix)
    if magnitude >= 1_000 and unit != 'usd_hour':
        return '${:,.1f}k{}'.format(value / 1_000, suffix)
    return '${:,.0f}{}'.format(value, suffix)
