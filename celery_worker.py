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

# Import tasks so they are registered with the celery app
import app.tasks.simulation  # noqa
import app.tasks.email       # noqa
import app.tasks.layer6      # noqa

# Celery beat schedule — Layer 6 orchestrator runs hourly and self-throttles per cadence
celery.conf.beat_schedule = {
    'layer6-cycle-check': {
        'task': 'app.tasks.layer6.run_layer6_cycles',
        'schedule': 3600,  # Every hour; task checks individual cadence internally
    },
}
celery.conf.timezone = 'UTC'
