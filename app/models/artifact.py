from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id
import json


class PrefillCorrection(db.Model):
    __tablename__ = 'prefill_corrections'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    user_id = db.Column(
        db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
    )
    action_type = db.Column(db.String(100), nullable=False)
    field_name = db.Column(db.String(100), nullable=False)
    prefilled_value = db.Column(db.Text, nullable=True)
    corrected_value = db.Column(db.Text, nullable=True)
    prefill_source = db.Column(db.String(100), nullable=True)
    confidence_level = db.Column(db.String(20), nullable=True)  # 'high', 'medium', 'low'
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ArtifactVersion(db.Model):
    __tablename__ = 'artifact_versions'

    EDITED_BY_AGENT    = 'agent'
    EDITED_BY_USER     = 'user'
    EDITED_BY_COPILOT  = 'co-pilot'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    action_id = db.Column(
        db.String(9), db.ForeignKey('agent_actions.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    layer_number = db.Column(db.Integer, nullable=False)
    action_type = db.Column(db.String(100), nullable=False)
    version_number = db.Column(db.Integer, nullable=False, default=1)
    version_label = db.Column(db.String(100), nullable=True)
    content = db.Column(db.Text, nullable=True)
    # SIM-PRD-VIEW-001: edited_by, edit_summary, parent_version_id, draft fields
    edited_by         = db.Column(db.String(20), nullable=False, default=EDITED_BY_AGENT)
    edit_summary      = db.Column(db.String(255), nullable=True)
    parent_version_id = db.Column(db.String(9), nullable=True)
    draft_content     = db.Column(db.Text, nullable=True)
    edited_at         = db.Column(db.DateTime, nullable=True)
    draft_updated_at  = db.Column(db.DateTime, nullable=True)
    file_type = db.Column(db.String(20), nullable=True, default='text')
    _prefill_inputs = db.Column('prefill_inputs', db.Text, nullable=True)
    change_summary = db.Column(db.Text, nullable=True)
    is_current = db.Column(db.Boolean, default=True, nullable=False)
    public_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.Column(db.String(20), default='user')  # legacy: 'user' | 'orchestrator'

    @property
    def prefill_inputs(self):
        return json.loads(self._prefill_inputs) if self._prefill_inputs else {}

    @prefill_inputs.setter
    def prefill_inputs(self, value):
        self._prefill_inputs = json.dumps(value) if value is not None else None

    @classmethod
    def current_for(cls, action_id: str):
        return (cls.query
                .filter_by(action_id=action_id, is_current=True)
                .order_by(cls.version_number.desc())
                .first())

    @classmethod
    def history_for(cls, action_id: str):
        return (cls.query
                .filter_by(action_id=action_id)
                .order_by(cls.version_number.desc())
                .all())

    def to_dict(self):
        return {
            'id': self.id,
            'action_id': self.action_id,
            'simulation_id': self.simulation_id,
            'layer_number': self.layer_number,
            'action_type': self.action_type,
            'version_number': self.version_number,
            'version_label': self.version_label,
            'content': self.content,
            'file_type': self.file_type,
            'prefill_inputs': self.prefill_inputs,
            'change_summary': self.change_summary,
            'edited_by':         self.edited_by,
            'edit_summary':      self.edit_summary,
            'parent_version_id': self.parent_version_id,
            'edited_at':         self.edited_at.isoformat() if self.edited_at else None,
            'has_draft':         bool(self.draft_content),
            'draft_updated_at':  self.draft_updated_at.isoformat() if self.draft_updated_at else None,
            'is_current': self.is_current,
            'public_url': self.public_url,
            'created_at': self.created_at.isoformat(),
            'created_by': self.created_by,
        }


class ArtifactBundle(db.Model):
    __tablename__ = 'artifact_bundles'

    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_READY = 'ready'
    STATUS_FAILED = 'failed'
    STATUS_EXPIRED = 'expired'

    BUNDLE_LAYER_PORTFOLIO = 'layer_portfolio'
    BUNDLE_FULL_SIMULATION = 'full_simulation'
    BUNDLE_ADVISOR_BRIEF = 'advisor_brief'
    BUNDLE_INVESTOR_ONE_PAGER = 'investor_one_pager'
    BUNDLE_CUSTOM = 'custom'

    VALID_BUNDLE_TYPES = (
        BUNDLE_LAYER_PORTFOLIO, BUNDLE_FULL_SIMULATION,
        BUNDLE_ADVISOR_BRIEF, BUNDLE_INVESTOR_ONE_PAGER, BUNDLE_CUSTOM,
    )

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    user_id = db.Column(
        db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
    )
    bundle_type = db.Column(db.String(50), nullable=False)
    bundle_name = db.Column(db.String(200), nullable=True)
    audience = db.Column(db.String(100), nullable=True)
    layer_number = db.Column(db.Integer, nullable=True)
    _artifact_ids = db.Column('artifact_ids', db.Text, nullable=True)  # JSON array of action IDs
    file_path = db.Column(db.String(500), nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_PENDING)
    error_message = db.Column(db.Text, nullable=True)
    download_count = db.Column(db.Integer, default=0, nullable=False)
    auto_generated = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)

    @property
    def artifact_ids(self):
        return json.loads(self._artifact_ids) if self._artifact_ids else []

    @artifact_ids.setter
    def artifact_ids(self, value):
        self._artifact_ids = json.dumps(value) if value is not None else None

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'user_id': self.user_id,
            'bundle_type': self.bundle_type,
            'bundle_name': self.bundle_name,
            'audience': self.audience,
            'layer_number': self.layer_number,
            'artifact_ids': self.artifact_ids,
            'file_path': self.file_path,
            'status': self.status,
            'error_message': self.error_message,
            'download_count': self.download_count,
            'auto_generated': self.auto_generated,
            'created_at': self.created_at.isoformat(),
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
        }


class ArtifactDependency(db.Model):
    __tablename__ = 'artifact_dependencies'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    simulation_id = db.Column(
        db.String(9), db.ForeignKey('simulations.id', ondelete='CASCADE'),
        nullable=False, index=True,
    )
    upstream_action_id = db.Column(
        db.String(9), db.ForeignKey('agent_actions.id', ondelete='CASCADE'),
        nullable=True, index=True,
    )
    upstream_action_type = db.Column(db.String(100), nullable=False)
    downstream_action_type = db.Column(db.String(100), nullable=False)
    _fields_passed = db.Column('fields_passed', db.Text, nullable=True)  # JSON array of field names
    upstream_version_used = db.Column(db.Integer, nullable=True)
    is_stale = db.Column(db.Boolean, default=False, nullable=False)
    stale_detected_at = db.Column(db.DateTime, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def fields_passed(self):
        return json.loads(self._fields_passed) if self._fields_passed else []

    @fields_passed.setter
    def fields_passed(self, value):
        self._fields_passed = json.dumps(value) if value is not None else None

    def to_dict(self):
        return {
            'id': self.id,
            'simulation_id': self.simulation_id,
            'upstream_action_id': self.upstream_action_id,
            'upstream_action_type': self.upstream_action_type,
            'downstream_action_type': self.downstream_action_type,
            'fields_passed': self.fields_passed,
            'upstream_version_used': self.upstream_version_used,
            'is_stale': self.is_stale,
            'stale_detected_at': self.stale_detected_at.isoformat() if self.stale_detected_at else None,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
            'created_at': self.created_at.isoformat(),
        }


class BundleTypeConfig(db.Model):
    __tablename__ = 'bundle_type_configs'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    bundle_type = db.Column(db.String(50), nullable=False, unique=True)
    audience = db.Column(db.String(100), nullable=True)
    _sort_order = db.Column('sort_order', db.Text, nullable=True)       # JSON array of category names
    _artifact_categories = db.Column('artifact_categories', db.Text, nullable=True)  # JSON
    include_cover_pdf = db.Column(db.Boolean, default=True, nullable=False)
    updated_by = db.Column(
        db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
    )
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def sort_order(self):
        return json.loads(self._sort_order) if self._sort_order else []

    @sort_order.setter
    def sort_order(self, value):
        self._sort_order = json.dumps(value) if value is not None else None

    @property
    def artifact_categories(self):
        return json.loads(self._artifact_categories) if self._artifact_categories else {}

    @artifact_categories.setter
    def artifact_categories(self, value):
        self._artifact_categories = json.dumps(value) if value is not None else None

    def to_dict(self):
        return {
            'id': self.id,
            'bundle_type': self.bundle_type,
            'audience': self.audience,
            'sort_order': self.sort_order,
            'artifact_categories': self.artifact_categories,
            'include_cover_pdf': self.include_cover_pdf,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
