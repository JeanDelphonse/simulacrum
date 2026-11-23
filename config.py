import os
from dotenv import load_dotenv

# Explicit path so load_dotenv works regardless of working directory (e.g. Passenger)
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-in-production')
    # Cookies (security review 2026-11): Lax keeps the session and remember
    # cookies off cross-site POSTs; remember-me lasts 30 days, not 365.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_DURATION = __import__('datetime').timedelta(days=30)
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'jwt-secret-change-in-production')
    JWT_EXPIRY_DAYS = 7
    EMAIL_PROVIDER = os.environ.get('EMAIL_PROVIDER', 'smtp')  # 'smtp' or 'sendgrid'
    SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'simi@simisi.io')
    MAIL_DEFAULT_SENDER_NAME = os.environ.get('MAIL_DEFAULT_SENDER_NAME', 'SimiSI')
    # Placeholder address assigned to prospects with no verified email; sends to
    # these land in an inbox the operator controls, never a real third party.
    FALLBACK_CONTACT_EMAIL = os.environ.get('FALLBACK_CONTACT_EMAIL', 'valuemanager.management@gmail.com')
    # Where internal founder-facing mail goes (SIM-PRD-CRM-001 outreach briefing).
    # Deliberately separate from MAIL_DEFAULT_SENDER, which is a From address.
    FOUNDER_EMAIL = os.environ.get('FOUNDER_EMAIL', 'simi@simisi.io')
    # Amazon listing for The Wealth Simulation Playbook, linked from the homepage.
    # Canonical /dp/<ASIN> form on purpose: the search-session parameters on a
    # copied Amazon URL (crid, dib, qid, sprefix, sr) are one visitor's session and
    # go stale. Env-overridable so a new edition, a different marketplace or an
    # affiliate tag needs no code change.
    PLAYBOOK_URL = os.environ.get(
        'PLAYBOOK_URL', 'https://www.amazon.com/dp/B0HDYH1PNM')
    CLAUDE_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
    CLAUDE_MODEL = 'claude-sonnet-4-6'
    ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY')
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
    STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY')
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')
    SIMULATION_PRICE_CENTS = 69500  # $695.00
    LINKEDIN_CLIENT_ID = os.environ.get('LINKEDIN_CLIENT_ID')
    LINKEDIN_CLIENT_SECRET = os.environ.get('LINKEDIN_CLIENT_SECRET')
    LINKEDIN_REDIRECT_URI = os.environ.get('LINKEDIN_REDIRECT_URI', 'https://simisi.io/api/resumes/linkedin/callback')
    # SIM-PRD-PRIVACY-001: public (no-login) callback for bio-page access requests.
    # Must ALSO be registered as an authorized redirect URL on the LinkedIn app.
    LINKEDIN_BIO_REDIRECT_URI = os.environ.get('LINKEDIN_BIO_REDIRECT_URI', 'https://simisi.io/bio/access/linkedin/callback')
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'https://simisi.io/api/auth/google/callback')
    ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY')  # AES-256 for OAuth tokens
    # Wall-clock cap on one prospect-research pass (Apollo + web search + email
    # verification). Unbounded, Cold Email with an exhausted CRM pool ran past the
    # 15-minute mark where the UI marks a run failed.
    RESEARCH_TIME_BUDGET_SECONDS = int(os.environ.get('RESEARCH_TIME_BUDGET_SECONDS', 180))
    APOLLO_CLIENT_ID = os.environ.get('APOLLO_CLIENT_ID')
    APOLLO_CLIENT_SECRET = os.environ.get('APOLLO_CLIENT_SECRET')
    APOLLO_REDIRECT_URI = os.environ.get('APOLLO_REDIRECT_URI', 'https://simisi.io/api/integrations/apollo/callback')
    PANDADOC_CLIENT_ID = os.environ.get('PANDADOC_CLIENT_ID')
    PANDADOC_CLIENT_SECRET = os.environ.get('PANDADOC_CLIENT_SECRET')
    PANDADOC_REDIRECT_URI = os.environ.get('PANDADOC_REDIRECT_URI', 'https://simisi.io/api/integrations/pandadoc/callback')
    BASE_URL = os.environ.get('BASE_URL', 'https://simisi.io')
    STRIPE_CLIENT_ID = os.environ.get('STRIPE_CLIENT_ID')                          # ca_xxxx (Connect app client ID)
    STRIPE_CONNECT_WEBHOOK_SECRET = os.environ.get('STRIPE_CONNECT_WEBHOOK_SECRET') # whsec_xxxx for Connect events
    # SendGrid webhooks (app/services/webhook_auth.py). Events: the signed-webhook
    # verification key from SendGrid (Settings -> Mail Settings -> Event Webhook ->
    # Signature Verification), and/or a random token added to the webhook URL as ?k=.
    # Inbound Parse: a random token added to the parse URL as ?k=. Unset = refused.
    SENDGRID_WEBHOOK_PUBLIC_KEY = os.environ.get('SENDGRID_WEBHOOK_PUBLIC_KEY')
    SENDGRID_WEBHOOK_TOKEN = os.environ.get('SENDGRID_WEBHOOK_TOKEN')
    SENDGRID_INBOUND_TOKEN = os.environ.get('SENDGRID_INBOUND_TOKEN')
    # Bearer secret for the digest cron endpoint; unset = endpoint refused.
    DIGEST_CRON_SECRET = os.environ.get('DIGEST_CRON_SECRET')
    CAL_CLIENT_ID = os.environ.get('CAL_CLIENT_ID')
    CAL_CLIENT_SECRET = os.environ.get('CAL_CLIENT_SECRET')
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20 MB (Founder Track pitch decks; owner approved 2026-11-13)
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
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///simulacrum.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    BCRYPT_LOG_ROUNDS = 12
    # Run Celery tasks synchronously when no Redis/worker is available (e.g. shared hosting)
    CELERY_TASK_ALWAYS_EAGER = os.environ.get('REDIS_URL') is None
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,   # test connection before use; auto-reconnect if stale
        'pool_recycle': 180,     # recycle well before GoDaddy's ~300s wait_timeout
        'pool_size': 5,
        'max_overflow': 2,
        'connect_args': {'connect_timeout': 10},
    }

    @classmethod
    def init_app(cls, app):
        sk = os.environ.get('SECRET_KEY') or ''
        if not sk or sk == 'dev-secret-change-in-production' or len(sk) < 16:
            # With the public default anyone could forge session cookies and
            # signed links (unsubscribe, approvals, invites) for any user.
            raise RuntimeError('SECRET_KEY must be set to a long random value in production '
                               '(cPanel -> Setup Python App -> Environment variables).')
        key = os.environ.get('ENCRYPTION_KEY')
        if not key:
            raise RuntimeError("ENCRYPTION_KEY environment variable is required in production configuration.")


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = False
    CELERY_TASK_ALWAYS_EAGER = True
    BCRYPT_LOG_ROUNDS = 4
    RATE_LIMITS_ENABLED = False      # utils/rate_limit.py; test_security_fixes.py turns it on


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}
