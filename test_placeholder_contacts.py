"""Agents never put placeholder people into the CRM.

When Cold Email had no real prospects, the model filled its list with template
rows like "[First Name 1] [Last Name 1] [Enterprise Company 1]", which were
saved as contacts. Runs on in-memory SQLite with a fake model client; nothing
is sent.

    python test_placeholder_contacts.py
"""
import json
import os
import sys
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
    def __init__(self, artifact):
        self.calls, self._artifact = [], artifact
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        planning = kw['messages'][0]['content'].startswith('You are planning')
        text = 'OBJECTIVE: x\nOUTLINE:\n1. A — b' if planning else self._artifact
        return SimpleNamespace(content=[SimpleNamespace(text=text)],
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                               stop_reason='end_turn')


def main():
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SENDGRID_API_KEY'] = None

    with app.app_context():
        db.create_all()
        from app.models.user import User
        from app.models.simulation import Simulation
        from app.models.contact import Contact
        from app.services import claude
        from app.services.contact_lookup import is_placeholder_contact, save_contacts_to_crm
        from app.services.outreach_email_service import send_outreach_email

        claude._log_interaction = lambda *a, **k: None

        print('\n1. Detection')
        placeholders = [
            ('[First Name 1]', '[Last Name 1]', '[Enterprise Company 1]', ''),
            ('First Name', 'Last Name', 'Company', ''),
            ('Prospect 3', '', '', ''), ('Contact', 'Name', '', ''), ('Hiring Manager', '', '', ''),
            ('John', 'Doe', '', ''), ('TBD', '', '', ''), ('{{first_name}}', '', '', ''),
            ('Ana', 'Ruiz', 'Enterprise Company 2', ''),
            ('Ana', 'Ruiz', '', 'ana@example.com'), ('Ana', 'Ruiz', '', 'firstname.lastname@acme.com'),
        ]
        real = [
            ('Ana', 'Ruiz', 'Acme Corp', 'ana@acme.com'), ('Tom', 'Lead', 'Lead Co', 'tom@lead.co'),
            ('Sam', 'Lee', 'The Firm', 'sam@email.com'), ('Pat', 'Client', '', 'contact@realco.com'),
            ('Jo', 'Test', 'Testa Inc', 'jo@testa.com'), ('Maria', 'Name', '', ''),
        ]
        missed = [p for p in placeholders if not is_placeholder_contact(*p)]
        check('every template row is caught', not missed, missed)
        wrong = [r for r in real if is_placeholder_contact(*r)]
        check('real people are not (surnames Lead/Client/Test, @email.com, contact@)', not wrong, wrong)

        user = User(id=generate_id(), email='o@x.com', full_name='O', password_hash='x')
        sim = Simulation(id=generate_id(), user_id=user.id, name='S', expertise_zone='Ops',
                         status='complete')
        db.session.add_all([user, sim])
        db.session.commit()

        print('\n2. CRM writes')
        saved = save_contacts_to_crm(
            [{'first_name': '[First Name 1]', 'last_name': '[Last Name 1]',
              'company_name': '[Enterprise Company 1]', 'email': 'first.last@enterprise1.com'},
             {'first_name': 'Ana', 'last_name': 'Ruiz', 'company_name': 'Acme',
              'email': 'ana@acme.com'}],
            user_id=user.id, simulation_id=sim.id, action_type='referral_network')
        names = {c.first_name for c in Contact.query.filter_by(user_id=user.id)}
        check('save_contacts_to_crm keeps the real person, drops the template row',
              saved == 1 and names == {'Ana'}, names)

        out = claude._finalize_outreach_email_artifact(json.dumps({'prospects': [
            {'first_name': '[First Name 2]', 'last_name': '[Last Name 2]',
             'company_name': '[Enterprise Company 2]', 'email': 'x@enterprisecompany2.com',
             'sequence': [{'step': 1, 'subject': 's', 'body': 'b'}]},
            {'first_name': 'Bo', 'last_name': 'Kim', 'company_name': 'Kim Co',
             'email': 'bo@kim.co', 'sequence': [{'step': 1, 'subject': 's', 'body': 'b'}]}]}),
            user.id, sim.id, 'a1')
        kept = [p['first_name'] for p in json.loads(out)['prospects']]
        check('cold email artifact loses its placeholder prospects', kept == ['Bo'], kept)
        check('…and they never reach the CRM',
              not Contact.query.filter(Contact.first_name.like('[%')).count())

        print('\n3. Cold Email with no real prospects')
        db.session.add(Contact(id=generate_id(), user_id=user.id, first_name='[First Name 9]',
                               last_name='[Last Name 9]', email='p9@enterprisecompany9.com',
                               pipeline_stage='prospect', source='agent_generated'))
        db.session.commit()
        section = claude._get_crm_prospect_section(user.id, sim.id, {}, 5)
        check('placeholder rows already in the CRM are not offered as prospects',
              '[First Name 9]' not in section)

        for c in Contact.query.filter_by(user_id=user.id).all():
            c.outreach_count = 1        # use up the real CRM pool
        db.session.commit()
        fake = FakeClient('should not be used')
        claude._client = lambda: fake
        art = claude._execute_agent_action_body(
            action_type='cold_email_campaign', layer_number=1, expertise_zone='Ops',
            parsed_text='', user_inputs={}, user_id=user.id, simulation_id=sim.id,
            action_id=None, prospect_count=5, model='m')
        data = json.loads(art)
        check('no model call when there is nobody to email', not fake.calls, len(fake.calls))
        check('returns an empty campaign with a reason',
              data['prospects'] == [] and 'No new prospects' in data.get('empty_reason', ''))

        print('\n4. Other agents and the send path')
        extracted = []
        claude._llm_extract_contacts = lambda text: extracted.append(text) or [
            {'first_name': 'Prospect 1', 'last_name': 'X'}]
        fake = FakeClient('Referral plan with [First Name] placeholders and no contacts block')
        claude._client = lambda: fake
        claude._execute_agent_action_body(
            action_type='affiliate_program', layer_number=3, expertise_zone='Ops',
            parsed_text='', user_inputs={}, user_id=user.id, simulation_id=sim.id,
            action_id=None, prospect_count=5, model='m')
        check('no Haiku contact harvesting when the agent was given no people', not extracted)
        system = fake.calls[-1].get('system', '')
        check('shared rule forbids placeholders in contact lists',
              'never in a prospect, contact or recipient list' in system)

        ph = Contact.query.filter(Contact.first_name == '[First Name 9]').first()
        res = send_outreach_email(sim.id, ph.id, 'Hi', '<p>x</p>', 'me@x.com', 'Me')
        check('a placeholder contact is never emailed, even if scheduled',
              res.get('reason') == 'placeholder_contact', res)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
