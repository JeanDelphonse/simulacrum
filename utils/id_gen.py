import secrets
import string
from app.extensions import db

ALPHABET = string.ascii_letters + string.digits  # 62 chars: A-Z a-z 0-9
ID_LENGTH = 9


def generate_id() -> str:
    """Generate a cryptographically secure 9-character alphanumeric ID."""
    return ''.join(secrets.choice(ALPHABET) for _ in range(ID_LENGTH))


def unique_id(model_class) -> str:
    """Generate an ID guaranteed unique within the given model's table."""
    while True:
        candidate = generate_id()
        exists = db.session.query(
            db.exists().where(model_class.id == candidate)
        ).scalar()
        if not exists:
            return candidate
