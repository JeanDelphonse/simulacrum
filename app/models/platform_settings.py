from datetime import datetime
from app.extensions import db
from utils.id_gen import generate_id


class PlatformSetting(db.Model):
    __tablename__ = 'platform_settings'

    id = db.Column(db.String(9), primary_key=True, default=generate_id)
    key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=False)
    updated_by = db.Column(db.String(9), db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls, key, default=None):
        setting = cls.query.filter_by(key=key).first()
        return setting.value if setting else default

    @classmethod
    def set(cls, key, value, updated_by=None):
        setting = cls.query.filter_by(key=key).first()
        if setting:
            setting.value = str(value)
            setting.updated_by = updated_by
            setting.updated_at = datetime.utcnow()
        else:
            setting = cls(key=key, value=str(value), updated_by=updated_by)
            db.session.add(setting)
        db.session.commit()
        return setting

    def __repr__(self):
        return f'<PlatformSetting {self.key}={self.value}>'
