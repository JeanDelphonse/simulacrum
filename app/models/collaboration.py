from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id
import secrets


class Collaboration(db.Model):
    __tablename__ = 'collaborations'

    PERM_VIEWER = 'viewer'
    PERM_COMMENTER = 'commenter'
    PERM_EDITOR = 'editor'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    invitee_email = db.Column(db.String(255), nullable=False, index=True)
    invitee_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    permission_level = db.Column(db.String(20), nullable=False, default='viewer')
    share_token = db.Column(db.String(64), unique=True, nullable=False,
                            default=lambda: secrets.token_urlsafe(48))
    expires_at = db.Column(db.DateTime, nullable=False)
    accepted_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    activities = db.relationship('CollabActivity', backref='collaborator_record', lazy='dynamic',
                                 foreign_keys='CollabActivity.collaboration_id')

    @property
    def is_active(self):
        from datetime import datetime
        return (
            self.revoked_at is None
            and self.accepted_at is not None
            and self.expires_at > datetime.utcnow()
        )

    def __repr__(self):
        return f'<Collaboration {self.invitee_email} on {self.simulation_id}>'


class CollabActivity(db.Model):
    __tablename__ = 'collab_activities'

    TYPE_COMMENT = 'comment'
    TYPE_REFINEMENT = 'refinement_request'
    TYPE_VIEW = 'view'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    collaborator_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    collaboration_id = db.Column(db.String(9), db.ForeignKey('collaborations.id', ondelete='SET NULL'), nullable=True)
    activity_type = db.Column(db.String(30), nullable=False)
    layer_number = db.Column(db.Integer, nullable=True)
    content = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'activity_type': self.activity_type,
            'layer_number': self.layer_number,
            'content': self.content,
            'collaborator_id': self.collaborator_id,
            'created_at': self.created_at.isoformat(),
        }

    def __repr__(self):
        return f'<CollabActivity {self.activity_type} on sim {self.simulation_id}>'
