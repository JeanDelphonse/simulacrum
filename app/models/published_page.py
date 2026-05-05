from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class PublishedPage(db.Model):
    __tablename__ = 'published_pages'

    id                  = db.Column(db.String(9), primary_key=True, default=generate_id)
    slug                = db.Column(db.String(200), nullable=False, unique=True, index=True)
    user_id             = db.Column(db.String(9), nullable=False, index=True)
    simulation_id       = db.Column(db.String(9), nullable=False, index=True)
    action_id           = db.Column(db.String(9), nullable=True, index=True)
    action_type         = db.Column(db.String(100), nullable=False)
    artifact_version_id = db.Column(db.String(9), nullable=True)
    layer_number        = db.Column(db.SmallInteger, nullable=False, default=3)
    title               = db.Column(db.String(500), nullable=True)
    html_content        = db.Column(db.Text, nullable=False)
    status              = db.Column(db.String(20), nullable=False, default='live')
    created_at          = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at          = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                                    onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'slug': self.slug,
            'user_id': self.user_id,
            'simulation_id': self.simulation_id,
            'action_id': self.action_id,
            'action_type': self.action_type,
            'artifact_version_id': self.artifact_version_id,
            'layer_number': self.layer_number,
            'title': self.title,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
