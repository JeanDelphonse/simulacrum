"""SimulationVideo — narrated MP4 overview generated from simulation data (FR-VOICE-11)."""
from datetime import datetime
from app.extensions import db


class SimulationVideo(db.Model):
    __tablename__ = 'simulation_videos'

    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETE   = 'complete'
    STATUS_FAILED     = 'failed'

    FORMAT_SQUARE    = 'square'
    FORMAT_LANDSCAPE = 'landscape'
    FORMAT_VERTICAL  = 'vertical'

    id               = db.Column(db.String(9),   primary_key=True)
    simulation_id    = db.Column(db.String(9),   db.ForeignKey('simulations.id'), nullable=False, index=True)
    user_id          = db.Column(db.String(9),   db.ForeignKey('users.id'),       nullable=False, index=True)
    script           = db.Column(db.Text,        nullable=True)
    audio_path       = db.Column(db.String(500), nullable=True)
    video_path       = db.Column(db.String(500), nullable=True)
    thumbnail_path   = db.Column(db.String(500), nullable=True)
    format           = db.Column(db.String(20),  nullable=False, default='square')
    duration_seconds = db.Column(db.Integer,     nullable=True)
    embedded_on_bio  = db.Column(db.Boolean,     nullable=False, default=False)
    status           = db.Column(db.String(20),  nullable=False, default='processing')
    created_at       = db.Column(db.DateTime,    nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':               self.id,
            'simulation_id':    self.simulation_id,
            'video_path':       self.video_path,
            'thumbnail_path':   self.thumbnail_path,
            'format':           self.format,
            'duration_seconds': self.duration_seconds,
            'embedded_on_bio':  self.embedded_on_bio,
            'status':           self.status,
            'created_at':       self.created_at.isoformat() if self.created_at else None,
        }
