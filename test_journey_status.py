"""Journey tab shows what agents are really doing.

A cycle dispatches its agents together but runs them one at a time, so an
agent waiting its turn used to read "in progress"; a run whose worker died read
"in progress" until the next cycle (up to a day). Quick agents also waited
behind the slow outreach agents, which ran first.

    python test_journey_status.py
"""
import os
import sys
from datetime import datetime, timedelta

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
    print(line.encode(sys.stdout.encoding or 'ascii', 'replace')
              .decode(sys.stdout.encoding or 'ascii'))


def main():
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.layer6 import Layer6Config, Layer6Cycle, Layer6ActionQueue
        from app.services import layer6

        user = User(id=generate_id(), email='o@x.com', full_name='O', password_hash='x',
                    email_verified=True)
        sim = Simulation(id=generate_id(), user_id=user.id, name='S', expertise_zone='Ops',
                         status='complete')
        db.session.add_all([user, sim])
        db.session.flush()
        db.session.add(Layer6Config(id=generate_id(), simulation_id=sim.id))
        now = datetime.utcnow()
        cycle = Layer6Cycle(simulation_id=sim.id, cycle_number=1, phase='explore',
                            cycle_started_at=now - timedelta(hours=2))
        db.session.add(cycle)
        db.session.flush()

        def entry(agent, dispatched_ago_min, action_age_min=None, action_status='in_progress'):
            q = Layer6ActionQueue(simulation_id=sim.id, cycle_id=cycle.id, source_layer=1,
                                  action_type=agent, priority_score=1,
                                  status=Layer6ActionQueue.STATUS_DISPATCHED,
                                  dispatched_at=now - timedelta(minutes=dispatched_ago_min))
            db.session.add(q)
            if action_age_min is not None:
                a = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1,
                                action_type=agent, status=action_status,
                                created_at=now - timedelta(minutes=action_age_min))
                db.session.add(a)
                db.session.flush()
                q.agent_action_id = a.id
            db.session.flush()
            return q

        running = entry('rate_card', 5, action_age_min=2)          # genuinely running
        waiting = entry('booking_page', 5)                          # waiting its turn
        dead_run = entry('linkedin_optimization', 50, action_age_min=40)
        dead_wait = entry('role_search', 60)
        db.session.commit()
        ids = {k: v.id for k, v in (('running', running), ('waiting', waiting),
                                    ('dead_run', dead_run), ('dead_wait', dead_wait))}
        sim_id, user_id = sim.id, user.id

        print('\n1. Journey API statuses')
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = user_id
            sess['_fresh'] = True
        res = client.get('/api/simulations/%s/journey' % sim_id)
        check('journey endpoint answers', res.status_code == 200, res.status_code)
        body = res.get_json() or {}
        statuses = {}
        for layer in body.get('layers', []) if isinstance(body.get('layers'), list) else []:
            for a in layer.get('actions', []):
                statuses[a['action_type']] = a['status']
        if not statuses:
            # tolerate the alternative response shape
            for rec in body.get('actions', []) or body.get('records', []):
                statuses[rec['action_type']] = rec['status']
        check('a running agent reads "in_progress"', statuses.get('rate_card') == 'in_progress', statuses)
        check('an agent waiting its turn reads "waiting", not "in progress"',
              statuses.get('booking_page') == 'waiting', statuses)
        check('a run whose worker died reads "failed" right away',
              statuses.get('linkedin_optimization') == 'failed', statuses)
        check('an entry that never started and is long overdue reads "failed"',
              statuses.get('role_search') == 'failed', statuses)

        dead_action = AgentAction.query.get(Layer6ActionQueue.query.get(ids['dead_run']).agent_action_id)
        check('the dead run carries an explanation',
              dead_action.status == 'failed' and 'next cycle' in (dead_action.error_message or ''),
              dead_action.error_message)
        check('a live run is left alone',
              Layer6ActionQueue.query.get(ids['running']).status == 'dispatched')

        print('\n2. Run order')
        order = sorted(['cold_email_campaign', 'booking_page', 'consulting_outreach',
                        'role_search', 'rate_card'], key=layer6._run_order_key)
        check('quick agents run before slow outreach/research agents',
              order[:2] == ['booking_page', 'rate_card'] and
              set(order[2:]) == {'cold_email_campaign', 'consulting_outreach', 'role_search'}, order)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
