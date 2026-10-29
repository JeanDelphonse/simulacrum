"""EXEC_PLAN P1 — artifact versioning invariants and safe actions.

  * the agent's generated version is immutable (enforced, copy-on-write)
  * a re-run never replaces a user-edited current version ("agent update available")
  * Re-run dispatches on a thread, not Celery
  * refine proposes without saving; accepting saves an agent_refine version
  * PDF export, copy source, duplicate — and none of them touch the network

Runs on in-memory SQLite with a fake model client.

    python test_artifact_actions.py
"""
import json
import os
import sys
import time
from types import SimpleNamespace

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


class FakeClient:
    def __init__(self, text):
        self.text, self.calls = text, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(text=self.text)],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                               stop_reason='end_turn')


def main():
    import requests
    network = []

    def no_network(*a, **k):
        network.append(a[0] if a else k.get('url'))
        raise AssertionError('network call during a safe action: %s' % (a[0] if a else k))

    requests.post = requests.get = no_network

    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.agent_action import AgentAction
        from app.models.artifact import ArtifactVersion, OriginalArtifactImmutable
        from app.services import artifact_actions, claude

        claude._log_interaction = lambda *a, **k: None
        from datetime import datetime as _dt
        owner = User(id=generate_id(), email='o@x.com', full_name='Owner', password_hash='x',
                     email_verified=True, onboarding_completed_at=_dt.utcnow())
        stranger = User(id=generate_id(), email='s@x.com', full_name='S', password_hash='x',
                        email_verified=True)
        db.session.add_all([owner, stranger])
        db.session.flush()
        sim = Simulation(id=generate_id(), user_id=owner.id, name='Engine', expertise_zone='Ops',
                         status='complete')
        db.session.add(sim)
        db.session.flush()

        def artifact(agent='rate_card', content='# Rate Card\n\n| Tier | Price |\n|---|---|\n| A | $1 |\n'):
            a = AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1, action_type=agent,
                            status='complete', artifact=content, created_by=owner.id)
            db.session.add(a)
            db.session.flush()
            db.session.add(ArtifactVersion(id=generate_id(), action_id=a.id, simulation_id=sim.id,
                                           layer_number=1, action_type=agent, version_number=1,
                                           content=content, is_current=True))
            db.session.commit()
            return a

        rc = artifact()
        camp = artifact('consulting_outreach', json.dumps({'prospects': [
            {'first_name': 'Ann', 'last_name': 'X', 'email': 'ann@a.com',
             'email_draft': {'subject': 'Hi', 'body': 'Hello'}}]}))
        ids = dict(rc=rc.id, camp=camp.id, sim=sim.id, owner=owner.id, stranger=stranger.id)

        client = app.test_client()

        def login(uid):
            with client.session_transaction() as sess:
                sess['_user_id'] = uid
                sess['_fresh'] = True
            g.pop('_login_user', None)

        print('\n1. The agent original is immutable')
        v1 = ArtifactVersion.query.filter_by(action_id=ids['rc'], version_number=1).first()
        v1.content = 'tampered'
        try:
            db.session.commit()
            check('in-place edit of an agent original is refused', False)
        except OriginalArtifactImmutable:
            db.session.rollback()
            check('in-place edit of an agent original is refused', True)
        v1 = ArtifactVersion.query.filter_by(action_id=ids['rc'], version_number=1).first()
        check('…and the original content is unchanged', v1.content.startswith('# Rate Card'))
        w = ArtifactVersion.ensure_writable(v1)
        w.content = 'tracked'
        db.session.commit()
        check('copy-on-write: first write clones into a new current version',
              w.version_number == 2 and w.is_current and not v1.is_current
              and v1.content.startswith('# Rate Card'))
        check('later writes reuse the clone (no version per click)',
              ArtifactVersion.ensure_writable(w) is w)
        v1._allow_original_write = True
        v1.content = v1.content
        db.session.commit()
        check('the sanctioned admin-release exception still works', True)
        # put rc back to a clean single agent version for the re-run tests
        ArtifactVersion.query.filter_by(action_id=ids['rc'], version_number=2).delete()
        v1.is_current = True
        db.session.commit()

        login(ids['owner'])
        res = client.post('/api/artifacts/%s/consulting-outreach/0/edit' % ids['camp'],
                          json={'subject': 'New subject'})
        db.session.expire_all()
        cv = ArtifactVersion.query.filter_by(action_id=ids['camp']).order_by(
            ArtifactVersion.version_number).all()
        check('outreach inline edit lands in a new user version; the original keeps its text',
              res.status_code == 200 and len(cv) == 2 and cv[1].edited_by == 'user'
              and json.loads(cv[0].content)['prospects'][0]['email_draft']['subject'] == 'Hi'
              and json.loads(cv[1].content)['prospects'][0]['email_draft']['subject'] == 'New subject',
              [(v.version_number, v.edited_by) for v in cv])

        print('\n2. Re-run never takes over a user edit')
        from app.tasks import agent as agent_task
        from app.tasks.agent import execute_agent_action_task, AGENT_UPDATE_SUMMARY
        # The task's "what changed" summary is a real model call, and engine
        # disposal (a MySQL idle-connection workaround) would drop the in-memory DB.
        agent_task._generate_change_summary = lambda *a, **k: None
        db.engine.dispose = lambda *a, **k: None
        claude.execute_agent_action = lambda **kw: '# Rate Card\n\nAgent rerun output'
        execute_agent_action_task.run(ids['rc'])      # no user edit yet → replaces
        login(ids['owner'])   # the task reset the session; drop the cached user
        db.session.expire_all()
        cur = ArtifactVersion.current_for(ids['rc'])
        check('without a user edit, the re-run becomes current',
              cur.version_number == 2 and 'Agent rerun output' in cur.content, cur.version_number)
        res = client.post('/api/artifacts/%s/save' % ids['rc'], json={'payload': '# Rate Card\n\nMy edit'})
        check('user saves an edit', res.status_code == 200, res.get_json())
        claude.execute_agent_action = lambda **kw: '# Rate Card\n\nSecond agent run'
        execute_agent_action_task.run(ids['rc'])
        login(ids['owner'])
        db.session.expire_all()
        cur = ArtifactVersion.current_for(ids['rc'])
        act = AgentAction.query.get(ids['rc'])
        upd = artifact_actions.agent_update(ids['rc'])
        check('the user edit stays current after a re-run', 'My edit' in cur.content, cur.content)
        check('downstream code still reads the user version (action.artifact)', 'My edit' in act.artifact)
        check('the re-run is parked as "agent update available"',
              upd is not None and 'Second agent run' in upd.content and upd.edit_summary == AGENT_UPDATE_SUMMARY)
        res = client.post('/api/artifacts/%s/versions/%d/restore' % (ids['rc'], upd.version_number))
        db.session.expire_all()
        check('"Use the agent\'s version" makes it current',
              res.status_code == 200 and 'Second agent run' in ArtifactVersion.current_for(ids['rc']).content)

        started = []
        execute_agent_action_task.apply = lambda args=None, **k: started.append(args)
        res = client.post('/api/artifacts/%s/rerun' % ids['rc'], json={})
        for _ in range(50):
            if started:
                break
            time.sleep(0.05)
        check('Re-run dispatches on a thread (no Celery worker needed)',
              res.status_code == 200 and started == [[ids['rc']]], (res.status_code, started))

        print('\n3. Refine, export, copy, duplicate')
        fake = FakeClient('# Rate Card\n\nRefined copy')
        claude._client = lambda: fake
        before = ArtifactVersion.query.filter_by(action_id=ids['rc']).count()
        res = client.post('/api/artifacts/%s/refine' % ids['rc'], json={'instruction': 'Tighten it'})
        d = res.get_json() or {}
        check('refine returns a proposal', res.status_code == 200 and 'Refined copy' in d.get('proposed', ''), d)
        check('…and saves nothing', ArtifactVersion.query.filter_by(action_id=ids['rc']).count() == before)
        check('refine prompt forbids inventing facts', 'Never invent' in fake.calls[-1]['system'])
        res = client.post('/api/artifacts/%s/save' % ids['rc'],
                          json={'payload': d['proposed'], 'origin': 'agent_refine', 'instruction': 'Tighten it'})
        db.session.expire_all()
        cur = ArtifactVersion.current_for(ids['rc'])
        check('accepting saves an agent_refine version with the instruction',
              cur.edited_by == 'agent_refine' and 'Tighten it' in (cur.edit_summary or ''), (cur.edited_by, cur.edit_summary))
        res = client.post('/api/artifacts/%s/refine' % ids['camp'], json={'instruction': 'x'})
        check('campaign JSON is not refined as a whole', res.status_code == 400)
        res = client.post('/api/artifacts/%s/refine' % ids['rc'], json={'instruction': ''})
        check('empty instruction refused', res.status_code == 400)

        res = client.get('/api/artifacts/%s/export.pdf' % ids['rc'])
        check('PDF export returns a PDF of the current version',
              res.status_code == 200 and res.mimetype == 'application/pdf' and res.data[:4] == b'%PDF',
              (res.status_code, res.mimetype))
        import fitz
        import unicodedata
        # NFKC folds the typeset 'ﬁ' ligature back to 'fi' for comparison.
        text = unicodedata.normalize('NFKC', fitz.open(stream=res.data, filetype='pdf')[0].get_text())
        check('PDF contains the current content', 'Refined copy' in text, text[:80])
        res = client.get('/api/artifacts/%s/export.pdf?version=1' % ids['rc'])
        t1 = fitz.open(stream=res.data, filetype='pdf')[0].get_text()
        check('PDF of a chosen earlier version', 'Tier' in t1 and 'Refined copy' not in t1)
        check('campaign artifacts export too',
              client.get('/api/artifacts/%s/export.pdf' % ids['camp']).data[:4] == b'%PDF')

        res = client.get('/api/artifacts/%s' % ids['rc'])
        check('copy source is the current version', 'Refined copy' in (res.get_json() or {}).get('current_payload', ''))

        n_actions = AgentAction.query.count()
        res = client.post('/api/artifacts/%s/duplicate' % ids['rc'])
        d = res.get_json() or {}
        db.session.expire_all()
        dup = AgentAction.query.get(d.get('action_id'))
        check('duplicate creates a new artifact with the current content',
              res.status_code == 200 and dup is not None and AgentAction.query.count() == n_actions + 1
              and 'Refined copy' in ArtifactVersion.current_for(dup.id).content, d)
        check('…and leaves the original untouched',
              'Refined copy' in ArtifactVersion.current_for(ids['rc']).content
              and ArtifactVersion.query.filter_by(action_id=ids['rc'], version_number=1).first().content.startswith('# Rate Card'))
        check('campaigns cannot be duplicated',
              client.post('/api/artifacts/%s/duplicate' % ids['camp']).status_code == 400)

        print('\n4. Access and page')
        login(ids['stranger'])
        check('strangers cannot refine', client.post('/api/artifacts/%s/refine' % ids['rc'],
                                                     json={'instruction': 'x'}).status_code == 403)
        check('strangers cannot export', client.get('/api/artifacts/%s/export.pdf' % ids['rc']).status_code == 403)
        check('strangers cannot duplicate', client.post('/api/artifacts/%s/duplicate' % ids['rc']).status_code == 403)
        login(ids['owner'])
        pres = client.get('/simulations/%s/artifacts/%s' % (ids['sim'], ids['rc']))
        page = pres.get_data(as_text=True)
        if pres.status_code != 200:
            print('    page status', pres.status_code, pres.headers.get('Location'))
        check('artifact page renders the action bar',
              all(k in page for k in ('aabRefineBtn', 'aabPdfBtn', 'aabCopyBtn', 'aabDuplicateBtn',
                                      'artifact_action_bar.js')))
        cpage = client.get('/simulations/%s/artifacts/%s' % (ids['sim'], ids['camp'])).get_data(as_text=True)
        check('campaign page: PDF and copy, no refine or duplicate',
              'aabPdfBtn' in cpage and 'aabRefineBtn' not in cpage and 'aabDuplicateBtn' not in cpage)

        check('no safe action made a network call', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
