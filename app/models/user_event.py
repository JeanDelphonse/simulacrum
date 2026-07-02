from datetime import datetime
from app.extensions import db


class UserEvent(db.Model):
    __tablename__ = 'user_events'

    # Lifecycle event types (FR-ANALYTICS-10)
    SIGNUP              = 'signup'
    EMAIL_VERIFIED      = 'email_verified'
    LOGIN               = 'login'
    RESUME_UPLOADED     = 'resume_uploaded'
    BIO_PUBLISHED       = 'bio_published'
    SIMULATION_LAUNCHED = 'simulation_launched'
    FIRST_INCOME        = 'first_income'

    id         = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    user_id    = db.Column(db.String(9),  nullable=False, index=True)
    event_type = db.Column(db.String(30), nullable=False, index=True)
    event_data = db.Column(db.JSON,       nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime,   nullable=False, default=datetime.utcnow)

    @staticmethod
    def track(user_id, event_type, event_data=None, ip_address=None):
        try:
            evt = UserEvent(
                user_id=user_id,
                event_type=event_type,
                event_data=event_data or {},
                ip_address=ip_address,
            )
            db.session.add(evt)
            db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
