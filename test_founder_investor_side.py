"""Founder Track investor connection, founder side (FOUNDER_PLAN.md WP8 part B).

Faked Claude and SendGrid; requests blocked; in-memory SQLite.

  * /founder/v/<id>/investors and its APIs 404 while the investor switch is
    off, without the counsel sign-off, or for a blocked user; unpaid redirects.
  * The offering exemption must be recorded (owner or legal holder) before any
    L5 step: matching, warm paths, drafts and sends refuse until then.
  * Under 506(b) a first contact to someone the founder doesn't know is
    refused, while a published application and an intro to the founder's own
    contact go through after approval; under 506(c) the first contact sends.
  * The first-contact cap, "negotiate never", advisor-only and the daily cap
    are enforced; an above-level attempt holds the draft and raises a flag.
  * Sends go through send_outreach_email: reply-to the founder, founder's name
    as display name, the on-behalf note, a member-scoped unsubscribe (also in
    List-Unsubscribe) and the postal footer; level_applied is set.
  * Warm paths are deterministic and the faked LLM never receives data from
    email-imported contacts; revoking contacts consent stops warm paths.
  * The 37% calibration marks the right share; the suggestion beats the bar.
  * Investor updates need approval by the decision holder, go only to opted-in
    or invited investors with access, and their unsubscribe is member-scoped.
  * LinkedIn drafts are never sent.

    python test_founder_investor_side.py
"""
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []
NEW_PY = ['app/services/founder_investors.py', 'app/blueprints/founder/investors.py']

DECK = ("Acme Dental cuts no-shows for dental clinics.\n"
        "Twelve clinics run our reminder pilot today.\n"
        "Clinics lose $40,000 a year to missed appointments.\n")


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


class FakeClaude(object):
    """Answers each forced tool by name and records every prompt."""

    def __init__(self):
        self.calls = []
        self.messages = self
        self.app_answers = None
        self.dirty_once = False

    def create(self, **kw):
        tool = kw['tools'][0]['name']
        text = kw['system'] + '\n' + kw['messages'][0]['content'][0]['text']
        self.calls.append({'tool': tool, 'text': text})
        if tool == 'deliver_intro':
            out = {'subject': 'Intro to Seed Fund?',
                   'request': 'Hi {first_name},\n\nWould you introduce me to the team at Seed Fund?\n\nThanks',
                   'blurb': 'Acme Dental cuts no-shows for dental clinics. Twelve clinics run the pilot.'}
            if self.dirty_once:
                self.dirty_once = False
                out['request'] += '\nInvestors will make 10x returns.'
        elif tool == 'deliver_answers':
            out = {'answers': self.app_answers or []}
        else:
            out = {'subject': 'Acme Dental: November update',
                   'body': 'Hi all,\n\nWe finished two plan steps and twelve clinics run the pilot.\n\nThanks,\nFran'}
        block = SimpleNamespace(type='tool_use', id='t%d' % len(self.calls), name=tool, input=out)
        return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=10))


def main():
    import requests
    requests.post = requests.get = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
    sent = []

    class FakeResponse(object):
        def __init__(self, n):
            self.headers = {'X-Message-Id': 'msg-%d' % n}

    class FakeSendGrid(object):
        def __init__(self, api_key=None, *a, **k):
            self.api_key = api_key

        def send(self, message):
            sent.append(message.get())
            return FakeResponse(len(sent))
    import sendgrid
    sendgrid.SendGridAPIClient = FakeSendGrid

    print('\n1. New files are Python 3.9 safe')
    missing = [f for f in NEW_PY
               if 'from __future__ import annotations' not in io.open(os.path.join(ROOT, f), encoding='utf-8').read()]
    check('every new .py file has "from __future__ import annotations"', not missing, missing)

    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      SENDGRID_API_KEY='SG.platform-key', MAIL_DEFAULT_SENDER='simi@simisi.io')
    from cryptography.fernet import Fernet
    app.config['ENCRYPTION_KEY'] = Fernet.generate_key().decode()

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.contact import Contact
        from app.models.founder import (ComplianceProfile, EvaluationRun, FounderVenture, IntroPath, InvestorAccess,
                                        InvestorProfile, InvestorTarget, InvestorUpdate, StartupPage, VentureArtifact,
                                        VentureMember, VentureOutreachAction)
        from app.models.mail_crm import ContactAccountLink, EmailAccount, UserSuppression, email_hash
        from app.models.observability import ObservabilityFlag
        from app.models.outreach_email import EmailLog, EmailSuppression
        from app.models.audit_log import AuditLog
        from app.models.user import User
        from app.services import (founder_alerts, founder_autonomy, founder_config, founder_feature,
                                  founder_investors as fi, founder_ventures, legal_text, sender_identity)
        now = datetime.utcnow()

        def user(email, name, admin=False):
            u = User(id=generate_id(), email=email, full_name=name, password_hash='x', is_admin=admin,
                     email_verified=True, onboarding_completed_at=now)
            db.session.add(u)
            db.session.commit()
            return u

        founder = user('fran@acme.dev', 'Fran Founder')
        cofounder = user('cam@acme.dev', 'Cam Cofounder')
        other = user('olly@other.dev', 'Olly Other')
        admin = user('admin@simisi.io', 'Ada Admin', admin=True)
        F_ID, C_ID, O_ID, A_ID = founder.id, cofounder.id, other.id, admin.id
        sender_identity.set_postal_address(founder, '1 Market St, San Francisco, CA 94105')
        v = founder_ventures.create_venture(founder, 'Acme Dental', 'Fewer no-shows for dental clinics', 'seed')
        V_ID = v.id
        db.session.add(VentureMember(venture_id=V_ID, user_id=C_ID, role='cofounder', status='active', joined_at=now))
        db.session.add(VentureArtifact(venture_id=V_ID, kind='pitch_deck', version=1, is_current=True,
                                       filename='deck.pdf', text_content=DECK, char_count=len(DECK), uploaded_by=F_ID))
        db.session.add(EvaluationRun(venture_id=V_ID, status='done', scores={}, finished_at=now))
        v.paid_at, v.rung = now, 4
        unpaid = founder_ventures.create_venture(other, 'Other Co', '', 'idea')
        db.session.commit()

        fake = FakeClaude()
        fi.AI_CLIENT = fake

        client = app.test_client()
        for verb in ('get', 'post'):
            orig = getattr(client, verb)
            setattr(client, verb, (lambda o: lambda *a, **k: (g.pop('_login_user', None), o(*a, **k))[1])(orig))

        def login(uid):
            with client.session_transaction() as s:
                if uid:
                    s['_user_id'], s['_fresh'] = uid, True
                else:
                    s.pop('_user_id', None)

        def venture():
            return db.session.get(FounderVenture, V_ID)

        def fresh(model, rid):
            db.session.expire_all()
            return db.session.get(model, rid)

        page = '/founder/v/%s/investors' % V_ID
        api = '/api/founder/v/%s/investors' % V_ID

        print('\n2. Hidden unless the investor switch is on with the counsel sign-off')
        login(F_ID)
        founder_feature.set_mode('founder_track', 'on', A_ID)
        check('page 404 while founder_investor is off', client.get(page).status_code == 404)
        ws = client.get('/founder/v/%s' % V_ID).get_data(as_text=True)
        check('no Investors tab in the venture nav while the switch is off', page not in ws)
        check('API 404 while founder_investor is off', client.post(api + '/match').status_code == 404)
        refused = False
        try:
            founder_feature.set_mode('founder_investor', 'on', A_ID)
        except founder_feature.FounderError:
            refused = True
        check('the switch refuses to turn on without the counsel sign-off', refused)
        check('page still 404 without counsel sign-off', client.get(page).status_code == 404)
        founder_config.set('fnd_counsel_signoff_at', '2026-11-01', A_ID)
        check('page 404 with the counsel date but the switch off', client.get(page).status_code == 404)
        founder_feature.set_mode('founder_investor', 'on', A_ID)
        r = client.get(page)
        html = r.get_data(as_text=True)
        check('page 200 once the switch is on and counsel signed off', r.status_code == 200, r.status_code)
        ws = client.get('/founder/v/%s' % V_ID).get_data(as_text=True)
        check('the venture nav shows the Investors tab once the switch is on', page in ws)
        check('page asks for the exemption and says to ask a lawyer',
              'Start with your offering exemption' in html and 'Ask your lawyer' in html)
        check('page carries the not-advice legal text', legal_text.get('founder_not_advice')[:40] in html)
        check('page has the Investors tab', 'aria-current="page">Investors<' in html)
        founder_feature.set_user_access([F_ID], 'blocked', A_ID, key='founder_investor')
        check('a blocked user gets 404', client.get(page).status_code == 404)
        founder_feature.set_user_access([F_ID], 'follow_global', A_ID, key='founder_investor')
        login(O_ID)
        check('a non-member gets 403', client.get(page).status_code == 403)
        r = client.get('/founder/v/%s/investors' % unpaid.id)
        check('an unpaid venture redirects to checkout', r.status_code == 302 and 'checkout' in r.headers['Location'],
              r.status_code)
        check('an unpaid venture API answers 402',
              client.post('/api/founder/v/%s/investors/match' % unpaid.id).status_code == 402)
        login(None)
        check('anonymous gets 404', client.get(page).status_code == 404)
        login(F_ID)

        print('\n3. Exemption before any L5 step')
        r = client.post(api + '/match')
        check('matching refuses (409) before the exemption is recorded', r.status_code == 409, r.get_json())
        check('drafting an update refuses before the exemption', client.post(api + '/updates').status_code == 409)
        pre = VentureOutreachAction(venture_id=V_ID, action_type='intro_request', level_required=3, status='approved',
                                    approved_by=F_ID, target_ref='email:someone@fund.vc', subject='Hi', body='Hello')
        db.session.add(pre)
        db.session.commit()
        try:
            founder_autonomy.send(pre, db.session.get(User, F_ID))
            ok = False
        except founder_autonomy.SendBlocked as exc:
            ok = exc.status == 409 and 'exemption' in str(exc)
        check('send refuses before the exemption is recorded', ok)
        check('nothing reached SendGrid', not sent)
        db.session.delete(pre)
        db.session.commit()
        login(C_ID)
        r = client.post(api + '/exemption', json={'exemption': '506b'})
        check('a co-founder without the legal decision right cannot record it', r.status_code == 403, r.get_json())
        login(F_ID)
        r = client.post(api + '/exemption', json={'exemption': 'reg-cf'})
        check('an unknown exemption is refused', r.status_code == 400)
        r = client.post(api + '/exemption', json={'exemption': '506b', 'note': 'Confirmed with counsel'})
        check('the owner records 506(b)', r.status_code == 200 and r.get_json()['exemption'] == '506b', r.get_json())
        cp = ComplianceProfile.query.filter_by(venture_id=V_ID).first()
        check('compliance_profile has recorded_by and recorded_at', cp.recorded_by == F_ID and cp.recorded_at)
        check('the exemption is audit-logged',
              AuditLog.query.filter_by(action='founder_exemption_recorded', resource_id=V_ID).count() == 1)

        print('\n4. Matching and 37% calibration')
        firms = []
        for i in range(10):
            sector = 'health' if i % 2 == 0 else 'fintech'
            p = InvestorProfile(name='Partner %d' % i, firm='Fund %d' % i, kind='investor',
                                stages=['seed'] if i < 6 else ['series-a'], check_min=25000, check_max=500000,
                                sectors=[sector], geographies=['us'], source='admin')
            db.session.add(p)
            firms.append(p)
        seed_fund = InvestorProfile(name='Sam Seed', firm='Seed Fund LLC', kind='investor', stages=['seed'],
                                    sectors=['health'], geographies=['us'], source='admin')
        accel = InvestorProfile(name='Dental Accelerator', firm='ToothStart', kind='accelerator', stages=['seed'],
                                sectors=['health'], apply_url='https://toothstart.org/apply',
                                accepts_applications=True, auto_submit_allowed=True, source='admin')
        db.session.add_all([seed_fund, accel])
        db.session.commit()
        r = client.post(api + '/match')
        check('match builds the target list', r.status_code == 200 and len(r.get_json()['targets']) == 12,
              r.get_json())
        rows = fi.targets(venture())
        n_cal = sum(1 for t in rows if t.status == 'calibration')
        check('the first ~37%% are marked calibration (%d of 12 -> 4)' % n_cal, n_cal == 4 == fi.calibration_size(12))
        check('calibration targets are the first by position',
              all(t.status == 'calibration' for t in rows[:4]) and all(t.status == 'candidate' for t in rows[4:]))
        check('calibration_size rounds 37% (10 -> 4, 100 -> 37, 1 -> 0)',
              (fi.calibration_size(10), fi.calibration_size(100), fi.calibration_size(1)) == (4, 37, 0))
        check('fit reasons explain the score', rows[0].fit_reasons and rows[0].fit_reasons.get('fits'))
        check('a series-a fund ranks below seed funds',
              max(t.position for t in rows if t.firm in ('Fund 0', 'Fund 2')) <
              min(t.position for t in rows if t.firm in ('Fund 6', 'Fund 7')))
        cal = fi.calibration(venture())
        check('no suggestion before calibration is reviewed', cal['suggestion'] is None and not cal['done'])
        for t in rows[:4]:
            status = 'pursuing' if (t.firm or '').startswith('Fund') and t.firm in ('Fund 0',) else 'passed'
            client.post(api + '/targets/%s/status' % t.id, json={'status': status})
        cal = fi.calibration(venture())
        check('calibration done after the first 4 are reviewed', cal['done'] and cal['reviewed'] == 4, cal)
        sug = cal['suggestion']
        check('a suggestion is made after calibration', sug is not None and sug.position >= 4, cal['note'])
        if sug is not None and cal['note'].startswith('The next match'):
            check('the suggestion beats the best seen in calibration', cal['adjusted'][sug.id] > cal['bar'])
            earlier = [t for t in fi.targets(venture()) if 4 <= t.position < sug.position
                       and t.status in ('candidate', 'calibration')]
            check('and it is the first one in list order that does',
                  all(cal['adjusted'][t.id] <= cal['bar'] for t in earlier))
        else:
            check('when nothing beats the bar the page says so', 'No remaining match beats' in cal['note'])
        r = client.post(api + '/targets', json={'name': 'Nina Angel', 'firm': 'Angel Circle', 'kind': 'investor',
                                                 'stages': 'seed', 'sectors': 'health', 'check_min': '10000',
                                                 'check_max': '50000'})
        check('the founder adds an investor', r.status_code == 200, r.get_json())
        added = InvestorProfile.query.filter_by(name='Nina Angel').first()
        check('it is stored as investor_profile source founder', added and added.source == 'founder'
              and added.added_by == F_ID)
        check('decided targets keep their status after a re-rank',
              InvestorTarget.query.filter_by(venture_id=V_ID, status='pursuing').count() == 1)

        print('\n5. Warm paths: deterministic, consent-gated, no email data to the LLM')
        r = client.post(api + '/warm-paths')
        check('warm paths need contacts consent (409)', r.status_code == 409, r.get_json())
        login(C_ID)
        r = client.post(api + '/consent', json={'kind': 'contacts', 'grant': True})
        check('only the owner can share their contacts', r.status_code == 403)
        login(F_ID)
        r = client.post(api + '/consent', json={'kind': 'contacts', 'grant': True})
        check('the owner turns contacts access on', r.status_code == 200 and fi.contacts_consent(venture()))
        acct = EmailAccount(user_id=F_ID, provider='gmail', address='fran@acme.dev')
        db.session.add(acct)
        db.session.flush()
        imported = Contact(user_id=F_ID, first_name='Zelda', last_name='Quimby', email='zelda.quimby@seedfund.vc',
                           company_name='Seed Fund', job_title='Platform lead', source='email_import')
        manual = Contact(user_id=F_ID, first_name='Mo', last_name='Manual', email='mo@seedfund.vc',
                         company_name='Seed Fund, Inc.', source='manual_entry')
        headline = Contact(user_id=F_ID, first_name='Hal', last_name='Headline', email='hal@else.com',
                           company_name='Elsewhere', linkedin_headline='Venture partner at Fund 0', source='manual_entry')
        stranger = Contact(user_id=O_ID, first_name='Not', last_name='Mine', email='not@seedfund.vc',
                           company_name='Seed Fund', source='manual_entry')
        db.session.add_all([imported, manual, headline, stranger])
        db.session.flush()
        db.session.add(ContactAccountLink(contact_id=imported.id, account_id=acct.id, sent_count=12, received_count=9,
                                          last_seen=now - timedelta(days=5)))
        db.session.commit()
        r = client.post(api + '/warm-paths')
        check('warm paths found', r.status_code == 200 and r.get_json()['count'] >= 3, r.get_json())
        ps = fi.paths(venture())
        conns = {p.connector_contact_id for p in ps}
        check('company equality (suffix-insensitive) finds both Seed Fund contacts',
              imported.id in conns and manual.id in conns)
        check('a headline naming the firm counts as affiliation', headline.id in conns)
        check("another member's contacts are never used", stranger.id not in conns)
        check('no LLM call while finding warm paths', not fake.calls)
        p_imp = [p for p in ps if p.connector_contact_id == imported.id][0]
        p_man = [p for p in ps if p.connector_contact_id == manual.id][0]
        check('without mailbox consent no mailbox counts are used',
              not any(e.get('kind') == 'mailbox' for e in p_imp.evidence or []))
        client.post(api + '/consent', json={'kind': 'mailbox', 'grant': True})
        client.post(api + '/warm-paths')
        ps = fi.paths(venture())
        p_imp = [p for p in ps if p.connector_contact_id == imported.id][0]
        p_man = [p for p in ps if p.connector_contact_id == manual.id][0]
        check('with mailbox consent sent/received counts rank the connector higher',
              p_imp.strength > p_man.strength and any(e.get('kind') == 'mailbox' for e in p_imp.evidence))
        r = client.post(api + '/paths/%s/draft' % p_imp.id)
        check('draft an intro request from a warm path', r.status_code == 200, r.get_json())
        intro = db.session.get(VentureOutreachAction, r.get_json()['action']['id'])
        prompt = '\n'.join(c['text'] for c in fake.calls)
        leaked = [x for x in ('Zelda', 'Quimby', 'zelda.quimby', 'Platform lead', 'Mo Manual', 'mo@seedfund',
                              'Headline', 'hal@else') if x in prompt]
        check('the LLM prompt carries no email-imported (or any connector) data', not leaked, leaked)
        check('the prompt has the target firm and the venture facts', 'Seed Fund' in prompt and 'Acme Dental' in prompt)
        check("the connector's first name is inserted after generation",
              intro.body.startswith('Hi Zelda,') and '{first_name}' not in intro.body)
        check('the intro includes a forwardable blurb', 'A note you can forward' in intro.body)
        check('the draft awaits approval at the warm-intro level',
              intro.status == 'awaiting_approval' and intro.level_required == 3, (intro.status, intro.level_required))
        check('the path is marked drafted', fresh(IntroPath, p_imp.id).status == 'drafted')
        fake.dirty_once = True
        r = client.post(api + '/paths/%s/draft' % p_man.id)
        man_intro = db.session.get(VentureOutreachAction, r.get_json()['action']['id'])
        check('a draft that projects returns is regenerated and never stored',
              'returns' not in man_intro.body and r.status_code == 200)

        print('\n6. Sending under 506(b)')
        login(C_ID)
        r = client.post(api + '/actions/%s/send' % intro.id)
        check('a co-founder without the investor decision right cannot approve', r.status_code == 403)
        login(F_ID)
        r = client.post(api + '/actions/%s/send' % intro.id)
        check('approve and send an intro to an own contact under 506(b)', r.status_code == 200, r.get_json())
        intro = fresh(VentureOutreachAction, intro.id)
        check('status sent with sent_at and level_applied', intro.status == 'sent' and intro.sent_at
              and intro.level_applied == 3, (intro.status, intro.level_applied))
        check('exactly one email went out', len(sent) == 1, len(sent))
        msg = sent[-1]
        content = ''.join(c.get('value', '') for c in msg.get('content', []))
        check('reply-to is the founder', (msg.get('reply_to') or {}).get('email') == 'fran@acme.dev',
              msg.get('reply_to'))
        check("display name is the founder's name", (msg.get('from') or {}).get('name') == 'Fran Founder')
        check('sent to the connector', msg['personalizations'][0]['to'][0]['email'] == 'zelda.quimby@seedfund.vc')
        check('labelled as sent on the founder\'s behalf by SimiSI',
              'Sent on behalf of Fran Founder by SimiSI' in content)
        check('the unsubscribe link is member-scoped', '/outreach/founder-unsubscribe/' in content
              and '/outreach/unsubscribe/' not in content)
        check('List-Unsubscribe header is the member-scoped link',
              '/outreach/founder-unsubscribe/' in (msg.get('headers') or {}).get('List-Unsubscribe', ''))
        check('the postal address footer is present', '1 Market St' in content)
        check('the send is logged in email_logs', EmailLog.query.filter_by(action_id=intro.id).count() == 1)
        check('the send is audit-logged', AuditLog.query.filter_by(action='founder_outreach_sent').count() == 1)
        check('no above-level flag for an in-level send', not ObservabilityFlag.query.filter_by(
            target_id=intro.id).first())

        fc = VentureOutreachAction(venture_id=V_ID, action_type='first_contact', level_required=2,
                                   status='awaiting_approval', target_ref='email:new.partner@coldfund.vc',
                                   subject='Acme Dental', body='Hello, we are Acme Dental.')
        db.session.add(fc)
        db.session.commit()
        r = client.post(api + '/actions/%s/send' % fc.id)
        check('under 506(b) a first contact to a stranger is refused', r.status_code == 409
              and '506(b)' in r.get_json()['error'], r.get_json())
        check('nothing sent for it', len(sent) == 1)

        tgt = InvestorTarget.query.filter_by(venture_id=V_ID, investor_profile_id=accel.id).first()
        fake.app_answers = [
            {'question': fi.APPLICATION_QUESTIONS[0], 'answer': 'We cut no-shows for dental clinics.',
             'evidence': 'Acme Dental cuts no-shows for dental clinics.'},
            {'question': fi.APPLICATION_QUESTIONS[1], 'answer': 'Clinics lose money to missed appointments.',
             'evidence': 'Clinics lose a million dollars a year.'},
            {'question': fi.APPLICATION_QUESTIONS[2], 'answer': 'Interviews.', 'evidence': ''},
        ]
        r = client.post(api + '/targets/%s/application' % tgt.id)
        body = r.get_json()
        check('draft an application for a published form', r.status_code == 200, body)
        ans = body['answers']
        check('an answer with an exact supporting passage is not flagged', ans[0]['needs_evidence'] is False)
        check('an answer whose quote is not in the documents is flagged', ans[1]['needs_evidence'] is True)
        check('an answer without a quote is flagged', ans[2]['needs_evidence'] is True)
        check('unanswered questions are flagged', all(a['needs_evidence'] for a in ans[3:]))
        check('auto-submit is never used, even where the site allows it', body['auto_submit'] is False)
        app_action = db.session.get(VentureOutreachAction, body['action']['id'])
        n_sent = len(sent)
        r = client.post(api + '/actions/%s/send' % app_action.id)
        check('under 506(b) a published application goes through after approval', r.status_code == 200
              and r.get_json()['delivery'] == 'founder_submits', r.get_json())
        check('nothing is submitted or emailed for an application', len(sent) == n_sent)
        app_action = fresh(VentureOutreachAction, app_action.id)
        check('the application is approved with level_applied, not marked sent',
              app_action.status == 'approved' and app_action.level_applied == 2, (app_action.status,
                                                                                 app_action.level_applied))

        print('\n7. Autonomy limits')
        check('first contact is held at fnd_first_contact_cap', founder_autonomy.limits('first_contact')['max'] ==
              founder_config.get('fnd_first_contact_cap') == 3)
        over = VentureOutreachAction(venture_id=V_ID, action_type='first_contact', level_required=4, status='approved',
                                     approved_by=F_ID, target_ref='contact:%s' % manual.id, subject='Hi',
                                     body='Hello Mo, a short note about Acme Dental.')
        db.session.add(over)
        db.session.commit()
        r = client.post(api + '/actions/%s/send' % over.id)
        check('a first contact above fnd_first_contact_cap is refused', r.status_code == 403, r.get_json())
        over = fresh(VentureOutreachAction, over.id)
        check('the draft is held', over.status == 'blocked')
        flag = ObservabilityFlag.query.filter_by(target_id=over.id,
                                                 rule_name=founder_alerts.RULE_PREFIX + 'above_level_send').first()
        check('the above-level attempt raised a flag at once', flag is not None and flag.severity == 'critical')

        neg = VentureOutreachAction(venture_id=V_ID, action_type='negotiate_terms', level_required=2,
                                    status='approved', approved_by=F_ID, target_ref='contact:%s' % manual.id,
                                    subject='Terms', body='About the round.')
        db.session.add(neg)
        db.session.commit()
        r = client.post(api + '/actions/%s/send' % neg.id)
        check('negotiating terms is never sent', r.status_code == 403 and 'never' in r.get_json()['error'])
        check('and it raises the flag', ObservabilityFlag.query.filter_by(target_id=neg.id).first() is not None)

        adv = VentureOutreachAction(venture_id=V_ID, action_type='followup', level_required=1, status='approved',
                                    approved_by=F_ID, target_ref='contact:%s' % imported.id, subject='Following up',
                                    body='Thanks again.')
        li = VentureOutreachAction(venture_id=V_ID, action_type='intro_request', level_required=3, status='approved',
                                   approved_by=F_ID, target_ref='linkedin:https://www.linkedin.com/in/zq',
                                   subject='Intro', body='Hi Zelda, could you introduce me?')
        db.session.add_all([adv, li])
        db.session.commit()
        r = client.post(api + '/actions/%s/send' % adv.id)
        check('advisor-only (level 1) is never sent by SimiSI', r.status_code == 409 and 'advisor' in
              r.get_json()['error'])
        n_sent = len(sent)
        r = client.post(api + '/actions/%s/send' % li.id)
        check('a LinkedIn draft is refused (copy-only)', r.status_code == 409
              and r.get_json()['error'] == legal_text.get('linkedin_manual'), r.get_json())
        check('LinkedIn: nothing sent', len(sent) == n_sent and fresh(VentureOutreachAction, li.id).status == 'approved')

        print('\n8. 506(c) and the daily cap')
        client.post(api + '/exemption', json={'exemption': '506c'})
        r = client.post(api + '/actions/%s/send' % fc.id)
        check('under 506(c) the first contact sends', r.status_code == 200, r.get_json())
        fc = fresh(VentureOutreachAction, fc.id)
        check('first contact level_applied 2', fc.status == 'sent' and fc.level_applied == 2)
        new_c = Contact.query.filter_by(user_id=F_ID, email='new.partner@coldfund.vc').first()
        check("the new investor is added to the owner's CRM as founder_investor",
              new_c is not None and new_c.source == fi.CONTACT_SOURCE)
        founder_config.set('fnd_action_cap_per_day', founder_autonomy.sent_today(venture()), A_ID)
        fu = VentureOutreachAction(venture_id=V_ID, action_type='followup', level_required=3, status='approved',
                                   approved_by=F_ID, target_ref='contact:%s' % imported.id, subject='Following up',
                                   body='Thanks for the reply, Zelda.')
        db.session.add(fu)
        db.session.commit()
        n_sent = len(sent)
        r = client.post(api + '/actions/%s/send' % fu.id)
        check('the daily action cap is enforced (429)', r.status_code == 429, r.get_json())
        check('cap: nothing sent', len(sent) == n_sent)
        founder_config.set('fnd_action_cap_per_day', 10, A_ID)
        client.post(api + '/exemption', json={'exemption': '506b'})
        r = client.post(api + '/actions/%s/send' % fu.id)
        check('under 506(b) a follow-up to an investor who replied sends', r.status_code == 200, r.get_json())

        print('\n9. Revoking contacts consent stops warm paths')
        pending = db.session.get(VentureOutreachAction, man_intro.id)
        r = client.post(api + '/consent', json={'kind': 'contacts', 'grant': False})
        check('one-click revoke', r.status_code == 200 and not fi.contacts_consent(venture()))
        check('every warm path is removed', IntroPath.query.filter_by(venture_id=V_ID).count() == 0)
        check('unsent intro drafts from contacts are held', fresh(VentureOutreachAction, pending.id).status == 'blocked')
        r = client.post(api + '/warm-paths')
        check('warm paths refuse after revoke', r.status_code == 409)
        check('revoke is audit-logged', AuditLog.query.filter_by(action='founder_consent_revoked').count() >= 1)

        print('\n10. Investor updates')
        sp = StartupPage(venture_id=V_ID, slug='acme-dental', status='published', name='Acme Dental',
                         traction=[{'label': 'Clinics in pilot', 'value': '12', 'as_of': '2026-11-01', 'show': True}])
        db.session.add(sp)
        db.session.flush()
        db.session.add_all([
            InvestorAccess(startup_page_id=sp.id, email='opted@fund.vc', kind='paid', status='active',
                           opted_in_updates=True),
            InvestorAccess(startup_page_id=sp.id, email='invited@fund.vc', kind='invited', status='active'),
            InvestorAccess(startup_page_id=sp.id, email='quiet@fund.vc', kind='paid', status='active'),
            InvestorAccess(startup_page_id=sp.id, email='waiting@fund.vc', kind='requested', status='requested',
                           opted_in_updates=True)])
        db.session.commit()
        calls_before = len(fake.calls)
        r = client.post(api + '/updates')
        check('draft an update', r.status_code == 200, r.get_json())
        up = db.session.get(InvestorUpdate, r.get_json()['update']['id'])
        upd_prompt = fake.calls[calls_before]['text'] if len(fake.calls) > calls_before else ''
        check('the update prompt uses the chosen metrics', 'Clinics in pilot' in upd_prompt)
        check('the update is a draft', up.status == 'draft')
        r = client.post(api + '/updates/%s/send' % up.id)
        check('an unapproved update is not sent', r.status_code == 409 and len(sent) == n_sent + 1)
        login(C_ID)
        r = client.post(api + '/updates/%s/approve' % up.id)
        check('only the investor-contact decision holder approves', r.status_code == 403)
        login(F_ID)
        r = client.post(api + '/updates/%s/approve' % up.id)
        check('the owner approves', r.status_code == 200)
        n_sent = len(sent)
        r = client.post(api + '/updates/%s/send' % up.id)
        out = r.get_json()
        check('the update sends to opted-in and invited investors with access only',
              r.status_code == 200 and out['sent'] == 2 and out['total'] == 2, out)
        to = sorted(m['personalizations'][0]['to'][0]['email'] for m in sent[n_sent:])
        check('recipients are exactly opted@ and invited@', to == ['invited@fund.vc', 'opted@fund.vc'], to)
        um = sent[-1]
        uc = ''.join(c.get('value', '') for c in um.get('content', []))
        check('the update carries the member-scoped unsubscribe and the update footer',
              '/outreach/founder-unsubscribe/' in uc and 'not an offer to sell' in uc)
        check('update reply-to is the founder', (um.get('reply_to') or {}).get('email') == 'fran@acme.dev')
        up = fresh(InvestorUpdate, up.id)
        check('the update is marked sent with the recipient count', up.status == 'sent' and up.recipient_count == 2)
        link = re.search(r'(/outreach/founder-unsubscribe/[^"\s<]+)', uc).group(1)
        login(None)
        r = client.get(link)
        check('the unsubscribe link works without signing in', r.status_code == 200, r.status_code)
        check('it says it applies to this sender only', 'this sender only' in r.get_data(as_text=True))
        rcpt = um['personalizations'][0]['to'][0]['email']
        check('member-scoped: suppression_list row for the venture owner', UserSuppression.query.filter_by(
            user_id=F_ID, email_hash=email_hash(rcpt)).first() is not None)
        check('never the global opt-out list', EmailSuppression.query.filter_by(email=rcpt).first() is None)
        acc = InvestorAccess.query.filter_by(startup_page_id=sp.id, email=rcpt).first()
        check('updates stop for that investor', acc.opted_in_updates is False)
        check('a bad token is refused', client.get('/outreach/founder-unsubscribe/nope').status_code == 400)
        login(F_ID)
        up2 = fi.draft_update(venture(), db.session.get(User, F_ID))
        fi.approve_update(venture(), up2, db.session.get(User, F_ID))
        res = fi.send_update(venture(), up2, db.session.get(User, F_ID))
        skipped = [x for x in res['results'] if x['email'] == rcpt]
        check('the next update skips the unsubscribed investor (or no longer lists them)',
              not skipped or skipped[0]['status'] == 'skipped', res)

        print('\n11. The page renders the full state')
        founder_config.set('fnd_counsel_signoff_at', '2026-11-01', A_ID)
        r = client.get(page)
        html = r.get_data(as_text=True)
        check('page 200 with data', r.status_code == 200)
        check('page shows the recorded exemption', 'Rule 506(b)</span> recorded' in html)
        k13 = fi.calibration_size(InvestorTarget.query.filter_by(venture_id=V_ID).count())
        check('page shows calibration progress (the share grows with the list: 13 -> 5)',
              k13 == 5 and ('of %d reviewed' % k13) in html, re.findall(
            r'Calibration: [^<]+', html))
        check('page lists levels and approvers on drafts', 'Approver:' in html or 'Nothing waiting' in html)
        check('page explains the on-behalf label', 'Sent on behalf of Fran Founder by SimiSI' in html)
        from app.services import compliance_filter
        visible = re.sub(r'<[^>]+>', ' ', re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S))
        check('page copy passes the banned-phrase check', not compliance_filter.banned_phrases(visible),
              compliance_filter.banned_phrases(visible))

        print('\n12. Investor switch off again: everything 404s')
        founder_feature.set_mode('founder_investor', 'off', A_ID)
        check('page 404', client.get(page).status_code == 404)
        check('send API 404', client.post(api + '/actions/%s/send' % fu.id).status_code == 404)
        later = VentureOutreachAction(venture_id=V_ID, action_type='intro_request', level_required=3,
                                      status='approved', approved_by=F_ID, target_ref='contact:%s' % manual.id,
                                      subject='x', body='y')
        db.session.add(later)
        db.session.commit()
        try:
            founder_autonomy.send(later, db.session.get(User, F_ID))
            ok = False
        except founder_autonomy.SendBlocked as exc:
            ok = exc.status == 403
        check('founder_autonomy.send refuses with the switch off', ok)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        print('FAILED: ' + '; '.join(FAIL))
        sys.exit(1)


if __name__ == '__main__':
    main()
