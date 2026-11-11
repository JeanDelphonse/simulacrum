"""SME Console guide: answering questions about the member's CRM contacts.

The model is faked (as in test_guide.py); the test checks what reaches it.

    python test_guide_crm.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail and not condition else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


class FakeAI:
    def __init__(self):
        self.queue, self.calls = [], []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if 'tools' not in kw:
            return SimpleNamespace(content=[SimpleNamespace(type='text', text='Summary.')], usage=usage)
        out = self.queue.pop(0) if self.queue else {'say': 'What would success look like?', 'about_user': False,
                                                   'sources': [], 'layer': 1, 'grow_step': 'G'}
        return SimpleNamespace(content=[SimpleNamespace(type='tool_use', input=out)], usage=usage)


def system_text(call) -> str:
    return '\n'.join(b['text'] for b in call['system'])


def main():
    import requests
    network = []
    requests.post = requests.get = (lambda *a, **k: network.append(a[0] if a else k)
                                    or (_ for _ in ()).throw(AssertionError('network')))
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    from cryptography.fernet import Fernet
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', ENCRYPTION_KEY=Fernet.generate_key().decode())

    with app.app_context():
        from app import models as _m  # noqa: F401
        from app.models import sme_guide as _sg, bayesian as _b  # noqa: F401
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.profile import UserProfile
        from app.models.contact import Contact
        from app.models.simulation import Simulation
        from app.models.outreach_email import EmailLog
        from app.models.mail_crm import ContactAccountLink
        from app.models.platform_settings import PlatformSetting
        from app.services import guide_service as gs, guide_crm

        ai = FakeAI()
        gs.AI_CLIENT = ai
        now = datetime.utcnow()
        E, OTHER = 'MEMBRGC01', 'OTHERGC01'
        for uid, name in ((E, 'Elena Vasquez'), (OTHER, 'Olu Other')):
            db.session.add(User(id=uid, email='%s@t.local' % uid.lower(), full_name=name, password_hash='x',
                                email_verified=True, onboarding_completed_at=now, is_admin=(uid == E)))
            db.session.add(UserProfile(user_id=uid, username=uid.lower(), display_name=name))
        sim = Simulation(id=generate_id(), user_id=E, name='Ops path', status='complete')
        db.session.add(sim)
        db.session.commit()

        def contact(first, last, company, **kw):
            c = Contact(id=generate_id(), user_id=kw.pop('user_id', E), first_name=first, last_name=last,
                        email='%s.%s@example.com' % (first.lower(), last.lower()), phone='+1 512 555 0100',
                        company_name=company, job_title=kw.pop('job_title', 'Director of Operations'),
                        source=kw.pop('source', 'manual_entry'), pipeline_stage=kw.pop('stage', 'prospect'),
                        notes='Private note about pricing', linkedin_url='https://linkedin.com/in/x', **kw)
            db.session.add(c)
            db.session.flush()
            return c

        ana = contact('Ana', 'Ruiz', 'Acme Robotics', stage='active', last_contacted_at=now - timedelta(days=10),
                      outreach_count=2)
        ben = contact('Ben', 'Okafor', 'Northwind', stage='active', last_contacted_at=now - timedelta(days=12),
                      outreach_count=1)
        db.session.add(EmailLog(id=generate_id(), simulation_id=sim.id, contact_id=ben.id, subject='s',
                                from_email='f@x.com', from_name='F', to_email=ben.email, status='sent',
                                sent_at=now - timedelta(days=12), replied_at=now - timedelta(days=11)))
        dee = contact('Dee', 'Moss', 'Globex', do_not_contact=True, last_contacted_at=now - timedelta(days=20),
                      outreach_count=1)
        gina = contact('Gina', 'Mail', 'Gmailco', source='email_import')
        lou = contact('Lou', 'Linked', 'Linkedco')
        db.session.add(ContactAccountLink(contact_id=lou.id, account_id='ACCOUNT01'))
        zed = contact('Zed', 'Archived', 'Oldco', is_archived=True)
        contact('Oscar', 'Others', 'Theirco', user_id=OTHER)
        for i in range(14):
            contact('Pat%02d' % i, 'Filler', 'Filler Inc %d' % i, last_contacted_at=now - timedelta(days=1, minutes=i))
        kess = contact('Kim', 'Kessler', 'Kessler Manufacturing', job_title='Plant Manager')
        db.session.commit()

        print('\n1. What the guide may see')
        facts = guide_crm.briefing_facts(E)
        blob = json.dumps(facts)
        summary = next(f for f in facts if f['id'] == 'crm')
        check('a CRM summary: counts by stage, recent activity, Do Not Contact',
              '18 contacts' in summary['text'] and 'active 2' in summary['text'] and '1 marked Do Not Contact' in summary['text'],
              summary['text'])
        check('never email addresses, phone numbers, profile links or notes',
              '@example.com' not in blob and '555' not in blob and 'linkedin.com' not in blob and 'Private note' not in blob)
        check('never contacts imported from or linked to email (Gmail Limited Use)',
              'Gina' not in blob and 'Lou' not in blob and 'Gmailco' not in blob)
        check("never archived contacts or another member's", 'Zed' not in blob and 'Oscar' not in blob)
        fu = next((f for f in facts if f['id'] == 'crm:followups'), {'text': ''})
        check('follow-ups due: contacted a week+ ago, no reply, not Do Not Contact',
              'Ana Ruiz' in fu['text'] and 'Ben' not in fu['text'] and 'Dee' not in fu['text'], fu['text'])
        cfacts = {f['id']: f for f in facts if f['kind'] == 'contact'}
        check('the most active contacts are listed, at most 12', 0 < len(cfacts) <= guide_crm.TOP_CONTACTS, len(cfacts))
        dee_f = guide_crm.contact_fact(dee, False)
        check('a Do Not Contact contact is marked as such', 'DO NOT CONTACT' in dee_f['text'])
        ben_f = guide_crm.contact_fact(ben, True)
        check('a contact fact carries role, company, stage and activity',
              all(t in ben_f['text'] for t in ('Director of Operations at Northwind', 'stage: active', 'last contacted', 'replied')),
              ben_f['text'])

        print('\n2. In a conversation')
        # E is an admin, so the guide is open to them (mode allowlist).
        client = app.test_client()
        with client.session_transaction() as s:
            s['_user_id'], s['_fresh'] = E, True
        g.pop('_login_user', None)
        ai.queue.append({'say': 'Hi Elena.', 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'G'})
        d = client.post('/api/guide/sessions?sim=%s' % sim.id, json={'layer': 1}).get_json()
        sys_txt = system_text(ai.calls[-1])
        check('the briefing sent to the model includes the CRM', '[crm] CRM summary' in sys_txt and 'Ana Ruiz' in sys_txt)
        check('the rules tell the guide how to use it', 'CONTACTS (CRM)' in sys_txt and 'DO NOT CONTACT' in sys_txt
              and 'never guess them' in sys_txt)
        check('no contact email or phone reaches the model', '@example.com' not in sys_txt and '555 0100' not in sys_txt)
        check('Kim Kessler is not in the top-12 briefing', 'Kessler' not in sys_txt)
        sid = d['session_id']
        g.pop('_login_user', None)
        ai.queue.append({'say': 'Kim Kessler is the Plant Manager at Kessler Manufacturing; not contacted yet.',
                         'about_user': True, 'sources': ['contact:%s' % kess.id], 'layer': 1, 'grow_step': 'R'})
        r = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'What do I have on Kessler Manufacturing?'})
        t = (r.get_json() or {}).get('turn', {})
        sys_txt = system_text(ai.calls[-1])
        check('naming a company looks the contact up for that answer', 'Kessler Manufacturing' in sys_txt
              and ('contact:%s' % kess.id) in sys_txt)
        check('an answer citing the contact passes the grounding check', not t.get('fallback') and 'Kim Kessler' in t.get('say', ''), t)
        g.pop('_login_user', None)
        ai.queue.append({'say': "I don't see Gina in your CRM.", 'about_user': False, 'sources': [], 'layer': 1, 'grow_step': 'R'})
        client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'Tell me about Gina Mail'})
        check('an email-imported contact is never looked up', 'Gmailco' not in system_text(ai.calls[-1])
              and 'Gina' not in system_text(ai.calls[-1]).split('BRIEFING')[1])
        g.pop('_login_user', None)
        ai.queue += [{'say': 'Ana has 3 kids.', 'about_user': True, 'sources': [], 'layer': 1, 'grow_step': 'R'}] * 3
        r = client.post('/api/guide/sessions/%s/turn' % sid, json={'text': 'Who is Ana?'})
        check('an uncited claim about a contact is refused (safe fallback)', (r.get_json() or {}).get('turn', {}).get('fallback'))

        print('\n3. Switch and notice')
        PlatformSetting.set('guide_crm_access', 'off')
        check('Admin can switch CRM access off', guide_crm.briefing_facts(E) == [] and guide_crm.lookup_facts(E, 'Kessler') == [])
        PlatformSetting.set('guide_crm_access', 'on')
        g.pop('_login_user', None)
        page = client.get('/guide?sim=%s' % sim.id).get_data(as_text=True)
        check('the console tells members what the guide can see', 'your CRM contacts' in page
              and 'never email addresses, phone numbers or notes' in page and 'imported from your email are never shared' in page)
        check('the setting is listed for admins', 'guide_crm_access' in gs.DEFAULTS)
        check('no network calls', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
