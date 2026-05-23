from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class KajabiProduct(db.Model):
    """A Kajabi product created by an agent action — course, membership, workshop, or coaching."""
    __tablename__ = 'kajabi_products'

    id                 = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id            = db.Column(db.String(9), nullable=False, index=True)
    simulation_id      = db.Column(db.String(9), nullable=False, index=True)
    action_id          = db.Column(db.String(9), nullable=True, index=True)
    artifact_id        = db.Column(db.String(9), nullable=True)
    kajabi_product_id  = db.Column(db.String(200), nullable=True, index=True)
    product_type       = db.Column(db.String(50), nullable=False)
    # 'course' | 'membership' | 'workshop' | 'group_coaching'
    name               = db.Column(db.String(500), nullable=False)
    checkout_url       = db.Column(db.String(1000), nullable=True)
    status             = db.Column(db.String(20), nullable=False, default='active')
    created_at         = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'kajabi_product_id': self.kajabi_product_id,
            'product_type': self.product_type,
            'name': self.name,
            'checkout_url': self.checkout_url,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
