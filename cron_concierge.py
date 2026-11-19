"""
cron_concierge.py — Founder Track concierge onboarding daily job
(FOUNDER_CONCIERGE_PLAN.md step 11), run by cron once a day.

Sends the due reminders (fnd_cc_reminder_days), expires cases not accepted
within their window, and permanently deletes declined/expired cases whose
window has ended (encrypted files, evaluation, documents, the venture row;
a minimal audit record stays). The same function also runs in-process from
app/scheduler.py; it is idempotent, and cron is the guarantee because
Passenger can stop an idle process (and APScheduler with it). A lock file
stops two runs from overlapping.

cPanel cron command (same interpreter as cron_mail.py), daily at 05:30:
  30 5 * * * /home/dburriyy6pdz/virtualenv/simisi/3.9/bin/python \
    /home/dburriyy6pdz/simisi/cron_concierge.py >> \
    /home/dburriyy6pdz/simisi/cron.log 2>&1

Like cron_mail.py, it reads ~/simisi/.env (cron gets no cPanel app env).
"""
import os
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)

import logging
logging.basicConfig(
    filename=os.path.join(APP_ROOT, 'cron.log'),
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s cron_concierge %(message)s',
)
log = logging.getLogger(__name__)

LOCK_PATH = os.path.join(APP_ROOT, '.cron_concierge.lock')


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
        from app.services import concierge_service
        try:
            res = concierge_service.run_daily()
            log.info('concierge daily: %s', res)
            if res.get('errors'):
                return 1
        except Exception as exc:
            log.error('concierge daily failed: %s', exc, exc_info=True)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
