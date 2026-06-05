from app import create_app
from celery import Celery


def make_celery(app):
    celery = Celery(
        app.import_name,
        broker=app.config['CELERY_BROKER_URL'],
        backend=app.config['CELERY_RESULT_BACKEND'],
    )
    celery.conf.update(
        result_backend=app.config['CELERY_RESULT_BACKEND'],
        task_always_eager=app.config.get('CELERY_TASK_ALWAYS_EAGER', False),
    )

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask
    return celery


flask_app = create_app()
celery = make_celery(flask_app)


# Each prefork worker inherits the parent's connection pool after fork.
# Those inherited sockets are stale — dispose them so every worker opens
# its own fresh connections instead of reusing the parent's dead ones.
from celery.signals import worker_process_init

@worker_process_init.connect
def _dispose_db_pool(**kwargs):
    with flask_app.app_context():
        from app.extensions import db
        db.engine.dispose()

# Import tasks so they are registered with the celery app
import app.tasks.simulation  # noqa
import app.tasks.email       # noqa
import app.tasks.layer6      # noqa
import app.tasks.contacts    # noqa
import app.tasks.agent       # noqa

# Celery beat schedule — Layer 6 orchestrator runs every 15 min and self-throttles per cadence
celery.conf.beat_schedule = {
    'layer6-cycle-check': {
        'task': 'app.tasks.layer6.run_layer6_cycles',
        'schedule': 900,  # Every 15 min; cadence checked inside task
    },
    'layer6-stale-cleanup': {
        'task': 'app.tasks.layer6.cleanup_stale_actions',
        'schedule': 900,  # Every 15 min
    },
}
celery.conf.timezone = 'UTC'
