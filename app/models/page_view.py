from datetime import datetime
from app.extensions import db


class PageView(db.Model):
    __tablename__ = 'page_views'

    id          = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    path        = db.Column(db.String(500), nullable=False)
    visitor_id  = db.Column(db.String(64),  nullable=False, index=True)
    session_id  = db.Column(db.String(64),  nullable=True)
    referrer    = db.Column(db.String(500), nullable=True)
    user_agent  = db.Column(db.String(500), nullable=True)
    user_id     = db.Column(db.String(9),   nullable=True, index=True)
    country     = db.Column(db.String(2),   nullable=True)
    device_type = db.Column(db.String(10),  nullable=True)
    created_at  = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    @staticmethod
    def record(path, visitor_id, session_id=None, referrer=None,
               user_agent=None, user_id=None, country=None, device_type=None):
        try:
            pv = PageView(
                path=path[:500],
                visitor_id=visitor_id,
                session_id=session_id,
                referrer=(referrer or '')[:500] if referrer else None,
                user_agent=(user_agent or '')[:500] if user_agent else None,
                user_id=user_id,
                country=country,
                device_type=device_type,
            )
            db.session.add(pv)
            db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass
