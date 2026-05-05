from celery_worker import celery
import logging

logger = logging.getLogger(__name__)


@celery.task
def score_contact(contact_id: str):
    """Async Celery task: calculate qualifying_score for a contact via Haiku 4.5."""
    from app.extensions import db
    from app.models.contact import Contact
    from app.services.contact_scoring import score_contact_sync

    contact = Contact.query.get(contact_id)
    if not contact:
        logger.error('Contact %s not found for scoring', contact_id)
        return

    score = score_contact_sync(contact_id)
    if score is not None:
        contact.qualifying_score = score
        db.session.commit()
        logger.info('Contact %s scored %.3f', contact_id, score)
    else:
        logger.warning('Contact %s scoring returned None', contact_id)
