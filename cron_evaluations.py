"""
cron_evaluations.py — Founder Track evaluation worker, run by cron EVERY MINUTE.

When Admin -> Founder Track -> Concierge -> "Run evaluations with" is set to
"Cron worker" (fnd_eval_runner = cron, the default), "Run evaluation" only
queues the run (status 'queued'). This script claims queued runs one at a time
(an atomic UPDATE, so two workers never start the same run) and runs them to
the end: concierge cases get the pre-fill and move to "Ready for review";
self-serve ventures get their readiness report. A cron process is not recycled
by cPanel/Passenger mid-run the way the web process (and its threads) can be.

A lock file keeps one worker at a time: a long run keeps the lock, and the
next minute's cron start sees it and exits. When the queue is empty the script
exits at once, so an idle minute costs one database query.

cPanel cron command (same interpreter as cron_mail.py; use the folder the app
runs from — the error log shows /home/dburriyy6pdz/public_html/simisi):
  * * * * * /home/dburriyy6pdz/virtualenv/public_html/simisi/3.9/bin/python \
    /home/dburriyy6pdz/public_html/simisi/cron_evaluations.py >> \
    /home/dburriyy6pdz/public_html/simisi/cron.log 2>&1

It reads the app folder's .env (config.py), so ENCRYPTION_KEY and the Claude
API key must be in that .env: cron does not get the cPanel app environment.
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
    format='[%(asctime)s] %(levelname)s cron_evaluations %(message)s',
)
log = logging.getLogger(__name__)

LOCK_PATH = os.path.join(APP_ROOT, '.cron_evaluations.lock')


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
            return 0             # the previous worker is still running a long evaluation
    from app import create_app
    app = create_app('production')
    with app.app_context():
        from app.services import founder_evaluation_service as fes
        try:
            n = fes.process_queue()
            if n:
                log.info('ran %d queued evaluation(s)', n)
        except Exception as exc:
            log.error('evaluation worker failed: %s', exc, exc_info=True)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
