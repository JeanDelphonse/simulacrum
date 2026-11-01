"""
cron_mail.py — Email-to-CRM sync batches (PRD-SIM-MAIL-001), run by cron
every minute.

Each run spends at most `mail_sync_seconds` (admin setting, default 40)
reading the next batch for connected accounts, oldest-synced first, then
exits. The feature switch is checked before every batch, so turning the
feature off stops syncing within a minute. A lock file stops two runs from
overlapping.

cPanel cron command (same interpreter as cron_generate.py):
  * * * * * /home/dburriyy6pdz/virtualenv/simisi/3.9/bin/python \
    /home/dburriyy6pdz/simisi/cron_mail.py >> \
    /home/dburriyy6pdz/simisi/cron.log 2>&1

Like cron_generate.py, it reads ~/simisi/.env (cron gets no cPanel app env).
"""
import os
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)

import logging
logging.basicConfig(
    filename=os.path.join(APP_ROOT, 'cron.log'),
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s cron_mail %(message)s',
)
log = logging.getLogger(__name__)

LOCK_PATH = os.path.join(APP_ROOT, '.cron_mail.lock')


def main():
    try:
        import fcntl
    except ImportError:          # Windows dev: no overlap protection needed
        fcntl = None
    lock = open(LOCK_PATH, 'w')
    if fcntl is not None:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.info('previous run still going; skipping')
            return 0
    from app import create_app
    app = create_app('production')
    with app.app_context():
        from app.services import mail_sync
        try:
            res = mail_sync.run_due()
            if res.get('batches'):
                log.info('mail sync: %d batch(es)', res['batches'])
        except Exception as exc:
            log.error('mail sync failed: %s', exc, exc_info=True)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
