"""/transition-package (employer marketing page) and POST /enterprise/inquiry.

    python test_transition_page.py
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


def text_of(html):
    html = re.sub(r'(?is)<(style|script)\b.*?</\1>', ' ', html)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))


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
    with app.app_context():
        from app.models.user_event import UserEvent as _UE  # noqa: F401  (not in models/__init__; create its table)
        db.create_all()
        from flask import g
        from app.models.user import User
        from app.models.enterprise_inquiry import EnterpriseInquiry
        from app.models.admin_prospect import AdminProspect
        from app.models.platform_settings import PlatformSetting
        from app.models.user_event import UserEvent
        import app.services.email_service as es

        mails = []
        es.send_enterprise_inquiry_email = lambda to, inq: mails.append((to, inq.id))

        client = app.test_client()

        def login(uid):
            with client.session_transaction() as s:
                s['_user_id'], s['_fresh'] = uid, True
            g.pop('_login_user', None)

        def token():
            html = client.get('/transition-package').get_data(as_text=True)
            m = re.search(r'name="csrf_token" value="([^"]+)"', html)
            return m.group(1) if m else ''

        print('\n1. The page')
        res = client.get('/transition-package')
        html = res.get_data(as_text=True)
        t = text_of(html)
        check('GET /transition-package returns 200', res.status_code == 200)
        check('title and meta description from the mockup',
              '<title>Transition Package for Employers | SimiSI</title>' in html and
              'Give every departing employee a 24-month plan, a professional bio page, 49 AI agents' in html
              and html.count('<title>') == 1)
        check('copy ported verbatim (hero, sections, FAQ)', all(s in t for s in (
            'When roles end, give every person', 'How you handle a layoff is remembered.',
            'Five ways a career can earn.', 'Live in a day. Useful from day one.', 'One console. No spreadsheets.',
            'You see whether it works. Only they see their plan.', 'Three packages. Priced per seat.',
            'A different kind of transition support.', 'Questions HR teams ask.')))
        check('no hardcoded price: "Per-seat quote" only', t.count('Per-seat quote') == 3
              and not re.search(r'\$\s?\d', t))
        check('honesty wording kept', 'sent only on their approval' in t and "never moves anyone's money" in t
              and 'Illustrative data' in t)
        check('mockup banner and fake-submit JS dropped', 'Concept mockup' not in html and 'mockup only' not in html)
        check('brand, contact address and legal links kept', 'simi@simulacrumai.io' in html
              and '/legal/privacy' in html and '/legal/terms' in html)
        check('uses the site\'s base layout and the shared stylesheet', 'class="navbar"' in html
              and 'css/marketing.css' in html and 'class="pf"' in html)

        print('\n2. Launch mode flag')
        check('design-partner mode by default: badge, CTAs, perks', 'Now enrolling design partners' in t
              and t.count('Become a design partner') >= 2 and 'Design-partner pricing' in t)
        PlatformSetting.set('transition_package_mode', 'general')
        db.session.commit()
        t2 = text_of(client.get('/transition-package').get_data(as_text=True))
        check('general mode: "Request a quote", no design-partner badge, CTAs or perks',
              'Request a quote' in t2 and 'Now enrolling design partners' not in t2
              and 'Become a design partner' not in t2 and 'Design-partner pricing' not in t2)
        PlatformSetting.set('transition_package_mode', 'design_partner')
        db.session.commit()

        print('\n3. Inquiry form')
        good = {'name': 'Hana Ruiz', 'email': 'hana@acme.com', 'company': 'Acme Corp', 'role': 'HR Director',
                'headcount': '100–500', 'timing': '1–3 months', 'notes': 'Reduction in Q1.', 'source': 'transition-package'}
        hdr = {'Accept': 'application/json'}
        res = client.post('/enterprise/inquiry', data=dict(good, csrf_token='wrong'), headers=hdr)
        check('a bad CSRF token is rejected', res.status_code == 400 and EnterpriseInquiry.query.count() == 0)
        tok = token()
        res = client.post('/enterprise/inquiry', data=dict(good, email='not-an-email', csrf_token=tok), headers=hdr)
        check('an invalid email is rejected', res.status_code == 400 and 'valid work email' in res.get_json()['message']
              and EnterpriseInquiry.query.count() == 0)
        res = client.post('/enterprise/inquiry', data=dict(good, headcount='a million', csrf_token=tok), headers=hdr)
        check('an unknown headcount band is rejected', res.status_code == 400)
        res = client.post('/enterprise/inquiry', data=dict(good, website='http://spam', csrf_token=tok), headers=hdr)
        check('a honeypot submission looks successful but stores and sends nothing',
              res.status_code == 200 and EnterpriseInquiry.query.count() == 0 and not mails)
        PlatformSetting.set('enterprise_inquiry_notify_email', 'sales@simisi.io')
        db.session.commit()
        res = client.post('/enterprise/inquiry', data=dict(good, csrf_token=tok), headers=hdr)
        check('a valid submission creates one row', res.status_code == 200 and EnterpriseInquiry.query.count() == 1)
        inq = EnterpriseInquiry.query.first()
        check('…with the fields, status new, and a hashed IP only', inq.company == 'Acme Corp'
              and inq.headcount_band == '100–500' and inq.status == 'new' and inq.mode == 'design_partner'
              and inq.ip_hash and len(inq.ip_hash) == 64)
        check('…and sends exactly one notification, to the admin-set address', mails == [('sales@simisi.io', inq.id)])
        ev = UserEvent.query.filter_by(event_type='transition_package_inquiry').all()
        check('the analytics event is recorded without personal data', len(ev) == 1 and ev[0].user_id is None
              and ev[0].event_data == {'source': 'transition-package', 'mode': 'design_partner'}
              and ev[0].ip_address is None)
        res = client.post('/enterprise/inquiry', data=dict(good, csrf_token=tok))
        page = res.get_data(as_text=True)
        check('without JavaScript the page re-renders with the thank-you in place of the form',
              res.status_code == 200 and "Thank you. We'll be in touch within one business day." in page
              and 'name="company"' not in page)
        PlatformSetting.set('enterprise_inquiry_rate_limit', '2')
        db.session.commit()
        res = client.post('/enterprise/inquiry', data=dict(good, csrf_token=tok), headers=hdr)
        check('the per-IP rate limit (admin setting) holds', res.status_code == 400
              and 'Too many' in res.get_json()['message'] and EnterpriseInquiry.query.count() == 2)

        print('\n4. Homepage Enterprise card')
        home = client.get('/').get_data(as_text=True)
        card = home[home.index('price-card fade-up d3'):]
        card = card[:card.index('</div>\n    </div>') if '</div>\n    </div>' in card else 4000]
        check('the Enterprise card links to /transition-package', 'See the Transition Package →' in card
              and '/transition-package' in card)
        check('…and keeps its price text, features and Contact sales', all(s in card for s in (
            'Custom', 'per seat · annual contract', 'Bulk seat licensing', 'Priority API access', 'Contact sales →')))
        check('the other price cards don\'t get the link', home.count('See the Transition Package') == 1
              and home.count('class="price-card') == 3)

        print('\n5. Sitemap and admin')
        check('the page is in the sitemap', 'https://simisi.io/transition-package' in client.get('/sitemap.xml').get_data(as_text=True))
        admin = User(id=generate_id(), email='a@x.com', full_name='Admin', password_hash='x', is_admin=True,
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        other = User(id=generate_id(), email='o@x.com', full_name='Other', password_hash='x',
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add_all([admin, other])
        db.session.commit()
        login(other.id)
        check('non-admins can\'t see inquiries', client.get('/admin/inquiries').status_code == 403
              and client.post('/api/admin/inquiries/%s/status' % inq.id, json={'status': 'closed'}).status_code == 403)
        login(admin.id)
        page = client.get('/admin/inquiries').get_data(as_text=True)
        check('the admin console lists new inquiries with status controls', 'Acme Corp' in page and 'data-status' in page
              and 'Add to prospects' in page)
        res = client.post('/api/admin/inquiries/%s/status' % inq.id, json={'status': 'contacted'})
        check('status can be changed', res.status_code == 200 and EnterpriseInquiry.query.get(inq.id).status == 'contacted')
        check('unknown statuses are refused', client.post('/api/admin/inquiries/%s/status' % inq.id,
                                                          json={'status': 'won'}).status_code == 400)
        res = client.post('/api/admin/inquiries/%s/promote' % inq.id)
        p = AdminProspect.query.filter_by(firm_name='Acme Corp').first()
        check('an inquiry can be added to prospects (outbound CRM)', res.status_code == 200 and p is not None
              and p.category == 'transition' and EnterpriseInquiry.query.get(inq.id).prospect_id == p.id)
        client.post('/api/admin/inquiries/%s/promote' % inq.id)
        check('…only once', AdminProspect.query.filter_by(firm_name='Acme Corp').count() == 1)
        check('no network calls at any point', not network, network)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
