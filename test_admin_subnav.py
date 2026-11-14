"""Admin navigation: one shared two-row menu (groups, then the current group's pages).

    python test_admin_subnav.py
"""
import glob
import os
import re
import sys
from datetime import datetime

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


# url -> (group, page label); a page label of None means a group with one page.
PAGES = {
    '/admin': ('Overview', None),
    '/admin/analytics': ('Growth', 'Analytics'),
    '/admin/outreach': ('Growth', 'Outreach'),
    '/admin/contacts': ('Growth', 'Contacts'),
    '/admin/partners': ('Growth', 'Partners'),
    '/admin/corporate': ('Growth', 'Organizations'),
    '/admin/transition': ('Growth', 'Transition'),
    '/admin/inquiries': ('Growth', 'Inquiries'),
    '/admin/founder': ('Founder Track', None),
    '/admin/experts': ('People', 'Experts'),
    '/admin/careers': ('People', 'Careers'),
    '/admin/feedback': ('People', 'Feedback'),
    '/admin/guide': ('People', 'SME Console'),
    '/admin/blog': ('Content', 'Blog'),
    '/admin/bio-review': ('Content', 'Bio Review'),
    '/admin/calibration': ('Platform', 'Calibration'),
    '/admin/observability': ('Platform', 'Observability'),
    '/admin/email-crm': ('Platform', 'Email Import'),
    '/admin/compliance': ('Platform', 'Compliance'),
    '/admin/legal-text': ('Platform', 'Legal Text'),
}


def main():
    from app import create_app
    from app.extensions import db
    from utils.id_gen import generate_id
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    print('\n1. One copy')
    copies = [p for p in glob.glob(os.path.join(ROOT, 'app', 'templates', '**', '*.html'), recursive=True)
              if 'class="admin-subnav"' in open(p, encoding='utf-8').read()]
    check('the menu markup lives only in the shared partial',
          [os.path.basename(p) for p in copies] == ['_admin_subnav.html'], copies)

    with app.app_context():
        db.create_all()
        from flask import g
        from app.models.user import User
        admin = User(id=generate_id(), email='a@x.com', full_name='Admin', password_hash='x', is_admin=True,
                     email_verified=True, onboarding_completed_at=datetime.utcnow())
        db.session.add(admin)
        db.session.commit()
        client = app.test_client()
        with client.session_transaction() as s:
            s['_user_id'], s['_fresh'] = admin.id, True
        print('\n2. Every admin page')
        for url, (group, page) in PAGES.items():
            g.pop('_login_user', None)
            res = client.get(url)
            html = res.get_data(as_text=True)
            if res.status_code != 200:
                check('%s renders' % url, False, res.status_code)
                continue
            top = re.search(r'<nav class="admin-subnav".*?</nav>', html, re.S)
            groups = re.findall(r'class="subnav-link[^"]*"[^>]*>([^<]+)<', top.group(0)) if top else []
            active = re.findall(r'class="subnav-link active"[^>]*>([^<]+)<', top.group(0)) if top else []
            sub = re.search(r'<nav class="admin-subnav-sub".*?</nav>', html, re.S)
            ok = groups == ['Overview', 'Growth', 'People', 'Content', 'Platform', 'Founder Track'] and active == [group]
            if page is None:
                ok = ok and sub is None
            else:
                sub_active = re.findall(r'class="subnav-sublink active"[^>]*>([^<]+)<', sub.group(0)) if sub else []
                ok = ok and sub_active == [page]
            check('%s: group "%s"%s' % (url, group, (', page "%s"' % page) if page else ''), ok,
                  (groups, active, sub and sub.group(0)[:200]))

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
