"""Compliance risk reduction (COMPLIANCE_PLAN.md). One section per plan step.

The network is blocked; Anthropic, SendGrid and Alpaca are faked where used.

    python test_compliance.py
"""
import os
import re
import sys
from datetime import datetime, timedelta

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def main():
    import requests
    network = []
    requests.post = requests.get = (lambda *a, **k: network.append(a[0] if a else k)
                                    or (_ for _ in ()).throw(AssertionError('network')))
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id, generate_pk
    from cryptography.fernet import Fernet
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False,
                      ENCRYPTION_KEY=Fernet.generate_key().decode())

    with app.app_context():
        from flask import g
        from app import models as _models  # noqa: F401
        db.create_all()
        from app.models.user import User
        from app.models.audit_log import AuditLog
        from app.models.compliance import LegalText
        from app.services import legal_text

        def user(admin=False, email=None):
            u = User(id=generate_id(), email=email or ('u%s@t.local' % generate_id()), full_name='Test Person',
                     password_hash='x', email_verified=True, is_admin=admin,
                     onboarding_completed_at=datetime.utcnow())
            db.session.add(u)
            db.session.commit()
            return u

        client = app.test_client()

        def login(u):
            with client.session_transaction() as s:
                s['_user_id'] = u.id
                s['_fresh'] = True
            g.pop('_login_user', None)

        # ── 1. Foundation ───────────────────────────────────────────────────
        print('\n1. generate_pk and the legal-text source')
        pks = {generate_pk() for _ in range(200)}
        check('generate_pk: 9 uppercase alphanumerics', all(re.fullmatch(r'[A-Z0-9]{9}', p) for p in pks))
        check('…and unique in practice', len(pks) == 200)
        check('new tables default to generate_pk', LegalText(key='x', body='y').id is None
              and LegalText.__table__.c.id.default.arg.__name__ == 'generate_pk')

        check('a default is served before anything is seeded', legal_text.get('estimate_label') == 'Estimate, not a promise')
        check('placeholders are filled, missing ones blank',
              legal_text.get('email_reply_optout', sender_name='Ana') == 'Reply STOP to stop receiving emails from Ana.'
              and 'Sent by  via SimiSI' in legal_text.get('email_footer'))
        try:
            legal_text.get('no_such_key')
            unknown_raises = False
        except KeyError:
            unknown_raises = True
        check('an unknown key is an error, not silent text', unknown_raises)

        legal_text.ensure_seeded()
        rows = LegalText.query.all()
        check('every default is seeded', len(rows) == len(legal_text.DEFAULTS))
        check('…and every one starts as a draft for counsel', all(r.status == LegalText.STATUS_DRAFT for r in rows))
        check('the Jinja global renders the same text',
              app.jinja_env.from_string("{{ legal('estimate_label') }}").render() == 'Estimate, not a promise')

        admin = user(admin=True)
        legal_text.update('estimate_label', 'An estimate, not a promise', admin.id)
        row = LegalText.query.filter_by(key='estimate_label').first()
        check('an edit is served, bumps the version and stays a draft',
              legal_text.get('estimate_label') == 'An estimate, not a promise' and row.version == 2
              and row.status == LegalText.STATUS_DRAFT)
        check('…and is audit-logged with the old and new wording',
              AuditLog.query.filter_by(action='legal_text_updated').count() == 1)
        legal_text.set_status('estimate_label', True, admin.id)
        check('an admin can record counsel approval', LegalText.query.filter_by(key='estimate_label').first().status
              == LegalText.STATUS_APPROVED)
        legal_text.update('estimate_label', 'Estimate, not a promise', admin.id)
        check('editing an approved text makes it a draft again',
              LegalText.query.filter_by(key='estimate_label').first().status == LegalText.STATUS_DRAFT)

        src = ''
        for root, _d, files in os.walk('app'):
            for f in files:
                if f.endswith(('.py', '.html', '.js')):
                    src += open(os.path.join(root, f), encoding='utf-8', errors='ignore').read()
        check('no code sets counsel approval except the admin action',
              src.count('STATUS_APPROVED if approved') == 1 and "status='counsel_approved'" not in src)

        member = user()
        login(member)
        check('members cannot open Admin → Legal text', client.get('/admin/legal-text').status_code == 403)
        check('…or change a text', client.put('/api/admin/legal-text/estimate_label', json={'approved': True}).status_code == 403)
        login(admin)
        page = client.get('/admin/legal-text')
        check('admins see every text with its status', page.status_code == 200
              and page.get_data(as_text=True).count('class="lt-card"') == len(legal_text.DEFAULTS)
              and 'Draft for counsel' in page.get_data(as_text=True))
        r = client.put('/api/admin/legal-text/l5_banner', json={'body': '  '})
        check('an empty text is refused', r.status_code == 400)

        # ── 3. Copy ─────────────────────────────────────────────────────────
        print('\n3. Earnings-claims copy')
        from app.models.outreach_campaign import OutreachTemplate
        from app.services import outreach_campaign_service as ocs
        import ast
        import subprocess
        old_src = subprocess.run(['git', 'show', '387cbcd:app/services/outreach_campaign_service.py'],
                                 capture_output=True, text=True, encoding='utf-8').stdout
        i = old_src.index('DEFAULT_TEMPLATES = {')
        old = ast.literal_eval(old_src[i + len('DEFAULT_TEMPLATES = '):old_src.index('\n}\n', i) + 2])
        db.session.add(OutreachTemplate(template_key='drip_3', name='d3', subject=old['drip_3']['subject'],
                                        preview_text='p', body=old['drip_3']['body'], is_drip=True))
        db.session.add(OutreachTemplate(template_key='drip_2', name='d2', subject='Our own subject',
                                        preview_text='p', body='An admin wrote this.', is_drip=True))
        db.session.commit()
        check('an unedited old drip is served as the new default',
              ocs.get_template('drip_3')['subject'] == ocs.DEFAULT_TEMPLATES['drip_3']['subject']
              and 'while you sleep' not in ocs.get_template('drip_3')['subject'].lower())
        check('…an admin-edited drip is left alone', ocs.get_template('drip_2')['body'] == 'An admin wrote this.')
        ocs.seed_default_templates()
        check('seeding upgrades only the unedited row',
              OutreachTemplate.query.filter_by(template_key='drip_3').first().subject
              == ocs.DEFAULT_TEMPLATES['drip_3']['subject']
              and OutreachTemplate.query.filter_by(template_key='drip_2').first().body == 'An admin wrote this.')

        paid = user()
        paid.voice_training_paid_at = datetime.utcnow()
        db.session.commit()
        login(paid)
        voice = client.get('/settings/voice').get_data(as_text=True)
        check('the voice-training script makes no income or results claims',
              'Read these paragraphs aloud' in voice and '46,000' not in voice and 'autopilot' not in voice
              and 'first client' not in voice)
        from app.models.simulation import SimulationLayer
        check('stored layer names no longer promise passive income',
              SimulationLayer.LAYER_NAMES[4] == 'Recurring Income — After the Initial Build'
              and 'Money Working' not in SimulationLayer.LAYER_NAMES[5])
        mig = open('sql/064_compliance.sql', encoding='utf-8').read()
        check('…and the migration renames existing simulations', "LIKE 'Automated Residual%'" in mig)
        login(admin)
        home = client.get('/').get_data(as_text=True)
        check("the home page's Layer 5 note comes from the legal-text source",
              legal_text.get('l5_note_short') in home and 'Advisory only' not in home)

    check('no network calls', not network, network)
    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
