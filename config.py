import os
from dotenv import load_dotenv

# Explicit path so load_dotenv works regardless of working directory (e.g. Passenger)
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'jwt-secret-change-in-production')
    JWT_EXPIRY_DAYS = 7
    EMAIL_PROVIDER = os.environ.get('EMAIL_PROVIDER', 'smtp')  # 'smtp' or 'sendgrid'
    SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@simulacrum.ai')
    CLAUDE_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
    CLAUDE_MODEL = 'claude-sonnet-4-20250514'
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
    STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')
    SIMULATION_PRICE_CENTS = 1000  # $10.00
    LINKEDIN_CLIENT_ID = os.environ.get('LINKEDIN_CLIENT_ID')
    LINKEDIN_CLIENT_SECRET = os.environ.get('LINKEDIN_CLIENT_SECRET')
    LINKEDIN_REDIRECT_URI = os.environ.get('LINKEDIN_REDIRECT_URI', 'http://localhost:5000/api/resumes/linkedin/callback')
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'http://localhost:5000/api/auth/google/callback')
    ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY')  # AES-256 for OAuth tokens
    APOLLO_CLIENT_ID = os.environ.get('APOLLO_CLIENT_ID')
    APOLLO_CLIENT_SECRET = os.environ.get('APOLLO_CLIENT_SECRET')
    APOLLO_REDIRECT_URI = os.environ.get('APOLLO_REDIRECT_URI', 'http://localhost:5000/api/integrations/apollo/callback')
    BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
    STRIPE_CLIENT_ID = os.environ.get('STRIPE_CLIENT_ID')                          # ca_xxxx (Connect app client ID)
    STRIPE_CONNECT_WEBHOOK_SECRET = os.environ.get('STRIPE_CONNECT_WEBHOOK_SECRET') # whsec_xxxx for Connect events
    CAL_CLIENT_ID = os.environ.get('CAL_CLIENT_ID')
    CAL_CLIENT_SECRET = os.environ.get('CAL_CLIENT_SECRET')
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB
    ALLOWED_EXTENSIONS = {'pdf', 'docx'}
    CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'cache+memory://')
    BCRYPT_LOG_ROUNDS = 12


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///simulacrum_dev.db')
    SQLALCHEMY_ECHO = False
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    CELERY_TASK_ALWAYS_EAGER = True  # Run tasks synchronously in dev (no Redis needed)


class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///simulacrum.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    BCRYPT_LOG_ROUNDS = 14
    # Run Celery tasks synchronously when no Redis/worker is available (e.g. shared hosting)
    CELERY_TASK_ALWAYS_EAGER = os.environ.get('REDIS_URL') is None
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,   # test connection before use; auto-reconnect if stale
        'pool_recycle': 280,     # recycle before GoDaddy's ~300s wait_timeout
        'pool_size': 5,
        'max_overflow': 2,
    }


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    CELERY_TASK_ALWAYS_EAGER = True
    BCRYPT_LOG_ROUNDS = 4


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}
