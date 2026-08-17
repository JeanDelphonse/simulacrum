"""SIM-PRD-CAL-001 — The Calibration Layer models.

The layer that sits between an agent's raw numeric estimate and what the user
sees: a reference-anchored range with a confidence tier and a cited source.

Ownership of the numbers is split deliberately:
  reference_datasets / reference_data_points  — external ground truth (§8)
  calibration_configs                         — every threshold, admin-editable (§7)
  calibration_runs                            — what we showed a given user, kept
                                                so the methodology drawer can be
                                                re-rendered without recomputing
  outcome_reports / calibration_audit          — the flywheel and its audit trail (§10)
"""
from __future__ import annotations

from datetime import datetime

from app.extensions import db
from utils.id_gen import generate_id


# ── Confidence tiers ─────────────────────────────────────────────────────────
TIER_HIGH = 'high'
TIER_MODERATE = 'moderate'
TIER_DIRECTIONAL = 'directional'

# Ordered weakest → strongest. Used by the credibility cap, which can only ever
# lower a tier (PRD §8: "Tier-C data can never render as High confidence").
TIER_ORDER = (TIER_DIRECTIONAL, TIER_MODERATE, TIER_HIGH)

TIER_LABELS = {
    TIER_HIGH: 'High confidence',
    TIER_MODERATE: 'Moderate confidence',
    TIER_DIRECTIONAL: 'Directional only',
}

TIER_BLURBS = {
    TIER_HIGH: 'dense matching data',
    TIER_MODERATE: 'partial matching data',
    TIER_DIRECTIONAL: 'treat as a signal, not a forecast',
}


class ReferenceDataset(db.Model):
    """An external ground-truth source scoped to one layer (the likelihood)."""

    __tablename__ = 'reference_datasets'

    TIER_A = 'A'
    TIER_B = 'B'
    TIER_C = 'C'

    # Highest confidence tier each credibility tier is allowed to produce.
    # Tier C is a modeled proxy or thin commercial source: useful, never "High".
    CREDIBILITY_CAP = {
        TIER_A: TIER_HIGH,
        TIER_B: TIER_HIGH,
        TIER_C: TIER_MODERATE,
    }

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    layer = db.Column(db.String(2), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    source = db.Column(db.String(200), nullable=False)
    source_url = db.Column(db.String(500), nullable=True)
    unit = db.Column(db.String(20), nullable=False, default='usd')
    geography_scope = db.Column(db.String(40), nullable=False, default='national')
    credibility_tier = db.Column(db.String(1), nullable=False, default=TIER_C)
    needs_review = db.Column(db.Boolean, nullable=False, default=True)
    derivation_note = db.Column(db.Text, nullable=True)
    as_of_label = db.Column(db.String(40), nullable=True)
    last_refreshed = db.Column(db.Date, nullable=True)
    refresh_cadence_days = db.Column(db.Integer, nullable=False, default=365)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    @property
    def tier_cap(self) -> str:
        """Ceiling this dataset's credibility places on any tier derived from it.

        An unreviewed dataset is capped at Moderate regardless of credibility —
        a seeded or freshly uploaded extract has not been verified by a human, so
        it must not be able to render as High confidence on its own.
        """
        cap = self.CREDIBILITY_CAP.get(self.credibility_tier, TIER_MODERATE)
        if self.needs_review and cap == TIER_HIGH:
            return TIER_MODERATE
        return cap

    @property
    def is_stale(self) -> bool:
        """True once the dataset is past its own refresh cadence (PRD R5)."""
        if not self.last_refreshed:
            return True
        age_days = (datetime.utcnow().date() - self.last_refreshed).days
        return age_days > (self.refresh_cadence_days or 365)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'layer': self.layer,
            'name': self.name,
            'source': self.source,
            'source_url': self.source_url,
            'unit': self.unit,
            'geography_scope': self.geography_scope,
            'credibility_tier': self.credibility_tier,
            'needs_review': bool(self.needs_review),
            'derivation_note': self.derivation_note,
            'as_of_label': self.as_of_label,
            'tier_cap': self.tier_cap,
            'is_stale': self.is_stale,
            'last_refreshed': self.last_refreshed.isoformat() if self.last_refreshed else None,
            'refresh_cadence_days': self.refresh_cadence_days,
            'is_active': bool(self.is_active),
            'point_count': ReferenceDataPoint.query.filter_by(dataset_id=self.id).count(),
        }


class ReferenceDataPoint(db.Model):
    """One reference distribution for one cohort."""

    __tablename__ = 'reference_data_points'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    dataset_id = db.Column(db.String(9), nullable=False, index=True)
    cohort_json = db.Column(db.JSON, nullable=True)
    cohort_hash = db.Column(db.String(32), nullable=False, index=True)
    p10 = db.Column(db.Numeric(16, 4), nullable=True)
    p50 = db.Column(db.Numeric(16, 4), nullable=False)
    p90 = db.Column(db.Numeric(16, 4), nullable=True)
    mean = db.Column(db.Numeric(16, 4), nullable=True)
    sample_size = db.Column(db.Integer, nullable=False, default=0)
    as_of_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('dataset_id', 'cohort_hash', name='uq_rdp_dataset_cohort'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'dataset_id': self.dataset_id,
            'cohort': self.cohort_json or {},
            'cohort_hash': self.cohort_hash,
            'p10': float(self.p10) if self.p10 is not None else None,
            'p50': float(self.p50),
            'p90': float(self.p90) if self.p90 is not None else None,
            'mean': float(self.mean) if self.mean is not None else None,
            'sample_size': self.sample_size,
            'as_of_date': self.as_of_date.isoformat() if self.as_of_date else None,
        }


class CalibrationConfig(db.Model):
    """Per-agent, per-field calibration settings. Every threshold lives here."""

    __tablename__ = 'calibration_configs'

    METHOD_BAYES = 'bayes_precision'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    agent_key = db.Column(db.String(50), nullable=False)
    layer = db.Column(db.String(2), nullable=False)
    dataset_id = db.Column(db.String(9), nullable=True)
    output_field = db.Column(db.String(60), nullable=False)
    field_label = db.Column(db.String(120), nullable=False)
    unit = db.Column(db.String(20), nullable=False, default='usd')
    method = db.Column(db.String(30), nullable=False, default=METHOD_BAYES)
    percentiles_json = db.Column(db.JSON, nullable=True)
    sigma_model_pct = db.Column(db.Numeric(6, 2), nullable=False, default=25)
    min_sample_high = db.Column(db.Integer, nullable=False, default=300)
    min_sample_moderate = db.Column(db.Integer, nullable=False, default=40)
    band_floor_pct = db.Column(db.Numeric(6, 2), nullable=False, default=8)
    drift_threshold_pct = db.Column(db.Numeric(6, 2), nullable=False, default=15)
    min_reports_to_reweight = db.Column(db.Integer, nullable=False, default=30)
    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('agent_key', 'output_field', name='uq_cc_agent_field'),
    )

    @property
    def percentiles(self) -> list:
        """[low, mid, high] percentile triple, defaulting to p10/p50/p90."""
        p = self.percentiles_json
        if isinstance(p, list) and len(p) == 3:
            return [float(x) for x in p]
        return [10.0, 50.0, 90.0]

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'agent_key': self.agent_key,
            'layer': self.layer,
            'dataset_id': self.dataset_id,
            'output_field': self.output_field,
            'field_label': self.field_label,
            'unit': self.unit,
            'method': self.method,
            'percentiles': self.percentiles,
            'sigma_model_pct': float(self.sigma_model_pct),
            'min_sample_high': self.min_sample_high,
            'min_sample_moderate': self.min_sample_moderate,
            'band_floor_pct': float(self.band_floor_pct),
            'drift_threshold_pct': float(self.drift_threshold_pct),
            'min_reports_to_reweight': self.min_reports_to_reweight,
            'is_enabled': bool(self.is_enabled),
        }


class CalibrationRun(db.Model):
    """A calibrated field as it was shown to one user, at one point in time.

    Persisted rather than recomputed so the methodology drawer always reflects
    the numbers the user actually saw, even after the reference data refreshes.
    """

    __tablename__ = 'calibration_runs'

    MATCH_EXACT = 'exact'
    MATCH_RELAXED = 'relaxed'
    MATCH_PROXY = 'proxy'
    MATCH_NONE = 'none'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(db.String(9), nullable=False, index=True)
    action_id = db.Column(db.String(9), nullable=True, index=True)
    version_number = db.Column(db.Integer, nullable=True)
    agent_key = db.Column(db.String(50), nullable=False)
    output_field = db.Column(db.String(60), nullable=False)
    field_label = db.Column(db.String(120), nullable=False)
    unit = db.Column(db.String(20), nullable=False, default='usd')
    raw_value = db.Column(db.Numeric(16, 4), nullable=False)
    cal_low = db.Column(db.Numeric(16, 4), nullable=False)
    cal_mid = db.Column(db.Numeric(16, 4), nullable=False)
    cal_high = db.Column(db.Numeric(16, 4), nullable=False)
    confidence_tier = db.Column(db.String(12), nullable=False)
    dataset_id = db.Column(db.String(9), nullable=True)
    method = db.Column(db.String(30), nullable=False, default=CalibrationConfig.METHOD_BAYES)
    cohort_json = db.Column(db.JSON, nullable=True)
    cohort_hash = db.Column(db.String(32), nullable=True)
    ref_p50 = db.Column(db.Numeric(16, 4), nullable=True)
    ref_sample_size = db.Column(db.Integer, nullable=True)
    ref_as_of = db.Column(db.Date, nullable=True)
    match_level = db.Column(db.String(20), nullable=True)
    thin_data_guard = db.Column(db.Boolean, nullable=False, default=False)
    rationale = db.Column(db.String(400), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self, include_methodology: bool = True) -> dict:
        """Serialise for the calibrated result card.

        include_methodology=False omits the prior → data → posterior detail, for
        the compact summary rendering on the simulation detail page.
        """
        dataset = ReferenceDataset.query.get(self.dataset_id) if self.dataset_id else None
        out = {
            'run_id': self.id,
            'agent_key': self.agent_key,
            'output_field': self.output_field,
            'field_label': self.field_label,
            'unit': self.unit,
            'raw': float(self.raw_value),
            'low': float(self.cal_low),
            'mid': float(self.cal_mid),
            'high': float(self.cal_high),
            'tier': self.confidence_tier,
            'tier_label': TIER_LABELS.get(self.confidence_tier, self.confidence_tier),
            'tier_blurb': TIER_BLURBS.get(self.confidence_tier, ''),
            'thin_data_guard': bool(self.thin_data_guard),
            'match_level': self.match_level,
            'dataset': dataset.name if dataset else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if include_methodology:
            out.update({
                'source': dataset.source if dataset else None,
                'source_url': dataset.source_url if dataset else None,
                'dataset_id': self.dataset_id,
                'credibility_tier': dataset.credibility_tier if dataset else None,
                'needs_review': bool(dataset.needs_review) if dataset else None,
                'derivation_note': dataset.derivation_note if dataset else None,
                'as_of_label': dataset.as_of_label if dataset else None,
                'method': self.method,
                'cohort': self.cohort_json or {},
                'ref_p50': float(self.ref_p50) if self.ref_p50 is not None else None,
                'ref_sample_size': self.ref_sample_size,
                'ref_as_of': self.ref_as_of.isoformat() if self.ref_as_of else None,
                'rationale': self.rationale,
            })
        return out


class OutcomeReport(db.Model):
    """A real-world outcome a user reported back — the flywheel input (PRD §10).

    Private by default: an individual report is never shown to anyone but its
    author and admins, and only aggregated cohort distributions influence
    reference data.
    """

    __tablename__ = 'outcome_reports'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), nullable=False, index=True)
    simulation_id = db.Column(db.String(9), nullable=True)
    run_id = db.Column(db.String(9), nullable=True)
    agent_key = db.Column(db.String(50), nullable=False)
    output_field = db.Column(db.String(60), nullable=False)
    reported_value = db.Column(db.Numeric(16, 4), nullable=False)
    months_elapsed = db.Column(db.Integer, nullable=True)
    cohort_json = db.Column(db.JSON, nullable=True)
    cohort_hash = db.Column(db.String(32), nullable=True)
    is_verified = db.Column(db.Boolean, nullable=False, default=False)
    is_excluded = db.Column(db.Boolean, nullable=False, default=False)
    note = db.Column(db.String(500), nullable=True)
    reported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'run_id', name='uq_or_user_run'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'run_id': self.run_id,
            'agent_key': self.agent_key,
            'output_field': self.output_field,
            'reported_value': float(self.reported_value),
            'months_elapsed': self.months_elapsed,
            'is_verified': bool(self.is_verified),
            'is_excluded': bool(self.is_excluded),
            'note': self.note,
            'reported_at': self.reported_at.isoformat() if self.reported_at else None,
        }


class CalibrationAudit(db.Model):
    """Drift review trail. One row per cohort per nightly evaluation (PRD §10)."""

    __tablename__ = 'calibration_audit'

    ACTION_NONE = 'none'
    ACTION_FLAGGED = 'flagged'
    ACTION_REWEIGHTED = 'reweighted'
    ACTION_GATED = 'gated'      # drift exceeded, but the optimal-stopping gate held

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    dataset_id = db.Column(db.String(9), nullable=True, index=True)
    agent_key = db.Column(db.String(50), nullable=True)
    output_field = db.Column(db.String(60), nullable=True)
    cohort_hash = db.Column(db.String(32), nullable=True)
    cohort_json = db.Column(db.JSON, nullable=True)
    drift_pct = db.Column(db.Numeric(8, 2), nullable=True)
    n_reports = db.Column(db.Integer, nullable=False, default=0)
    ref_p50 = db.Column(db.Numeric(16, 4), nullable=True)
    reported_p50 = db.Column(db.Numeric(16, 4), nullable=True)
    action = db.Column(db.String(20), nullable=False, default=ACTION_NONE)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        dataset = ReferenceDataset.query.get(self.dataset_id) if self.dataset_id else None
        return {
            'id': self.id,
            'dataset_id': self.dataset_id,
            'dataset_name': dataset.name if dataset else None,
            'agent_key': self.agent_key,
            'output_field': self.output_field,
            'cohort': self.cohort_json or {},
            'cohort_hash': self.cohort_hash,
            'drift_pct': float(self.drift_pct) if self.drift_pct is not None else None,
            'n_reports': self.n_reports,
            'ref_p50': float(self.ref_p50) if self.ref_p50 is not None else None,
            'reported_p50': float(self.reported_p50) if self.reported_p50 is not None else None,
            'action': self.action,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
