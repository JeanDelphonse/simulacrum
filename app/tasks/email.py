from celery_worker import celery
import logging

logger = logging.getLogger(__name__)


@celery.task
def send_verification_email_task(user_email: str, user_name: str, token: str):
    from app.services.email_service import send_verification_email
    send_verification_email(user_email, user_name, token)


@celery.task
def send_password_reset_email_task(user_email: str, user_name: str, token: str):
    from app.services.email_service import send_password_reset_email
    send_password_reset_email(user_email, user_name, token)


@celery.task
def send_invoice_email_task(user_email: str, user_name: str, sim_name: str, sim_id: str, amount_cents: int = 1000):
    from app.services.email_service import send_invoice_email
    send_invoice_email(user_email, user_name, sim_name, sim_id, amount_cents)


@celery.task
def send_collab_invite_task(invitee_email: str, inviter_name: str, sim_name: str, share_token: str):
    from app.services.email_service import send_collab_invite_email
    send_collab_invite_email(invitee_email, inviter_name, sim_name, share_token)


@celery.task
def send_simulation_failed_task(user_email: str, user_name: str, sim_name: str, sim_id: str, amount_cents: int = 1000):
    from app.services.email_service import send_simulation_failed_email
    send_simulation_failed_email(user_email, user_name, sim_name, sim_id, amount_cents)
