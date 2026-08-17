"""Admin run-cycle reset / delete — HTTP layer.

Covers the authorisation boundary (non-admin, anonymous), the preview-is-safe
contract, and the 409-then-force path for a cycle that is still mid-flight.
Against in-memory SQLite; no network, no real data.

    python test_cycle_admin_api.py
"""
import os
import sys
from datetime import datetime

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
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:')

    with app.app_context():
        from app.models import kajabi, prospect_research, social_queue  # noqa: F401
        from app.models.agent_action import AgentAction
        from app.models.layer6 import Layer6ActionQueue, Layer6Cycle
        from app.models.simulation import Simulation
        from app.models.user import User
        db.create_all()

        owner = User(id=generate_id(), email='owner@t.local', full_name='Owner',
                     password_hash='x')
        admin = User(id=generate_id(), email='admin@t.local', full_name='Admin',
                     password_hash='x', is_admin=True)
        db.session.add_all([owner, admin])
        sim = Simulation(id=generate_id(), user_id=owner.id, name='S', status='complete')
        db.session.add(sim)
        db.session.flush()

        done = Layer6Cycle(id=generate_id(), simulation_id=sim.id, cycle_number=1,
                          cycle_completed_at=datetime.utcnow())
        running = Layer6Cycle(id=generate_id(), simulation_id=sim.id, cycle_number=2)
        db.session.add_all([done, running])
        db.session.flush()

        action = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1,
                            action_type='rate_card', status='complete')
        db.session.add(action)
        db.session.flush()
        db.session.add(Layer6ActionQueue(
            id=generate_id(), simulation_id=sim.id, cycle_id=done.id, source_layer=1,
            action_type='rate_card', agent_action_id=action.id, status='complete'))
        db.session.commit()

        sim_id, owner_id, admin_id = sim.id, owner.id, admin.id
        done_id, running_id = done.id, running.id

    def login(client, user_id):
        with client.session_transaction() as sess:
            sess['_user_id'] = user_id
            sess['_fresh'] = True

    print('\nauthorisation')
    with app.test_client() as c:
        login(c, owner_id)   # the simulation's own owner, but not an admin
        check('owner (non-admin) blocked from user simulations',
              c.get('/api/admin/users/{}/simulations'.format(owner_id)).status_code == 403)
        check('owner blocked from the cycle list',
              c.get('/api/admin/simulations/{}/cycles'.format(sim_id)).status_code == 403)
        check('owner blocked from preview',
              c.post('/api/admin/simulations/{}/cycles/preview'.format(sim_id),
                     json={}).status_code == 403)
        check('owner blocked from delete',
              c.post('/api/admin/simulations/{}/cycles/delete'.format(sim_id),
                     json={}).status_code == 403)
        check('owner blocked from reset',
              c.post('/api/admin/simulations/{}/cycles/reset'.format(sim_id),
                     json={}).status_code == 403)
    with app.test_client() as c:
        check('anonymous is not served',
              c.get('/api/admin/simulations/{}/cycles'.format(sim_id)).status_code
              in (302, 401))

    print('\nlisting')
    with app.test_client() as c:
        login(c, admin_id)
        r = c.get('/api/admin/users/{}/simulations'.format(owner_id))
        check('lists the user simulations with cycle counts',
              r.status_code == 200 and r.get_json()['simulations'][0]['cycle_count'] == 2,
              r.get_json())
        js = c.get('/api/admin/simulations/{}/cycles'.format(sim_id)).get_json()
        check('cycles listed newest first',
              [x['cycle_number'] for x in js['cycles']] == [2, 1])
        check('the mid-flight cycle is flagged', js['cycles'][0]['is_running'] is True)
        check('artifact count per cycle is reported',
              js['cycles'][1]['artifacts'] == 1, js['cycles'][1])

    print('\npreview is non-destructive')
    with app.test_client() as c:
        login(c, admin_id)
        r = c.post('/api/admin/simulations/{}/cycles/preview'.format(sim_id),
                   json={'cycle_ids': [done_id]})
        check('preview 200', r.status_code == 200, r.status_code)
        check('preview counts the action to delete',
              r.get_json()['delete'].get('agent_actions') == 1, r.get_json().get('delete'))
        check('preview changed nothing',
              len(c.get('/api/admin/simulations/{}/cycles'.format(sim_id))
                  .get_json()['cycles']) == 2)

    print('\ndelete + the mid-flight guard')
    with app.test_client() as c:
        login(c, admin_id)
        r = c.post('/api/admin/simulations/{}/cycles/delete'.format(sim_id),
                   json={'cycle_ids': [running_id]})
        check('409 while a cycle is still running',
              r.status_code == 409 and r.get_json()['error'] == 'cycle_running',
              r.status_code)
        check('the 409 names the cycle so the UI can explain it',
              r.get_json().get('running_cycles') == [2], r.get_json().get('running_cycles'))
        r = c.post('/api/admin/simulations/{}/cycles/delete'.format(sim_id),
                   json={'cycle_ids': [running_id], 'force': True})
        check('force=true overrides the guard',
              r.status_code == 200 and r.get_json()['ok'] is True, r.get_json())

        r = c.post('/api/admin/simulations/{}/cycles/delete'.format(sim_id),
                   json={'cycle_ids': [done_id]})
        check('deletes a completed cycle',
              r.status_code == 200 and r.get_json()['cycles_deleted'] == 1)
        check('response explains what was preserved',
              'Preserved' in r.get_json()['message'] or not r.get_json()['preserved'],
              r.get_json()['message'])
        check('all cycles gone',
              c.get('/api/admin/simulations/{}/cycles'.format(sim_id))
              .get_json()['cycles'] == [])

    print('\nreset + not-found handling')
    with app.test_client() as c:
        login(c, admin_id)
        r = c.post('/api/admin/simulations/{}/cycles/reset'.format(sim_id), json={})
        check('reset succeeds even with nothing left to delete',
              r.status_code == 200 and r.get_json()['ok'] is True, r.get_json())
        check('404 for an unknown simulation',
              c.post('/api/admin/simulations/nope12345/cycles/reset',
                     json={}).status_code == 404)
        check('404 for an unknown user',
              c.get('/api/admin/users/nope12345/simulations').status_code == 404)

    print('\n' + '=' * 62)
    print('{} passed, {} failed'.format(len(PASS), len(FAIL)))
    for f in FAIL:
        print('  FAILED: ' + f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
