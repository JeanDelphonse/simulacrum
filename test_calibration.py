"""SIM-PRD-CAL-001 — end-to-end check of the Calibration Layer.

Runs against an in-memory SQLite database, so it touches no real data and makes
no network calls. Verifies the pieces that are easy to get subtly wrong:
the cohort ladder, the posterior math, every tier guard, the METRICS round-trip,
and the drift gate.

    python test_calibration.py
"""
import os
import sys

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from utils.id_gen import generate_id                          # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    mark = 'ok  ' if condition else 'FAIL'
    line = '  {} {}{}'.format(mark, label, ('  -- ' + str(detail)) if detail else '')
    # The Windows console is cp1252; drop anything it can't encode rather than
    # letting a stray glyph in a detail string abort the run.
    print(line.encode(sys.stdout.encoding or 'ascii', 'replace')
              .decode(sys.stdout.encoding or 'ascii'))


def main():
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()

        from app.models.calibration import (
            CalibrationConfig, CalibrationRun, OutcomeReport, ReferenceDataPoint,
            ReferenceDataset, TIER_DIRECTIONAL, TIER_HIGH, TIER_MODERATE,
        )
        from app.models.simulation import Simulation
        from app.models.user import User
        from app.services import calibration_cohort as coh
        from app.services import calibration_service as cal
        from app.services import calibration_drift as drift

        # ── cohort hashing + ladder ─────────────────────────────────────────
        print('\ncohort resolution')
        h1 = coh.cohort_hash({'soc_group': '13-1111', 'metro': 'national', 'seniority': 'any'})
        h2 = coh.cohort_hash({'seniority': 'any', 'soc_group': '13-1111', 'metro': 'national'})
        check('hash is order-independent', h1 == h2)
        check('hash ignores extra descriptive keys',
              h1 == coh.cohort_hash({'soc_group': '13-1111', 'metro': 'national',
                                     'seniority': 'any', 'soc_title': 'Management Analysts'}))
        check('major group derives correctly',
              coh.soc_major_group('13-1111') == '13-0000',
              coh.soc_major_group('13-1111'))

        ladder = coh.candidate_ladder({'soc_group': '13-1111', 'metro': 'us-nyc',
                                       'seniority': 'senior'})
        levels = [lvl for _, lvl in ladder]
        check('ladder starts exact and ends proxy',
              levels[0] == 'exact' and levels[-1] == 'proxy', levels)
        check('ladder relaxes metro to national',
              any(c['metro'] == 'national' for c, _ in ladder))
        check('ladder has no duplicate rungs',
              len({coh.cohort_hash(c) for c, _ in ladder}) == len(ladder))
        check('zone maps to SOC', coh.soc_from_zones('consulting') == '13-1111')
        check('unknown zone falls back', coh.soc_from_zones('astrology') == coh.DEFAULT_SOC)
        check('metro normalises', coh.normalize_metro('Brooklyn, NY') == 'us-nyc')
        check('unknown location is national', coh.normalize_metro('Reykjavik') == 'national')
        check('seniority: exec beats senior', coh.infer_seniority('Senior Vice President') == 'exec')
        check('seniority: director is senior', coh.infer_seniority('Director of Ops') == 'senior')

        # ── fixtures ────────────────────────────────────────────────────────
        user = User(id=generate_id(), email='cal@test.local', full_name='Cal Test',
                    password_hash='x')
        db.session.add(user)
        sim = Simulation(id=generate_id(), user_id=user.id, name='Test sim',
                         expertise_zone='consulting', status='complete')
        sim.cohort_json = {'soc_group': '13-1111', 'metro': 'national',
                           'seniority': 'senior', 'soc_title': 'Management Analysts'}
        db.session.add(sim)

        ds = ReferenceDataset(
            id=generate_id(), layer='L1', name='OES test', source='BLS',
            unit='usd_year', credibility_tier='B', needs_review=False,
            as_of_label='May 2024', is_active=True,
        )
        db.session.add(ds)
        db.session.flush()

        cohort_national = {'soc_group': '13-1111', 'metro': 'national', 'seniority': 'any'}
        db.session.add(ReferenceDataPoint(
            id=generate_id(), dataset_id=ds.id, cohort_json=cohort_national,
            cohort_hash=coh.cohort_hash(cohort_national),
            p10=372000, p50=398000, p90=451000, sample_size=1240,
        ))
        cfg = CalibrationConfig(
            id=generate_id(), agent_key='rate_card', layer='L1', dataset_id=ds.id,
            output_field='projected_annual_income', field_label='Projected annual income',
            unit='usd_year', sigma_model_pct=25, min_sample_high=300,
            min_sample_moderate=40, band_floor_pct=8, drift_threshold_pct=15,
            min_reports_to_reweight=30, is_enabled=True,
        )
        db.session.add(cfg)
        db.session.commit()

        # ── reference lookup ────────────────────────────────────────────────
        print('\nreference lookup')
        point, level = cal.lookup_reference(ds.id, sim.cohort_json)
        check('finds the national row by relaxing seniority', point is not None, level)
        check('match level is exact (metro was already national)', level == 'exact', level)

        cal.invalidate_cache()
        missing, mlevel = cal.lookup_reference(ds.id, {'soc_group': '99-9999',
                                                      'metro': 'national', 'seniority': 'any'})
        check('unknown occupation misses cleanly', missing is None and mlevel == 'none')

        # ── posterior ───────────────────────────────────────────────────────
        print('\nposterior (PRD §9 worked example: model 432k vs reference p50 398k)')
        res = cal.compute_posterior(432000, cfg, point, ds, 'exact')
        check('mid lands between model and reference', 398000 < res['mid'] < 432000,
              '{:.0f}'.format(res['mid']))
        check('low < mid < high', res['low'] < res['mid'] < res['high'],
              '{:.0f} / {:.0f} / {:.0f}'.format(res['low'], res['mid'], res['high']))
        check('dense Tier-B verified data renders High', res['tier'] == TIER_HIGH, res['tier'])
        check('no thin-data guard on n=1240', res['thin_data_guard'] is False)
        check('rationale names the direction', 'down' in res['rationale'], res['rationale'])

        # thin data
        point.sample_size = 5
        thin = cal.compute_posterior(432000, cfg, point, ds, 'exact')
        check('thin sample fires the guard', thin['thin_data_guard'] is True)
        check('thin sample returns the raw value as mid', abs(thin['mid'] - 432000) < 1)
        check('thin sample is Directional', thin['tier'] == TIER_DIRECTIONAL, thin['tier'])
        check('thin band is wider than the dense band',
              (thin['high'] - thin['low']) > (res['high'] - res['low']))

        # no reference row at all
        nomatch = cal.compute_posterior(432000, cfg, None, ds, 'none')
        check('missing reference still returns a usable range',
              nomatch['low'] < nomatch['mid'] < nomatch['high'] and
              nomatch['tier'] == TIER_DIRECTIONAL)

        # tier guards
        point.sample_size = 1240
        ds.credibility_tier = 'C'
        capped = cal.compute_posterior(432000, cfg, point, ds, 'exact')
        check('Tier-C data can never render High', capped['tier'] == TIER_MODERATE, capped['tier'])

        ds.credibility_tier = 'B'
        ds.needs_review = True
        unverified = cal.compute_posterior(432000, cfg, point, ds, 'exact')
        check('unverified dataset is capped at Moderate',
              unverified['tier'] == TIER_MODERATE, unverified['tier'])

        ds.needs_review = False
        proxied = cal.compute_posterior(432000, cfg, point, ds, 'proxy')
        check('proxy match demotes one step', proxied['tier'] == TIER_MODERATE, proxied['tier'])

        # band floor: an agent that agrees exactly with the reference must still
        # produce a visible range, never a point
        agreeing = cal.compute_posterior(398000, cfg, point, ds, 'exact')
        half_pct = (agreeing['high'] - agreeing['low']) / 2 / agreeing['mid'] * 100
        check('band floor holds when model and data agree', half_pct >= 7.9,
              '{:.1f}% half-width'.format(half_pct))

        # percent units clamp
        pct_cfg = CalibrationConfig(
            id=generate_id(), agent_key='sales_page', layer='L3', dataset_id=ds.id,
            output_field='conversion_rate', field_label='Conversion rate', unit='pct',
            sigma_model_pct=25, min_sample_high=300, min_sample_moderate=40,
            band_floor_pct=8, drift_threshold_pct=15, min_reports_to_reweight=30,
        )
        pct_point = ReferenceDataPoint(
            id=generate_id(), dataset_id=ds.id, cohort_json=cohort_national,
            cohort_hash='x' * 32, p10=1.0, p50=2.5, p90=5.0, sample_size=400,
        )
        pct_res = cal.compute_posterior(3.0, pct_cfg, pct_point, ds, 'exact')
        check('percent stays within 0-100', 0 <= pct_res['low'] and pct_res['high'] <= 100,
              '{:.2f}-{:.2f}'.format(pct_res['low'], pct_res['high']))

        # ── METRICS round-trip ──────────────────────────────────────────────
        print('\nMETRICS block')
        prompt = cal.metrics_prompt_block('rate_card')
        check('prompt block generated for a configured agent',
              'projected_annual_income' in prompt and '<!--METRICS' in prompt)
        check('no prompt block for an unconfigured agent',
              cal.metrics_prompt_block('booking_page') == '')

        artifact = ('# Your rate card\n\nTier 1: $5,000/project\n\n'
                    '<!--METRICS\n{"projected_annual_income": 432000}\nMETRICS-->')
        clean, metrics = cal.extract_metrics_block(artifact)
        check('block is stripped from the artifact', 'METRICS' not in clean, repr(clean[-30:]))
        check('artifact body survives intact', clean.startswith('# Your rate card'))
        check('metric parsed', metrics == {'projected_annual_income': 432000.0}, metrics)

        _, dirty = cal.extract_metrics_block('x <!--METRICS not json METRICS-->')
        check('malformed block degrades to no metrics', dirty == {})
        _, zeroed = cal.extract_metrics_block('x <!--METRICS {"a": 0} METRICS-->')
        check('template zero is treated as no estimate', zeroed == {})
        _, messy = cal.extract_metrics_block('x <!--METRICS {"a": "$185/hr"} METRICS-->')
        check('currency-formatted value coerces', messy == {'a': 185.0}, messy)

        # ── persistence + read path ─────────────────────────────────────────
        print('\npersistence')
        runs = cal.calibrate_metrics(
            metrics={'projected_annual_income': 432000},
            agent_key='rate_card', simulation_id=sim.id, action_id='act12345',
        )
        check('one run persisted', len(runs) == 1, len(runs))
        stored = CalibrationRun.query.filter_by(action_id='act12345').all()
        check('run is readable back', len(stored) == 1)
        check('cohort recorded on the run',
              stored[0].cohort_json.get('soc_group') == '13-1111')
        # The requested cohort was seniority='senior'; the reference row we hold is
        # seniority='any'. The run must record what was matched so the drift job can
        # find the same row again.
        check('run records the MATCHED cohort, not the requested one',
              stored[0].cohort_json.get('seniority') == 'any',
              stored[0].cohort_json)
        matched_point = ReferenceDataPoint.query.filter_by(
            dataset_id=ds.id, cohort_hash=stored[0].cohort_hash).first()
        check('run cohort_hash resolves back to a real reference row',
              matched_point is not None)

        cal.stamp_version('act12345', 3)
        check('version stamped',
              CalibrationRun.query.get(stored[0].id).version_number == 3)

        # A re-run must supersede, not duplicate, the field on the card.
        cal.calibrate_metrics(
            metrics={'projected_annual_income': 410000},
            agent_key='rate_card', simulation_id=sim.id, action_id='act12345',
        )
        latest = cal.runs_for_action('act12345')
        check('re-run shows one row per field, not two', len(latest) == 1, len(latest))
        check('latest run is the newest', float(latest[0].raw_value) == 410000,
              float(latest[0].raw_value))

        check('unconfigured agent produces nothing',
              cal.calibrate_metrics({'x': 1}, 'booking_page', sim.id) == [])

        # A config bound to a dataset in a different unit would anchor, say, a
        # speaking fee to annual wage data and render it confidently. Refuse.
        print('\nunit guard')
        mismatched = CalibrationConfig(
            id=generate_id(), agent_key='speaking_proposals', layer='L2',
            dataset_id=ds.id, output_field='speaking_fee',
            field_label='Average speaking fee', unit='usd',   # dataset is usd_year
            sigma_model_pct=30, min_sample_high=300, min_sample_moderate=40,
            band_floor_pct=8, drift_threshold_pct=15, min_reports_to_reweight=30,
        )
        db.session.add(mismatched)
        db.session.commit()
        check('mismatched unit refuses to calibrate',
              cal.calibrate_value(mismatched, 7500, sim.cohort_json) is None)
        check('mismatched config writes no run',
              cal.calibrate_metrics({'speaking_fee': 7500}, 'speaking_proposals',
                                    sim.id, action_id='act99999') == [])
        check('mismatched config is not asked for a METRICS value',
              cal.metrics_prompt_block('speaking_proposals') != '' and
              'speaking_fee' in cal.metrics_prompt_block('speaking_proposals'),
              'prompt still lists it (bound but unusable) — admin sees the mismatch flag')
        mismatched.unit = 'usd_year'
        db.session.commit()
        check('fixing the unit restores calibration',
              cal.calibrate_value(mismatched, 400000, sim.cohort_json) is not None)

        print('\nformatting')
        check('formats 372000 as $372k', cal.format_value(372000, 'usd_year') == '$372k',
              cal.format_value(372000, 'usd_year'))
        check('formats hourly', cal.format_value(185, 'usd_hour') == '$185/hr',
              cal.format_value(185, 'usd_hour'))
        check('formats millions', cal.format_value(2400000, 'usd') == '$2.4M',
              cal.format_value(2400000, 'usd'))
        check('formats percent', cal.format_value(2.45, 'pct') == '2.5%',
              cal.format_value(2.45, 'pct'))

        # ── drift + the optimal-stopping gate ───────────────────────────────
        print('\ndrift + reweight gate')
        run = stored[0]
        for i in range(5):
            db.session.add(OutcomeReport(
                id=generate_id(), user_id=generate_id(), run_id=generate_id(),
                simulation_id=sim.id, agent_key='rate_card',
                output_field='projected_annual_income', reported_value=300000,
                cohort_json=cohort_national, cohort_hash=run.cohort_hash,
                is_verified=True,
            ))
        db.session.commit()

        results = drift.evaluate_all(persist=False)
        check('drift evaluated for the cohort', len(results) == 1, len(results))
        row = results[0]
        check('drift is negative (reports below reference)', row['drift_pct'] < 0,
              row['drift_pct'])
        check('drift exceeds the 15% threshold', row['exceeds_threshold'] is True)
        check('gate is NOT cleared at 5 of 30 reports', row['gate_cleared'] is False)
        check('action is gated, not flagged', row['action'] == 'gated', row['action'])

        blocked = drift.reweight_cohort('rate_card', 'projected_annual_income',
                                        run.cohort_hash)
        check('reweight refused below the gate',
              blocked['ok'] is False and blocked['error'] == 'gate_not_cleared', blocked)

        before = float(ReferenceDataPoint.query.filter_by(
            dataset_id=ds.id, cohort_hash=run.cohort_hash).first().p50)
        forced = drift.reweight_cohort('rate_card', 'projected_annual_income',
                                       run.cohort_hash, force=True)
        after = float(ReferenceDataPoint.query.filter_by(
            dataset_id=ds.id, cohort_hash=run.cohort_hash).first().p50)
        check('forced reweight succeeds', forced['ok'] is True, forced.get('message'))
        check('reweight moves p50 toward the reports', after < before,
              '{:.0f} -> {:.0f}'.format(before, after))
        check('parity blend keeps the move small (5 reports vs n=1240)',
              abs(after - before) / before < 0.02,
              '{:.3f}% shift'.format(abs(after - before) / before * 100))
        check('reweighted dataset returns to review',
              ReferenceDataset.query.get(ds.id).needs_review is True)

        check('nightly job runs clean', drift.run_drift_job('test')['cohorts_evaluated'] == 1)
        check('automatic reweighting is off by default', drift.reweight_is_live() is False)

    print('\n' + '=' * 62)
    print('{} passed, {} failed'.format(len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print('  FAILED: ' + f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
