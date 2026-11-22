"""
cron_customer_email.py — Founder Track customer email worker, run by cron
EVERY 5 MINUTES (CUSTOMER_EMAIL_PLAN.md).

Sends customer emails the founders scheduled for later, and the rest of a large
send (the website sends the first 50 recipients at once, the worker the
remainder). Every email is re-checked before it goes: the setting is on, the
venture isn't paused, the contact hasn't opted out or bounced, the sender is
still a founder. When nothing is due the script exits after one query.

A lock file keeps one worker at a time.

cPanel cron command (same interpreter and folder as cron_evaluations.py):
  */5 * * * * /home/dburriyy6pdz/virtualenv/public_html/simisi/3.9/bin/python \
    /home/dburriyy6pdz/public_html/simisi/cron_customer_email.py >> \
    /home/dburriyy6pdz/public_html/simisi/cron.log 2>&1

It reads the app folder's .env (config.py), so SENDGRID_API_KEY must be there:
cron does not get the cPanel app environment.
"""
from __future__ import annotations

import os
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)
os.environ.setdefault('SIMISI_NO_SCHEDULER', '1')      # no second copy of the in-process jobs

import logging
logging.basicConfig(
    filename=os.path.join(APP_ROOT, 'cron.log'),
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s cron_customer_email %(message)s',
)
log = logging.getLogger(__name__)

LOCK_PATH = os.path.join(APP_ROOT, '.cron_customer_email.lock')


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
            return 0             # the previous worker is still sending
    from app import create_app
    app = create_app('production')
    with app.app_context():
        from app.services import founder_customer_email as fce
        try:
            out = fce.process_scheduled()
            if out.get('due'):
                log.info('customer email: %s', out)
        except Exception as exc:
            log.error('customer email worker failed: %s', exc, exc_info=True)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
