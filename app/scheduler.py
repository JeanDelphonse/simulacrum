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


def _run_with_retry(app, label, fn):
    """Run a scheduled job with a fresh DB connection, retrying ONCE if the pooled
    connection was reset. On shared hosting MySQL drops idle connections, so a
    job's first query can fail with "server has gone away" even after _refresh_pool
    — disposing the pool and retrying gets a clean connection instead of failing
    the whole job (which would skip that tick's work entirely)."""
    from sqlalchemy.exc import OperationalError, DBAPIError
    with app.app_context():
        _refresh_pool(app)
        try:
            fn()
        except (OperationalError, DBAPIError) as exc:
            logger.warning('APScheduler: %s hit a stale DB connection (%s) — '
                           'disposing pool and retrying once', label, exc.__class__.__name__)
            _refresh_pool(app)
            try:
                fn()
            except Exception:
                logger.exception('APScheduler: %s failed after retry', label)
        except Exception:
            logger.exception('APScheduler: %s failed', label)


def _layer6_cycle_job(app):
    def _run():
        from app.tasks.layer6 import run_layer6_cycles
        run_layer6_cycles()
    _run_with_retry(app, 'layer6 cycle job', _run)


def _layer6_cleanup_job(app):
    def _run():
        from app.tasks.layer6 import cleanup_stale_actions
        cleanup_stale_actions()
    _run_with_retry(app, 'layer6 stale-cleanup job', _run)


def _proactive_alerts_job(app):
    def _run():
        from app.services.proactive_alerts_service import check_proactive_alerts
        check_proactive_alerts()
    _run_with_retry(app, 'proactive alerts job', _run)


def _alert_digest_job(app):
    def _run():
        from app.services.proactive_alerts_service import send_alert_digest
        send_alert_digest()
    _run_with_retry(app, 'alert digest job', _run)


def _outreach_drip_job(app):
    def _run():
        from app.services.outreach_campaign_service import (
            process_drip_queue, process_scheduled_broadcasts,
        )
        process_drip_queue()
        process_scheduled_broadcasts()
    _run_with_retry(app, 'outreach drip/broadcast job', _run)


def _sme_rec_expiry_job(app):
    """SIM-PRD-SME-002 §4 — auto-expire untouched recommendations past their window."""
    def _run():
        from app.services.sme_console_service import expire_stale_recommendations
        n = expire_stale_recommendations()
        if n:
            logger.info('APScheduler: expired %d stale SME recommendations', n)
    _run_with_retry(app, 'SME recommendation expiry job', _run)


def _admin_crm_briefing_job(app):
    """SIM-PRD-CRM-001 §3 — draft the day's outreach touches and email the founder.

    Drafts and reminds only; no touch is ever sent to a prospect from here.
    """
    def _run():
        from app.services.admin_crm_service import run_morning_briefing
        result = run_morning_briefing()
        if result.get('due'):
            logger.info('APScheduler: outreach briefing — %d due, %d overdue, emailed=%s',
                        result.get('due'), result.get('overdue', 0), result.get('emailed'))
    _run_with_retry(app, 'admin outreach briefing job', _run)


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

    # SIM-PRD-SME-002: expire untouched SME recommendations once a day.
    _scheduler.add_job(
        _sme_rec_expiry_job,
        'cron',
        hour=3,
        minute=30,
        args=[app],
        id='sme-rec-expiry-daily',
        replace_existing=True,
    )

    # SIM-PRD-CRM-001: the founder-ops morning briefing. Pinned to Pacific rather
    # than the scheduler's UTC default so it stays at 6:30 local across DST.
    _scheduler.add_job(
        _admin_crm_briefing_job,
        'cron',
        hour=6,
        minute=30,
        day_of_week='mon-fri',
        timezone='America/Los_Angeles',
        args=[app],
        id='admin-crm-briefing',
        replace_existing=True,
    )

    _scheduler.start()
    logger.info('APScheduler started — layer6 cycle-check every 900 s')
