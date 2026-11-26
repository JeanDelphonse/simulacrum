"""Member pronouns: the setting, the prompt rule, and fixing text already written.

    python test_pronouns.py
"""
import os
import re
import sys
from datetime import datetime

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


class _Msg:
    def __init__(self, text):
        self.content = [type('C', (), {'text': text})()]
        self.usage = None
        self.stop_reason = 'end_turn'


class FakeClient:
    """Stands in for the model: swaps her/she -> his/he (or does something worse)."""
    def __init__(self, mode='good'):
        self.mode = mode
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        text = kw['messages'][0]['content'].split('TEXT:\n', 1)[1]
        if self.mode == 'good':
            text = re.sub(r'\bpositions her\b', 'positions him', text)
            text = re.sub(r'\bHer\b', 'His', text)
            text = re.sub(r'\bher\b', 'his', text)
            text = re.sub(r'\bShe\b', 'He', text)
            text = re.sub(r'\bshe\b', 'he', text)
        elif self.mode == 'embellish':
            text = text.replace('her', 'his').replace('Her', 'His') + ' He is world-class.'
        return _Msg(text)


def main():
    import app.services.pronouns as pr

    # ── the rule ────────────────────────────────────────────────────────────
    check('normalize accepts codes and labels', pr.normalize('he') == 'he' and pr.normalize('She/Her') == 'she'
          and pr.normalize('they/them') == 'they' and pr.normalize('') == '' and pr.normalize('xe') == '')
    r = pr.prompt_rule(name='Jean', code='he')
    check('stated: names the pronouns', 'he/him' in r and 'he, him, his, himself' in r and 'Jean' in r, r)
    r = pr.prompt_rule(name='Jean', code='')
    check('not stated: forbids inferring gender from the name, asks for name or they/them',
          'Do not infer gender from the name' in r and 'they/them' in r and 'never use he/him or she/her' in r, r)

    # ── the guard ───────────────────────────────────────────────────────────
    old = "Jean's experience positions her perfectly. Her track record makes her expertise valuable."
    new ="Jean's experience positions him perfectly. His track record makes his expertise valuable."
    check('needs_fix: she/her text for a he/him member', pr.needs_fix(old, 'he') and not pr.needs_fix(new, 'he'))
    check('needs_fix: nothing to do without a choice', not pr.needs_fix(old, ''))
    check('guard: a pronoun-only edit passes', pr.safe_rewrite(old, new, 'he'))
    check('guard: an added claim is rejected', not pr.safe_rewrite(old, new + ' He is world-class.', 'he'))
    check('guard: a reworded sentence is rejected',
          not pr.safe_rewrite(old, new.replace('perfectly', 'ideally'), 'he'))
    check('guard: wrong pronouns left behind are rejected',
          not pr.safe_rewrite(old, new.replace('His track', 'Her track'), 'he'))
    check('guard: they/them may change verb agreement',
          pr.safe_rewrite('She leads the team and she is based in Boston. She carries it.',
                          'They lead the team and they are based in Boston. They carry it.', 'they'))
    check('guard: but not other words',
          not pr.safe_rewrite('She leads the team.', 'They manage the team.', 'they'))
    check('guard: he/him gets no verb leeway', not pr.safe_rewrite('She is here.', 'He are here.', 'he'))

    # ── the app ─────────────────────────────────────────────────────────────
    from app import create_app
    from app.extensions import db, bcrypt
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        db.create_all()
        from app.models.user import User
        from app.models.profile import UserProfile
        from app.models.simulation import IncomeStream, Simulation, SimulationLayer
        now = datetime.utcnow()
        user = User(id=generate_id(), email='jean@example.com', full_name='Jean Doe',
                    password_hash=bcrypt.generate_password_hash('pw12345678').decode(),
                    email_verified=True, onboarding_completed_at=now)
        db.session.add(user)
        db.session.commit()
        prof = UserProfile(id=generate_id(), user_id=user.id, username='jeandoe', display_name='Jean Doe',
                           bio='Jean Doe is a program manager. She led platform work at Adobe.')
        db.session.add(prof)
        sim = Simulation(id=generate_id(), user_id=user.id, name='S', expertise_zone='AI/ML', status='complete')
        db.session.add(sim)
        db.session.flush()
        layer = SimulationLayer(id=generate_id(), simulation_id=sim.id, layer_number=1, layer_name='Active',
                                ai_narrative=old)
        db.session.add(layer)
        db.session.flush()
        stream = IncomeStream(id=generate_id(), layer_id=layer.id, name='Consulting',
                              description='Fractional advisory work.', ai_reasoning='Her Adobe programs justify it.')
        other = User(id=generate_id(), email='x@example.com', full_name='Alex Roe',
                     password_hash='x', email_verified=True, onboarding_completed_at=now)
        db.session.add_all([stream, other])
        db.session.commit()
        osim = Simulation(id=generate_id(), user_id=other.id, name='O', expertise_zone='Ops', status='complete')
        db.session.add(osim)
        db.session.flush()
        olayer = SimulationLayer(id=generate_id(), simulation_id=osim.id, layer_number=1, layer_name='A',
                                 ai_narrative='Her work positions her well.')
        db.session.add(olayer)
        db.session.commit()

        check('prompt rule reads the stored choice', 'has not stated pronouns' in pr.prompt_rule(user.id))
        prof.pronouns = 'he'
        db.session.commit()
        check('…and once set, names it with the first name', pr.prompt_rule(user.id).startswith('PRONOUNS: Jean uses he/him'))

        import app.services.claude as cl
        import app.services.bio_service as bs
        import app.blueprints.bio_chat.routes as bc
        check('simulation layers + agents get the rule', 'he/him' in cl._pronoun_rule(user.id))
        check('bio generation gets the rule', 'he/him' in bs._pronoun_rule(prof))
        check('public bio chat gets the rule', 'Jean uses he/him' in bc._pronoun_rule(user.id, 'Jean'))
        src = open('app/services/claude.py', encoding='utf-8').read()
        check('rule is in the layer, refine and agent prompts', src.count('{_pronoun_rule(user_id)}') == 2 and src.count('+ _pronoun_rule(user_id)') == 1)

        fake = FakeClient('good')
        stats = pr.fix_existing(user.id, 'he', client=fake)
        db.session.expire_all()
        check('fix: narrative, reasoning and bio rewritten', stats['fixed'] == 3 and stats['skipped'] == 0, stats)
        check('…layer narrative now uses him/his',
              SimulationLayer.query.get(layer.id).ai_narrative == new, SimulationLayer.query.get(layer.id).ai_narrative)
        check('…bio too', UserProfile.query.get(prof.id).bio.endswith('He led platform work at Adobe.'))
        check('…text without pronouns is not sent to the model',
              all('Fractional advisory' not in c['messages'][0]['content'] for c in fake.calls))
        check('…another member\'s text is untouched',
              SimulationLayer.query.get(olayer.id).ai_narrative == 'Her work positions her well.')
        check('…the model is Haiku, temperature 0', fake.calls[0]['model'].startswith('claude-haiku')
              and fake.calls[0]['temperature'] == 0)

        layer2 = SimulationLayer.query.get(layer.id)
        layer2.ai_narrative = old
        db.session.commit()
        stats = pr.fix_existing(user.id, 'he', client=FakeClient('embellish'))
        db.session.expire_all()
        check('fix: an edit that adds words is discarded, text kept as is',
              stats['skipped'] == 1 and SimulationLayer.query.get(layer.id).ai_narrative == old, stats)

        # ── settings ────────────────────────────────────────────────────────
        started = []
        pr.fix_existing_async = lambda a, uid, code: started.append((uid, code))
        prof = UserProfile.query.get(prof.id)
        prof.pronouns = None
        db.session.commit()
        client = app.test_client()
        res = client.post('/api/auth/login', json={'email': 'jean@example.com', 'password': 'pw12345678'})
        check('login', res.status_code == 200, res.status_code)
        html = client.get('/settings/profile').get_data(as_text=True)
        check('settings: pronouns field, "Not stated" by default', 'id="f-pronouns"' in html
              and '<option value="" selected>Not stated</option>' in html)
        res = client.put('/api/settings/profile', json={'pronouns': 'he'})
        check('save he/him', res.status_code == 200 and res.get_json()['pronouns'] == 'he'
              and UserProfile.query.get(prof.id).pronouns == 'he', res.status_code)
        check('…starts the background fix once', started == [(user.id, 'he')], started)
        client.put('/api/settings/profile', json={'pronouns': 'he', 'location': 'Boston'})
        check('…not again when unchanged', len(started) == 1, started)
        html = client.get('/settings/profile').get_data(as_text=True)
        check('settings shows the saved choice', '<option value="he" selected>he/him</option>' in html)
        res = client.put('/api/settings/profile', json={'pronouns': 'xe'})
        check('unknown value rejected', res.status_code == 400 and UserProfile.query.get(prof.id).pronouns == 'he')
        res = client.put('/api/settings/profile', json={'pronouns': ''})
        check('clearing goes back to not stated, no rewrite',
              res.status_code == 200 and UserProfile.query.get(prof.id).pronouns is None and len(started) == 1)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
