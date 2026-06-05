"""One-off script to create the initial admin user and seed default platform settings."""
from app import create_app
from app.extensions import db, bcrypt
from app.models.user import User
from app.models.platform_settings import PlatformSetting
from utils.id_gen import generate_id

ADMIN_EMAIL = 'admin@simulacrum.ai'
ADMIN_PASSWORD = 'ChangeMe123!'
ADMIN_NAME = 'Admin'

app = create_app('development')

with app.app_context():
    if User.query.filter_by(email=ADMIN_EMAIL).first():
        print(f'User {ADMIN_EMAIL} already exists.')
    else:
        pw_hash = bcrypt.generate_password_hash(
            ADMIN_PASSWORD, rounds=app.config['BCRYPT_LOG_ROUNDS']
        ).decode('utf-8')
        user = User(
            id=generate_id(),
            email=ADMIN_EMAIL,
            password_hash=pw_hash,
            full_name=ADMIN_NAME,
            email_verified=True,
            is_admin=True,
        )
        db.session.add(user)
        db.session.commit()
        print(f'Admin user created: {ADMIN_EMAIL}')

# Seed default platform settings
with app.app_context():
    if not PlatformSetting.get('simulation_price'):
        PlatformSetting.set('simulation_price', '69500')
        db.session.commit()
        print('Seeded platform setting: simulation_price = 69500 cents ($695.00)')
    else:
        print(f'simulation_price already set: {PlatformSetting.get("simulation_price")} cents')
