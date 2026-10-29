"""Execution-layer tables for artifacts (EXEC_PLAN P2).

``artifact_id`` is ``agent_actions.id`` — the existing artifact object
(EXEC_PLAN §2: reuse agent_actions rather than a parallel artifacts table).
"""
from datetime import datetime
import json

from app.extensions import db
from utils.id_gen import generate_id


class ArtifactUnit(db.Model):
    """An actionable piece of an artifact: a sequence step, a schedule entry, or
    a per-recipient variant of a message (send-to-contacts drafts)."""
    __tablename__ = 'artifact_units'
    __table_args__ = (
        db.Index('ix_artifact_units_artifact', 'artifact_id', 'status'),
    )

    KIND_STEP = 'step'
    KIND_ENTRY = 'entry'
    KIND_VARIANT = 'variant'

    STATUS_DRAFT = 'draft'          # prepared, previewed, NOT sent
    STATUS_DISCARDED = 'discarded'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    artifact_id = db.Column(db.String(9), nullable=False)
    batch_id = db.Column(db.String(9), nullable=True)    # one "prepare" run
    seq_index = db.Column(db.Integer, nullable=False, default=0)
    kind = db.Column(db.String(20), nullable=False, default=KIND_VARIANT)
    contact_id = db.Column(db.String(9), nullable=True)
    subject = db.Column(db.String(300), nullable=True)
    body_ref = db.Column(db.Text, nullable=True)           # the unit's resolved body
    _warnings = db.Column('warnings', db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_DRAFT)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def warnings(self) -> list:
        return json.loads(self._warnings) if self._warnings else []

    @warnings.setter
    def warnings(self, value):
        self._warnings = json.dumps(value) if value else None

    def to_dict(self):
        return {
            'id': self.id, 'artifact_id': self.artifact_id, 'batch_id': self.batch_id,
            'seq_index': self.seq_index, 'kind': self.kind, 'contact_id': self.contact_id,
            'subject': self.subject, 'body': self.body_ref, 'warnings': self.warnings,
            'status': self.status, 'created_at': self.created_at.isoformat(),
        }


class ArtifactAction(db.Model):
    """Log of every verb applied to an artifact or one of its units."""
    __tablename__ = 'artifact_actions'
    __table_args__ = (
        db.Index('ix_artifact_actions_artifact', 'artifact_id', 'ts'),
    )

    ACTOR_USER = 'user'
    ACTOR_AGENT = 'agent'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    artifact_id = db.Column(db.String(9), nullable=False)
    unit_id = db.Column(db.String(9), nullable=True)
    verb = db.Column(db.String(40), nullable=False)
    actor = db.Column(db.String(10), nullable=False, default=ACTOR_USER)
    actor_id = db.Column(db.String(9), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='ok')
    result_ref = db.Column(db.Text, nullable=True)         # small JSON summary
    ts = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
