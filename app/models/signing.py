from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class SigningDocument(db.Model):
    __tablename__ = 'signing_documents'

    id                      = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id                 = db.Column(db.String(9), nullable=False, index=True)
    simulation_id           = db.Column(db.String(9), nullable=False, index=True)
    action_id               = db.Column(db.String(9), nullable=True, index=True)
    action_type             = db.Column(db.String(100), nullable=False)
    artifact_version_id     = db.Column(db.String(9), nullable=True)
    layer_number            = db.Column(db.SmallInteger, nullable=False, default=1)
    pandadoc_document_id    = db.Column(db.String(200), nullable=False, index=True)
    recipient_email         = db.Column(db.String(255), nullable=False)
    recipient_name          = db.Column(db.String(200), nullable=True)
    document_title          = db.Column(db.String(500), nullable=True)
    status                  = db.Column(db.String(20), nullable=False, default='sent')
    sent_at                 = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    viewed_at               = db.Column(db.DateTime, nullable=True)
    signed_at               = db.Column(db.DateTime, nullable=True)
    declined_at             = db.Column(db.DateTime, nullable=True)
    created_at              = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'simulation_id': self.simulation_id,
            'action_id': self.action_id,
            'action_type': self.action_type,
            'artifact_version_id': self.artifact_version_id,
            'layer_number': self.layer_number,
            'pandadoc_document_id': self.pandadoc_document_id,
            'recipient_email': self.recipient_email,
            'recipient_name': self.recipient_name,
            'document_title': self.document_title,
            'status': self.status,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'viewed_at': self.viewed_at.isoformat() if self.viewed_at else None,
            'signed_at': self.signed_at.isoformat() if self.signed_at else None,
            'declined_at': self.declined_at.isoformat() if self.declined_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
