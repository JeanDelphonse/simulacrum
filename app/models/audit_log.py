from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id
import json


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    action = db.Column(db.String(100), nullable=False)
    resource_id = db.Column(db.String(9), nullable=True)
    _metadata = db.Column('metadata', db.Text, nullable=True)  # JSON
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def extra(self):
        if self._metadata:
            return json.loads(self._metadata)
        return {}

    @extra.setter
    def extra(self, value):
        self._metadata = json.dumps(value) if value else None

    @classmethod
    def log(cls, action, user_id=None, resource_id=None, metadata=None):
        entry = cls(
            action=action,
            user_id=user_id,
            resource_id=resource_id,
        )
        if metadata:
            entry.extra = metadata
        db.session.add(entry)
        # Don't commit here — let the caller's transaction handle it
        return entry

    def __repr__(self):
        return f'<AuditLog {self.action} by {self.user_id}>'
