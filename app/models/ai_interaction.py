from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class AIInteraction(db.Model):
    __tablename__ = 'ai_interactions'

    TYPE_ZONE_EXTRACT = 'zone_extract'
    TYPE_LAYER_GENERATE = 'layer_generate'
    TYPE_LAYER_REFINE = 'layer_refine'
    TYPE_LINKEDIN_NORMALIZE = 'linkedin_normalize'
    TYPE_AGENT_ACTION = 'agent_action'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    user_id = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    simulation_id = db.Column(db.String(9), db.ForeignKey('simulations.id', ondelete='SET NULL'), nullable=True, index=True)
    interaction_type = db.Column(db.String(30), nullable=False)
    prompt_tokens = db.Column(db.Integer, nullable=True)
    completion_tokens = db.Column(db.Integer, nullable=True)
    model = db.Column(db.String(100), nullable=False, default='claude-sonnet-4-20250514')
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f'<AIInteraction {self.interaction_type} for user {self.user_id}>'
