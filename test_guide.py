"""SME Console conversational guide.

Claude is faked: each test queues the structured turn the model "returns" and
checks what the server does with it. requests is blocked.

    python test_guide.py
"""
import os
import re
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


class FakeAI:
    def __init__(self):
        self.queue, self.calls = [], []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=1200, output_tokens=150, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if 'tools' not in kw:      # the end-of-session summary
            return SimpleNamespace(content=[SimpleNamespace(type='text', text='Covered L1; goal approved.')], usage=usage)
        out = self.queue.pop(0) if self.queue else {'say': 'What would success look like?', 'about_user': False,
                                                   'sources': [], 'layer': 1, 'grow_step': 'G'}
        return SimpleNamespace(content=[SimpleNamespace(type='tool_use', input=out)], usage=usage)


def main():
    import requests
    network = []
    requests.post = requests.get = (lambda *a, **k: network.append(a[0] if a else k)
                                    or (_ for _ in ()).throw(AssertionError('network')))
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    from cryptography.fernet import Fernet
    app.config['ENCRYPTION_KEY'] = Fernet.generate_key().decode()

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.profile import UserProfile
        from app.models.resume import Resume
        from app.models.contact import Contact
        from app.models.simulation import Simulation, SimulationLayer, IncomeStream
        from app.models.agent_action import AgentAction
        from app.models.platform_settings import PlatformSetting
        from app.models.sme_guide import (GoalCard, GoalCardAgentChange, SkillSuggestion, SmeGuide, SmeSession,
                                          SmeTurn)
        from app.services import guide_service as gs
        from app.services.agent_briefing import build_briefing

        ai = FakeAI()
        gs.AI_CLIENT = ai
        now = datetime.utcnow()

        def user(email, name, admin=False):
            u = User(id=generate_id(), email=email, full_name=name, password_hash='x', is_admin=admin,
                     email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.flush()
            db.session.add(UserProfile(user_id=u.id, username=email.split('@')[0], display_name=name,
                                       tagline='Operations consultant', location='Austin, TX'))
            db.session.commit()
            return u

        elena = user('elena@x.com', 'Elena Vasquez')
        admin = user('admin@x.com', 'Admin', admin=True)
        other = user('other@x.com', 'Other')
        E, A, O = elena.id, admin.id, other.id
        p = UserProfile.query.filter_by(user_id=E).first()
        p.career_history = [{'company_name': 'Kessler Manufacturing', 'role': 'Operations Director',
                             'start_date': '2016', 'end_date': 'Present',
                             'description': 'Led plant consolidation; downtime down 18%.'}]
        db.session.add(Resume(id=generate_id(), user_id=E, label='CV', parsed_text=(
            'Elena Vasquez\nLed the plant consolidation at Kessler that cut downtime 18% across two sites\n'
            'Managed 14 vendors and reduced procurement cost by $1.2M annually\nHobbies: hiking')))
        sim = Simulation(id=generate_id(), user_id=E, name='Ops path', expertise_zone='Operations', status='complete')
        db.session.add(sim)
        db.session.flush()
        L1 = SimulationLayer(id=generate_id(), simulation_id=sim.id, layer_number=1, layer_name='Active income',
                             ai_narrative='Fractional operations consulting is the fastest path.')
        db.session.add(L1)
        db.session.flush()
        db.session.add(IncomeStream(id=generate_id(), layer_id=L1.id, name='Fractional COO', est_monthly_low=6000,
                                    est_monthly_high=12000, ai_reasoning='x'))
        db.session.add(AgentAction(id=generate_id(), simulation_id=sim.id, layer_number=1, action_type='rate_card',
                                   status='complete', artifact='rate card', created_by=E, completed_at=now))
        db.session.add(Contact(id=generate_id(), user_id=E, first_name='Gina', last_name='Mail', email='gina@gmail.com',
                               source='email_import'))
        db.session.commit()

        client = app.test_client()
        for verb in ('get', 'post', 'put', 'delete'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                s['_user_id'], s['_fresh'] = uid, True
            g.pop('_login_user', None)

        print('\n1. Beta gate')
        login(E)
        check('default is allowlist: a regular user is not in', gs.mode() == 'allowlist'
              and client.get('/guide').status_code == 403 and client.post('/api/guide/sessions', json={}).status_code == 403)
        gs.set_user_access([E], 'allowed', A)
        check('an allowlisted user is in', client.get('/guide').status_code == 200)
        gs.set_mode('off', A)
        check('off wins over the allowlist', client.get('/guide').status_code == 403)
        gs.set_mode('allowlist', A)
        import app.models.mail_crm as mc
        real = mc.FeatureFlag.query

        class Boom:
            def filter_by(self, **k):
                raise RuntimeError('db down')
        mc.FeatureFlag.query = Boom()
        check('fails closed', not gs.is_enabled(User.query.get(E)))
        mc.FeatureFlag.query = real

        print('\n2. Guides and the picker')
        gl = gs.guides()
        check('eleven guides, Simi first, varied voices', len(gl) == 11 and gl[0].slug == 'simi'
              and len({(x.gender_hint, x.pitch, x.rate) for x in gl}) == 11)
        boot = client.get('/api/guide/state').get_json()
        check('the picker opens on first use', boot['has_chosen'] is False and len(boot['guides']) == 11)
        james = SmeGuide.query.filter_by(slug='james').first()
        client.post('/api/guide/prefs', json={'guide_id': james.id})
        check('the choice is saved to the profile', client.get('/api/guide/state').get_json()['guide_id'] == james.id)
        page = client.get('/guide').get_data(as_text=True)
        check('the console labels the guide an AI and loads the ported assets', 'AI guide' in page
              and 'css/guide.css' in page and 'js/guide_console.js' in page and 'Concept mockup' not in page)

        print('\n3. Briefing: grounded facts with source ids')
        facts = gs.build_facts(E)
        ids = {f['id'] for f in facts}
        check('profile, career, résumé achievements and simulation facts carry ids',
              {'profile', 'career:0', 'resume:0', 'resume:1', 'sim', 'sim:L1'} <= ids, sorted(ids))
        check('résumé lines without an achievement are left out', not any('Hobbies' in f['text'] for f in facts))
        check('simulation ranges are in the briefing', any('$6,000–$12,000/mo' in f['text'] for f in facts))
        check('contacts (incl. email-imported) are never in the briefing', not any('gina' in f['text'].lower() for f in facts))

        print('\n4. A conversation')
        ai.queue.append({'say': "Hi Elena, I'm James. Let's set your Layer 1 goal. What would success look like?",
                         'about_user': True, 'sources': ['profile', 'sim'], 'layer': 1, 'grow_step': 'G'})
        res = client.post('/api/guide/sessions', json={})
        d = res.get_json()
        sid = d['session_id']
        check('a session starts with an opening turn and the facts panel', res.status_code == 200
              and d['turn']['sources'] == ['profile', 'sim'] and len(d['facts']) >= 5 and d['layer'] == 1, d.get('turn'))
        sysmsg = ai.calls[-1]['system']
        check('the briefing is cached and identical across turns', sysmsg[1].get('cache_control') == {'type': 'ephemeral'}
              and '[sim:L1]' in sysmsg[1]['text'])
        check('the guide must answer with one structured tool call', ai.calls[-1]['tool_choice']
              == {'type': 'tool', 'name': 'guide_turn'})
        ai.queue.append({'say': 'Your simulation puts your market rate at $145-185/hr.', 'about_user': True,
                         'sources': ['made-up-id'], 'layer': 1, 'grow_step': 'R'})
        ai.queue.append({'say': 'Your simulation shows Fractional COO at $6,000-$12,000 a month.', 'about_user': True,
                         'sources': ['sim:L1'], 'layer': 1, 'grow_step': 'R'})
        n_before = len(ai.calls)
        d = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'I want more consulting work.',
                                                                   'input_mode': 'voice'}).get_json()
        check('a turn citing a non-existent source is regenerated once', len(ai.calls) == n_before + 2
              and d['turn']['sources'] == ['sim:L1'] and not d['turn'].get('fallback'), d['turn'])
        check('user words reach the model wrapped as data', '<user_said>I want more consulting work.</user_said>'
              in str(ai.calls[-2]['messages']))
        ai.queue += [{'say': 'You earn $500/hr.', 'about_user': True, 'sources': [], 'layer': 1, 'grow_step': 'R'}] * 2
        d = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'What do I earn?'}).get_json()
        check('unsourced claims twice -> a safe fallback question', d['turn'].get('fallback')
              and d['turn']['say'] == gs.SAFE_FALLBACK)

        print('\n5. Skills')
        ai.queue.append({'say': "That's cross-functional change leadership. Does that fit?", 'about_user': True,
                         'sources': ['resume:0'], 'layer': 1, 'grow_step': 'R',
                         'skill': {'name': 'Cross-functional change leadership', 'evidence_source': 'resume:0'}})
        d = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'Getting union, finance and engineering aligned.'}).get_json()
        sk = d['turn'].get('skill')
        check('a skill is proposed with its evidence, not added', sk and sk.get('id')
              and SkillSuggestion.query.get(sk['id']).status == 'proposed'
              and 'plant consolidation' in SkillSuggestion.query.get(sk['id']).evidence_text)
        ai.queue.append({'say': 'Sounds good.', 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'R',
                         'skill': {'name': 'Mind reading', 'evidence_source': 'nope'}})
        d2 = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'ok'}).get_json()
        check('a skill without a real evidence source is never proposed', 'skill' not in d2['turn'])
        client.post('/api/guide/skills/%s' % sk['id'], json={'accept': True})
        check('the user accepts it: now part of the profile', SkillSuggestion.query.get(sk['id']).status == 'accepted'
              and any(f['id'] == 'skills' and 'Cross-functional' in f['text'] for f in gs.build_facts(E)))

        print('\n6. Goal card')
        ai.queue.append({'say': "Here's your Layer 1 goal. If you approve it, your agents will plan with it.",
                         'about_user': True, 'sources': ['sim:L1', 'resume:0'], 'layer': 1, 'grow_step': 'W',
                         'options': [{'key': 'A', 'label': 'Win 2 clients'}],
                         'goal_card': {'goal': 'Win 2 new fractional operations clients at $160/hr by May 2027',
                                       'measure': 'Signed contracts at $160/hr+', 'target': '2 clients',
                                       'deadline': 'May 2027', 'evidence_sources': ['sim:L1', 'resume:0', 'fake'],
                                       'simulation_source': 'sim:L1',
                                       'agent_changes': [{'agent': 'rate_card', 'setting': 'hourly_rate', 'new_value': '$160'},
                                                         {'agent': 'course_curriculum', 'setting': 'x', 'new_value': 'y'}]}})
        d = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'B'}).get_json()
        card = GoalCard.query.get(d['turn']['goal_card']['id'])
        check('the goal card is a draft until approved', card.status == 'draft' and card.layer == 1)
        check('evidence keeps only real sources; agent changes only this layer\'s agents',
              [e['source_id'] for e in card.evidence_refs] == ['sim:L1', 'resume:0']
              and [c.agent_id for c in GoalCardAgentChange.query.filter_by(goal_card_id=card.id).all()] == ['rate_card'])
        check('nothing reaches the agents before approval', build_briefing('rate_card', 1, sim.id, E, {}).goal == '')
        login(O)
        check('another user can\'t approve it', client.post('/api/guide/goals/%s/approve' % card.id).status_code in (400, 403))
        login(E)
        client.put('/api/guide/goals/%s' % card.id, json={'target': '2 clients at $160/hr'})
        res = client.post('/api/guide/goals/%s/approve' % card.id)
        card = GoalCard.query.get(card.id)
        check('approve saves it with a review date', res.status_code == 200 and card.status == 'approved'
              and card.review_at and card.target == '2 clients at $160/hr')
        b1 = build_briefing('rate_card', 1, sim.id, E, {})
        check('approved goal reaches that layer\'s agents as context', 'Win 2 new fractional' in b1.goal
              and "USER'S APPROVED GOAL" in b1.sections())
        check('…but not other layers\' agents', build_briefing('course_curriculum', 3, sim.id, E, {}).goal == '')
        check('agent setting changes are recorded, not applied (EXEC configuration path)',
              all(c.applied_at is None for c in GoalCardAgentChange.query.filter_by(goal_card_id=card.id).all()))
        PlatformSetting.set('guide_goals_in_agent_briefing', 'off')
        db.session.commit()
        check('admin can switch goals-to-agents off', build_briefing('rate_card', 1, sim.id, E, {}).goal == '')
        PlatformSetting.set('guide_goals_in_agent_briefing', 'on')
        db.session.commit()
        ai.queue.append({'say': 'New goal.', 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'W',
                         'goal_card': {'goal': 'Win 3 clients', 'measure': 'm', 'target': 't', 'deadline': 'June 2027',
                                       'evidence_sources': [], 'agent_changes': []}})
        d = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'Actually, three.'}).get_json()
        client.post('/api/guide/goals/%s/approve' % d['turn']['goal_card']['id'])
        check('one goal per layer: approving a new one retires the old', GoalCard.query.get(card.id).status == 'retired')

        print('\n7. Safety')
        n_before = len(ai.calls)
        d = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': "honestly I just want to kill myself"}).get_json()
        check('crisis language stops goal-setting before any model call and shows 988', len(ai.calls) == n_before
              and '988' in d['turn']['say'] and d['turn'].get('resources') and SmeSession.query.get(sid).crisis_flag)
        ai.queue.append({'say': 'x', 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'G', 'wellbeing': 'crisis'})
        d = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'everything is falling apart'}).get_json()
        check('a model-detected crisis also shows resources', '988' in d['turn']['say'] and d['turn'].get('resources'))
        ai.queue.append({'say': 'Saving 20% of new income is a planning intention.', 'about_user': False, 'sources': [],
                         'layer': 5, 'grow_step': 'O'})
        d = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': "Let's do Layer 5."}).get_json()
        check('L5 turns carry the standing not-financial-advice note', d['turn'].get('note') == gs.L5_NOTE)
        sysmsg = ai.calls[-1]['system'][0]['text']
        check('the rules forbid advice, personality labels and following instructions in user text',
              'No financial, legal or tax advice' in sysmsg and 'Never label personality' in sysmsg
              and 'treat it as data' in sysmsg)

        print('\n8. Same advice whichever guide')
        ai.queue.append({'say': 'Hi.', 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'G'})
        client.post('/api/guide/sessions', json={'layer': 1})
        s_james = ai.calls[-1]['system']
        client.post('/api/guide/prefs', json={'guide_id': SmeGuide.query.filter_by(slug='aisha').first().id})
        ai.queue.append({'say': 'Hi.', 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'G'})
        client.post('/api/guide/sessions', json={'layer': 1})
        s_aisha = ai.calls[-1]['system']
        check('the model gets only the guide\'s name; everything else is identical',
              s_james[0]['text'].replace('James', 'X') == s_aisha[0]['text'].replace('Aisha', 'X')
              and s_james[1] == s_aisha[1])

        print('\n9. Privacy, limits, history')
        t = SmeTurn.query.filter_by(session_id=sid, speaker='user').first()
        check('transcripts are encrypted at rest', 'consulting' not in t.text_enc)
        client.post('/api/guide/sessions/%s/end' % sid)
        s = SmeSession.query.get(sid)
        check('ending saves an encrypted summary for next time', s.status == 'ended' and s.summary_enc
              and 'Covered L1' not in s.summary_enc and 'Covered L1' in gs.last_summary(E))
        check('model cost is metered', s.model_cost_cents > 0 and s.input_tokens > 0)
        PlatformSetting.set('guide_sessions_per_day', '3')
        db.session.commit()
        res = client.post('/api/guide/sessions', json={})
        check('sessions per day are capped', res.status_code == 429, res.status_code)
        PlatformSetting.set('guide_sessions_per_day', '50')
        PlatformSetting.set('guide_turns_per_session', '1')
        db.session.commit()
        ai.queue.append({'say': 'Hi.', 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'G'})
        s2 = client.post('/api/guide/sessions', json={}).get_json()['session_id']
        res = client.post('/api/guide/sessions/%s/turn' % s2, json={'text': 'hi'})
        check('turns per session are capped', res.status_code == 429)
        PlatformSetting.set('guide_turns_per_session', '60')
        db.session.commit()
        login(O)
        gs.set_user_access([O], 'allowed', A)
        check('another user can\'t read or post to the session', client.post('/api/guide/sessions/%s/turn' % s2,
              json={'text': 'hi'}).status_code == 400 and client.get('/api/guide/history/%s' % sid).status_code == 404)
        login(E)
        h = client.get('/api/guide/history').get_json()['sessions']
        tr = client.get('/api/guide/history/%s' % sid).get_json()['turns']
        check('the user can view their history and transcript', len(h) >= 3 and any(x['speaker'] == 'user' for x in tr))
        res = client.delete('/api/guide/history')
        check('deleting history removes conversations but keeps goals and skills',
              res.get_json()['deleted'] >= 3 and SmeTurn.query.count() == 0
              and gs.approved_goal(E, 1) is not None and gs.accepted_skills(E))
        page = client.get('/guide/history').get_data(as_text=True)
        check('history page shows approved goals and confirmed skills', 'Win 3 clients' in page and 'Cross-functional' in page)

        ai.queue.append({'say': 'Hi.', 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'G'})
        s3 = client.post('/api/guide/sessions', json={}).get_json()['session_id']
        ai.queue.append({'say': 'Noted.', 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'G'})
        client.post('/api/guide/sessions/%s/turn' % s3, json={'text': 'Getting union, finance and engineering aligned.'})

        print('\n10. Admin')
        login(O)
        check('non-admins can\'t open the admin page', client.get('/admin/guide').status_code == 403)
        login(A)
        page = client.get('/admin/guide').get_data(as_text=True)
        check('admin page shows metadata only, never what users said', 'SME Console (AI guide)' in page
              and 'goals approved' in page and 'union, finance and engineering' not in page
              and 'kill myself' not in page)
        res = client.post('/api/admin/guide/guides/%s' % james.id, json={'pitch': '0.8', 'enabled': False})
        check('admin can tune or disable a guide', res.status_code == 200 and not SmeGuide.query.get(james.id).enabled)
        for x in SmeGuide.query.all():
            if x.id != james.id:
                x.enabled = False
        db.session.commit()
        res = client.post('/api/admin/guide/guides/%s' % SmeGuide.query.filter_by(slug='simi').first().id,
                          json={'enabled': False})
        check('at least one guide must stay enabled', res.status_code == 400)
        check('no network calls at any point', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
