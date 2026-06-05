from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class SocialPostQueue(db.Model):
    __tablename__ = 'social_post_queue'

    STATUS_PENDING  = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'

    id               = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id          = db.Column(db.String(9), nullable=False, index=True)
    platform         = db.Column(db.String(30), nullable=False)
    simulation_id    = db.Column(db.String(9), nullable=True)
    artifact_id      = db.Column(db.String(9), nullable=True)
    post_text        = db.Column(db.Text, nullable=False)
    status           = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    action_item_id   = db.Column(db.String(9), nullable=True)
    platform_post_id = db.Column(db.String(200), nullable=True)
    reviewed_at      = db.Column(db.DateTime, nullable=True)
    reviewed_by      = db.Column(db.String(9), nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index('idx_spq_user_status', 'user_id', 'status', 'created_at'),
    )
