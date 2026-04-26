from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class AgentContext(db.Model):
    """Persists user-supplied prompt answers per simulation layer.

    layer_number=0 means cross-simulation / shared-across-layers context.
    Layer-specific values (layer_number > 0) take precedence over layer_number=0
    when both are present for the same key.
    """
    __tablename__ = 'agent_context'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    # layer_number=0 = cross-layer / simulation-wide context
    layer_number = db.Column(db.Integer, nullable=False, default=0)
    context_key = db.Column(db.String(100), nullable=False)
    context_value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint(
            'simulation_id', 'layer_number', 'context_key',
            name='uq_agent_context_sim_layer_key',
        ),
    )

    @classmethod
    def get_for_layer(cls, simulation_id, layer_number):
        """Return dict of {key: value} merging cross-layer (0) with layer-specific."""
        rows = cls.query.filter(
            cls.simulation_id == simulation_id,
            cls.layer_number.in_([0, layer_number]),
        ).all()
        # Cross-layer values first, then layer-specific override
        ctx = {}
        for r in sorted(rows, key=lambda x: x.layer_number):
            ctx[r.context_key] = r.context_value
        return ctx

    @classmethod
    def upsert(cls, simulation_id, layer_number, key, value):
        """Insert or update a single context entry (does not commit)."""
        existing = cls.query.filter_by(
            simulation_id=simulation_id,
            layer_number=layer_number,
            context_key=key,
        ).first()
        if existing:
            existing.context_value = str(value) if value is not None else None
            existing.updated_at = datetime.utcnow()
        else:
            db.session.add(cls(
                simulation_id=simulation_id,
                layer_number=layer_number,
                context_key=key,
                context_value=str(value) if value is not None else None,
            ))

    @classmethod
    def save_inputs(cls, simulation_id, layer_number, inputs: dict):
        """Bulk-upsert a dict of inputs for a layer (does not commit)."""
        for key, value in inputs.items():
            if value:
                cls.upsert(simulation_id, layer_number, key, value)

    def __repr__(self):
        return f'<AgentContext {self.simulation_id} L{self.layer_number} {self.context_key}>'
