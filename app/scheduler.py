"""
In-process background scheduler for environments where Celery Beat cannot run
as a separate process (e.g. GoDaddy cPanel / Passenger shared hosting).

APScheduler's BackgroundScheduler spawns a single daemon thread inside the
Flask process and fires jobs on the configured interval.  It is started once
in create_app() and is skipped entirely in testing mode.
"""
import logging
import os

logger = logging.getLogger(__name__)

_scheduler = None


def _refresh_pool(app):
    """Dispose the connection pool so the next DB access gets a fresh connection.

    APScheduler reuses threads between ticks. The scoped session for this thread
    may hold a checked-out connection from the previous job invocation. If MySQL
    dropped that connection during the idle interval, engine.dispose() alone won't
    help — the session still holds the stale connection. Call session.remove()
    first to return the connection to the pool, then dispose to close all pooled
    connections. The next DB access opens a clean connection.
    """
    try:
        from app.extensions import db
        db.session.remove()
        db.engine.dispose()
    except Exception:
        pass


def _layer6_cycle_job(app):
    with app.app_context():
        _refresh_pool(app)
        try:
            from app.tasks.layer6 import run_layer6_cycles
            run_layer6_cycles()
        except Exception:
            logger.exception('APScheduler: layer6 cycle job failed')


def _layer6_cleanup_job(app):
    with app.app_context():
        _refresh_pool(app)
        try:
            from app.tasks.layer6 import cleanup_stale_actions
            cleanup_stale_actions()
        except Exception:
            logger.exception('APScheduler: layer6 stale-cleanup job failed')


def _proactive_alerts_job(app):
    with app.app_context():
        _refresh_pool(app)
        try:
            from app.services.proactive_alerts_service import check_proactive_alerts
            check_proactive_alerts()
        except Exception:
            logger.exception('APScheduler: proactive alerts job failed')


def _alert_digest_job(app):
    with app.app_context():
        _refresh_pool(app)
        try:
            from app.services.proactive_alerts_service import send_alert_digest
            send_alert_digest()
        except Exception:
            logger.exception('APScheduler: alert digest job failed')


def _outreach_drip_job(app):
    with app.app_context():
        _refresh_pool(app)
        try:
            from app.services.outreach_campaign_service import (
                process_drip_queue, process_scheduled_broadcasts,
            )
            process_drip_queue()
            process_scheduled_broadcasts()
        except Exception:
            logger.exception('APScheduler: outreach drip/broadcast job failed')


def start_scheduler(app):
    """Start the background scheduler.  Safe to call multiple times — no-ops if already running.
    If APScheduler is not installed the function logs a warning and returns — the app still starts."""
    global _scheduler

    if app.testing:
        return

    # In dev with the Werkzeug reloader, the app starts twice; only start the
    # scheduler in the actual worker process (the one with WERKZEUG_RUN_MAIN=true),
    # or in production where that env var is absent.
    if app.debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return

    if _scheduler is not None and _scheduler.running:
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning('APScheduler not installed — layer6 auto-cycles disabled. '
                       'Run: pip install APScheduler>=3.10,<4')
        return

    _scheduler = BackgroundScheduler(timezone='UTC')

    _scheduler.add_job(
        _layer6_cycle_job,
        'interval',
        seconds=900,
        args=[app],
        id='layer6-cycle-check',
        replace_existing=True,
    )
    _scheduler.add_job(
        _layer6_cleanup_job,
        'interval',
        seconds=900,
        args=[app],
        id='layer6-stale-cleanup',
        replace_existing=True,
    )

    _scheduler.add_job(
        _proactive_alerts_job,
        'interval',
        seconds=900,
        args=[app],
        id='proactive-alerts-check',
        replace_existing=True,
    )

    _scheduler.add_job(
        _alert_digest_job,
        'cron',
        hour=8,
        minute=0,
        args=[app],
        id='alert-digest-daily',
        replace_existing=True,
    )

    # SIM-PRD-OUTREACH-001: drip queue + scheduled broadcasts. Runs every 30 min;
    # the 12h/Day-7/Day-14 cadence is enforced per-send by scheduled_at, so a
    # sub-hour tick just means due emails go out promptly.
    _scheduler.add_job(
        _outreach_drip_job,
        'interval',
        seconds=1800,
        args=[app],
        id='outreach-drip-check',
        replace_existing=True,
    )

    _scheduler.start()
    logger.info('APScheduler started — layer6 cycle-check every 900 s')
