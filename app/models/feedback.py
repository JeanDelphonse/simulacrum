from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id

_LAYER_NAMES = {
    1: 'Active Income',
    2: 'Leveraged Income',
    3: 'Productized Income',
    4: 'Automated Residual',
    5: 'Wealth Deployment',
    6: 'Growth Orchestrator',
}


class UserFeedback(db.Model):
    __tablename__ = 'user_feedback'

    id            = db.Column(db.CHAR(9), primary_key=True, default=generate_id)
    user_id       = db.Column(db.CHAR(9), db.ForeignKey('users.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    simulation_id = db.Column(db.CHAR(9), db.ForeignKey('simulations.id', ondelete='SET NULL'),
                              nullable=True)
    star_rating   = db.Column(db.Integer, nullable=False)
    layers_attributed = db.Column(db.JSON)
    outcome_text  = db.Column(db.String(300), nullable=False)
    quote_text    = db.Column(db.String(200), nullable=False)
    name_display  = db.Column(
        db.Enum('full', 'first_last_initial', 'first_only', 'anonymous'),
        nullable=False, default='first_last_initial',
    )
    status = db.Column(
        db.Enum('pending', 'approved', 'rejected'),
        nullable=False, default='pending',
    )
    admin_note    = db.Column(db.String(500), nullable=True)
    approved_by   = db.Column(db.CHAR(9), db.ForeignKey('users.id', ondelete='SET NULL'),
                              nullable=True)
    approved_at   = db.Column(db.DateTime, nullable=True)
    is_featured   = db.Column(db.Boolean, nullable=False, default=False)
    display_order = db.Column(db.Integer, nullable=True)
    expertise_zone_snapshot    = db.Column(db.String(500), nullable=True)
    withdrawn_requested_at     = db.Column(db.DateTime, nullable=True)
    submitted_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = db.Column(db.DateTime, onupdate=datetime.utcnow)

    @property
    def display_name_computed(self):
        from app.models.user import User
        from app.models.profile import UserProfile
        user = User.query.get(self.user_id)
        profile = UserProfile.query.filter_by(user_id=self.user_id).first()
        name = (profile.display_name if profile else None) or (user.full_name if user else 'Simulacrum User')
        parts = name.strip().split()
        if self.name_display == 'full':
            return name
        if self.name_display == 'first_last_initial':
            return f'{parts[0]} {parts[-1][0]}.' if len(parts) > 1 else parts[0]
        if self.name_display == 'first_only':
            return parts[0]
        return 'Simulacrum User'

    def layer_names_list(self):
        if not self.layers_attributed:
            return []
        out = []
        for n in self.layers_attributed:
            if n == 0:
                out.append({'num': 0, 'label': 'General / Overall'})
            elif n in _LAYER_NAMES:
                out.append({'num': n, 'label': f'L{n}: {_LAYER_NAMES[n]}'})
        return out

    def to_public_dict(self):
        return {
            'id':           self.id,
            'star_rating':  self.star_rating,
            'quote_text':   self.quote_text,
            'display_name': self.display_name_computed,
            'expertise_zone': self.expertise_zone_snapshot,
            'layers':       self.layer_names_list(),
            'is_featured':  self.is_featured,
            'approved_at':  self.approved_at.strftime('%B %Y') if self.approved_at else None,
        }
