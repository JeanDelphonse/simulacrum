"""Admin switch for the home-page integrations section (site_integrations_section).

    python test_admin_integrations_toggle.py
"""
import os
import sys
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    print('  %s %s%s' % ('ok  ' if condition else 'FAIL', label, ('  -- %s' % (detail,)) if detail and not condition else ''))


def main():
    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', WTF_CSRF_ENABLED=False)
    with app.app_context():
        from flask import g
        from app import models as _models  # noqa: F401
        db.create_all()
        from app.models.user import User
        from app.models.audit_log import AuditLog
        from app.models.platform_settings import PlatformSetting
        for uid, admin in (('ADMINTG01', True), ('MEMBRTG01', False)):
            db.session.add(User(id=uid, email='%s@t.local' % uid.lower(), full_name='T ' + uid, password_hash='x',
                                email_verified=True, is_admin=admin, onboarding_completed_at=datetime.utcnow()))
        db.session.commit()

        def as_user(client, uid):
            with client.session_transaction() as s:
                s['_user_id'] = uid
                s['_fresh'] = True
            g.pop('_login_user', None)

        admin, member, anon = app.test_client(), app.test_client(), app.test_client()
        as_user(admin, 'ADMINTG01')

        def toggle_html():
            g.pop('_login_user', None)
            page = admin.get('/admin').get_data(as_text=True)
            return page.split('id="siteIntegrationsToggle"')[1].split('>')[0] if 'id="siteIntegrationsToggle"' in page else None

        print('\nAdmin -> Platform Settings')
        t = toggle_html()
        check('the switch is on the main Admin page', t is not None)
        check('it starts off, like the home page', t is not None and 'checked' not in t.split('onchange')[0], t)
        check('it writes site_integrations_section on/off',
              t is not None and "updateSetting('site_integrations_section', this.checked ? 'on' : 'off')" in t, t)
        home = anon.get('/').get_data(as_text=True)
        check('home page: section hidden while off', 'id="integrations"' not in home)

        print('\nSwitching it')
        g.pop('_login_user', None)
        r = admin.put('/api/admin/settings/site_integrations_section', json={'value': 'on'})
        check('an admin can turn it on', r.status_code == 200, r.status_code)
        home = anon.get('/').get_data(as_text=True)
        check('home page: the section shows at once, with the FAQ link',
              'id="integrations"' in home and 'href="#integrations"' in home)
        t = toggle_html()
        check('the switch now reads on', t is not None and 'checked' in t.split('onchange')[0], t)
        check('the change is in the audit log', AuditLog.query.filter_by(action='setting_updated').count() >= 1)
        g.pop('_login_user', None)
        admin.put('/api/admin/settings/site_integrations_section', json={'value': 'off'})
        home = anon.get('/').get_data(as_text=True)
        check('turning it off hides the section again', 'id="integrations"' not in home and 'href="#integrations"' not in home)

        print('\nOnly admins')
        as_user(member, 'MEMBRTG01')
        r = member.put('/api/admin/settings/site_integrations_section', json={'value': 'on'})
        check('a member cannot switch it', r.status_code in (401, 403)
              and PlatformSetting.get('site_integrations_section') == 'off', r.status_code)
        r = anon.put('/api/admin/settings/site_integrations_section', json={'value': 'on'})
        check('a visitor cannot switch it', r.status_code in (302, 401, 403)
              and PlatformSetting.get('site_integrations_section') == 'off', r.status_code)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for label in FAIL:
        print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
