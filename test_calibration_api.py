"""SIM-PRD-CAL-001 §13 — HTTP-level checks for the Calibration Layer API.

Exercises the real routes through Flask's test client against in-memory SQLite,
including the authorisation boundaries (owner vs stranger vs admin) and the
outcome-report upsert. No network, no real data.

    python test_calibration_api.py
"""
import os
import sys

os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label,
                              ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def main():
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id

    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      WTF_CSRF_ENABLED=False, LOGIN_DISABLED=False)

    with app.app_context():
        db.create_all()

        from app.models.agent_action import AgentAction
        from app.models.calibration import (
            CalibrationConfig, OutcomeReport, ReferenceDataPoint, ReferenceDataset,
        )
        from app.models.simulation import Simulation
        from app.models.user import User
        from app.services import calibration_cohort as coh
        from app.services import calibration_service as cal

        owner = User(id=generate_id(), email='owner@t.local', full_name='Owner',
                     password_hash='x')
        stranger = User(id=generate_id(), email='stranger@t.local', full_name='Stranger',
                        password_hash='x')
        admin = User(id=generate_id(), email='admin@t.local', full_name='Admin',
                     password_hash='x', is_admin=True)
        db.session.add_all([owner, stranger, admin])

        sim = Simulation(id=generate_id(), user_id=owner.id, name='S',
                         expertise_zone='consulting', status='complete')
        sim.cohort_json = {'soc_group': '13-1111', 'metro': 'national',
                           'seniority': 'any', 'soc_title': 'Management Analysts'}
        db.session.add(sim)

        action = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1,
                            action_type='rate_card', status='complete')
        db.session.add(action)

        ds = ReferenceDataset(id=generate_id(), layer='L1', name='OES', source='BLS',
                              source_url='https://www.bls.gov/oes/', unit='usd_hour',
                              credibility_tier='C', needs_review=True,
                              as_of_label='May 2024', is_active=True)
        db.session.add(ds)
        db.session.flush()

        cohort = {'soc_group': '13-1111', 'metro': 'national', 'seniority': 'any'}
        db.session.add(ReferenceDataPoint(
            id=generate_id(), dataset_id=ds.id, cohort_json=cohort,
            cohort_hash=coh.cohort_hash(cohort), p10=56, p50=109, p90=186,
            sample_size=1030000))
        db.session.add(CalibrationConfig(
            id=generate_id(), agent_key='rate_card', layer='L1', dataset_id=ds.id,
            output_field='billable_hourly_rate', field_label='Your billable hourly rate',
            unit='usd_hour', sigma_model_pct=25, min_sample_high=300,
            min_sample_moderate=40, band_floor_pct=8, drift_threshold_pct=15,
            min_reports_to_reweight=30, is_enabled=True))
        db.session.commit()

        runs = cal.calibrate_metrics({'billable_hourly_rate': 400}, 'rate_card',
                                     sim.id, action_id=action.id)
        run_id = runs[0].id
        sim_id, action_id = sim.id, action.id
        owner_id, stranger_id, admin_id = owner.id, stranger.id, admin.id
        ds_id = ds.id

    def login(client, user_id):
        with client.session_transaction() as sess:
            sess['_user_id'] = user_id
            sess['_fresh'] = True

    print('\nuser API — owner')
    with app.test_client() as c:
        login(c, owner_id)
        r = c.get('/api/artifacts/{}/calibration'.format(action_id))
        check('artifact calibration 200', r.status_code == 200, r.status_code)
        body = r.get_json()
        check('one calibrated field returned', len(body['fields']) == 1, body.get('fields'))
        f = body['fields'][0]
        check('label present', f['field_label'] == 'Your billable hourly rate')
        check('range is ordered', f['low'] < f['mid'] < f['high'],
              '{:.0f}/{:.0f}/{:.0f}'.format(f['low'], f['mid'], f['high']))
        check('display strings formatted', f['display']['low'].startswith('$'),
              f['display'])
        check('Tier-C source capped below High', f['tier'] != 'high', f['tier'])
        check('source cited', f['source'] and f['source_url'], f.get('source'))
        check('band geometry present and ordered',
              f['band']['low_pct'] < f['band']['mid_pct'] < f['band']['high_pct'],
              f['band'])
        check('raw tick clamped into the track',
              0 <= f['band']['raw_pct'] <= 100 and f['band']['raw_clamped'] is True,
              f['band'])
        check('cohort labelled for the drawer', 'Management Analysts' in f['cohort_label'],
              f['cohort_label'])
        check('tier hint explains the ceiling',
              'verification' in f['tier_hint'] or 'proxy' in f['tier_hint'],
              f['tier_hint'])
        check('outcome widget open, nothing reported yet',
              f['outcome']['open'] is True and f['outcome']['reported'] is None)

        r = c.get('/api/calibration/{}'.format(run_id))
        check('drawer detail 200', r.status_code == 200, r.status_code)
        check('drawer carries the reference p50',
              r.get_json()['ref_p50'] == 109, r.get_json().get('ref_p50'))

        r = c.get('/api/simulations/{}/calibration'.format(sim_id))
        check('simulation summary 200', r.status_code == 200, r.status_code)
        check('summary links back to the artifact',
              r.get_json()['fields'][0]['action_id'] == action_id)

    print('\nauthorisation')
    with app.test_client() as c:
        login(c, stranger_id)
        check('stranger blocked from artifact calibration',
              c.get('/api/artifacts/{}/calibration'.format(action_id)).status_code == 403)
        check('stranger blocked from the drawer',
              c.get('/api/calibration/{}'.format(run_id)).status_code == 403)
        check('stranger blocked from the summary',
              c.get('/api/simulations/{}/calibration'.format(sim_id)).status_code == 403)
        check('stranger cannot report an outcome on it',
              c.post('/api/outcomes', json={'run_id': run_id,
                                            'reported_value': 1}).status_code == 403)
        check('stranger blocked from admin datasets',
              c.get('/api/admin/calibration/datasets').status_code == 403)
    with app.test_client() as c:
        check('anonymous is not served', c.get(
            '/api/artifacts/{}/calibration'.format(action_id)).status_code in (302, 401))

    print('\noutcome reporting')
    with app.test_client() as c:
        login(c, owner_id)
        r = c.post('/api/outcomes', json={'run_id': run_id, 'reported_value': '$185/hr',
                                          'note': 'landed a retainer'})
        check('report created 201', r.status_code == 201, r.status_code)
        check('currency string parsed to 185',
              r.get_json()['report']['reported_value'] == 185,
              r.get_json()['report'])
        check('confirmation frames it as private',
              'private' in r.get_json()['message'].lower())

        r2 = c.post('/api/outcomes', json={'run_id': run_id, 'reported_value': 210})
        check('resubmit updates in place (200, not a duplicate)', r2.status_code == 200,
              r2.status_code)
        with app.app_context():
            from app.models.calibration import OutcomeReport as OR
            check('exactly one report stored for this user+run',
                  OR.query.filter_by(user_id=owner_id, run_id=run_id).count() == 1)
            check('stored value is the latest',
                  float(OR.query.filter_by(run_id=run_id).first().reported_value) == 210)

        check('rejects a non-numeric value',
              c.post('/api/outcomes', json={'run_id': run_id,
                                            'reported_value': 'lots'}).status_code == 400)
        check('rejects an unknown run',
              c.post('/api/outcomes', json={'run_id': 'nope12345',
                                            'reported_value': 5}).status_code == 404)

        r = c.get('/api/artifacts/{}/calibration'.format(action_id))
        check('card now shows the reported outcome',
              r.get_json()['fields'][0]['outcome']['reported'] is not None)

    print('\nadmin API')
    with app.test_client() as c:
        login(c, admin_id)
        check('datasets list', c.get('/api/admin/calibration/datasets').status_code == 200)
        check('configs list', c.get('/api/admin/calibration/configs').status_code == 200)
        check('drift view', c.get('/api/admin/calibration/drift').status_code == 200)
        check('outcomes list', c.get('/api/admin/calibration/outcomes').status_code == 200)

        r = c.post('/api/admin/calibration/datasets',
                   json={'name': 'New DS', 'source': 'Somebody', 'layer': 'L2',
                         'unit': 'usd', 'credibility_tier': 'A'})
        check('dataset created 201', r.status_code == 201, r.status_code)
        new_id = r.get_json()['id']
        check('new dataset starts unverified and capped at Moderate',
              r.get_json()['needs_review'] is True and r.get_json()['tier_cap'] == 'moderate',
              r.get_json())

        csv_body = ('soc_group,metro,seniority,p10,p50,p90,sample_size,as_of_date\n'
                    '13-1111,national,any,"1,000","2,000","4,000",500,2025-01-01\n'
                    '15-1252,,,90000,140000,210000,900,2025-01-01\n'
                    ',,,1,2,3,4,2025-01-01\n')
        r = c.post('/api/admin/calibration/datasets/{}/upload'.format(new_id),
                   json={'csv': csv_body})
        up = r.get_json()
        check('CSV upload created 2 rows and skipped the bad one',
              up['created'] == 2 and up['skipped'] == 1, up)
        check('upload reports why a row was skipped', bool(up['errors']), up['errors'])

        r = c.post('/api/admin/calibration/datasets/{}/upload'.format(new_id),
                   json={'csv': csv_body})
        check('re-upload upserts rather than duplicating',
              r.get_json()['updated'] == 2 and r.get_json()['created'] == 0,
              r.get_json())

        r = c.post('/api/admin/calibration/configs',
                   json={'agent_key': 'sales_page', 'layer': 'L3',
                         'output_field': 'conversion_rate', 'field_label': 'Conversion',
                         'unit': 'pct', 'dataset_id': new_id})
        check('config created 201', r.status_code == 201, r.status_code)
        cfg_id = r.get_json()['id']
        check('duplicate config rejected 409',
              c.post('/api/admin/calibration/configs',
                     json={'agent_key': 'sales_page', 'layer': 'L3',
                           'output_field': 'conversion_rate', 'field_label': 'x',
                           'unit': 'pct'}).status_code == 409)
        check('incoherent thresholds rejected 400',
              c.put('/api/admin/calibration/configs/{}'.format(cfg_id),
                    json={'min_sample_high': 10,
                          'min_sample_moderate': 500}).status_code == 400)

        check('deleting a bound dataset is refused 409',
              c.delete('/api/admin/calibration/datasets/{}'.format(new_id)).status_code == 409)
        check('forced delete succeeds',
              c.delete('/api/admin/calibration/datasets/{}?force=1'.format(
                  new_id)).status_code == 200)
        with app.app_context():
            from app.models.calibration import CalibrationConfig as CC
            check('forced delete unbinds rather than orphans the config',
                  CC.query.get(cfg_id).dataset_id is None)

        check('verifying a dataset lifts its cap',
              c.put('/api/admin/calibration/datasets/{}'.format(ds_id),
                    json={'needs_review': False}).get_json()['tier_cap'] == 'moderate')

    print('\npages')
    with app.test_client() as c:
        login(c, admin_id)
        check('admin console renders', c.get('/admin/calibration').status_code == 200)
    with app.test_client() as c:
        login(c, owner_id)
        check('non-admin blocked from the console',
              c.get('/admin/calibration').status_code == 403)

    print('\n' + '=' * 62)
    print('{} passed, {} failed'.format(len(PASS), len(FAIL)))
    for f in FAIL:
        print('  FAILED: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
