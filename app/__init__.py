import os
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask
from config import config
from app.extensions import db, migrate, login_manager, bcrypt, mail, cors


def _configure_logging(app):
    """Write logs to error.log at the project root."""
    log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'error.log')
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=5)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
    ))
    # Route everything through the root logger only.
    # app.logger and werkzeug both propagate to root by default, so attaching
    # the handler only to root avoids every record being written twice.
    root = logging.getLogger()
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', None) == log_path
               for h in root.handlers):
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
    # Prevent Flask's app logger from double-writing via propagation + its own handlers
    app.logger.handlers = []
    app.logger.propagate = True
    app.logger.setLevel(logging.DEBUG)


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get('FLASK_ENV', 'development')

    app = Flask(__name__, template_folder='templates', static_folder='static')
    config_obj = config[config_name]
    app.config.from_object(config_obj)
    if hasattr(config_obj, 'init_app'):
        config_obj.init_app(app)

    # Force UPLOAD_FOLDER to an absolute path relative to this file
    # (relative paths break under Passenger where cwd != app root)
    if not os.path.isabs(app.config.get('UPLOAD_FOLDER', 'uploads')):
        app.config['UPLOAD_FOLDER'] = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            app.config['UPLOAD_FOLDER'],
        )

    _configure_logging(app)
    app.logger.info('App starting up — config=%s', config_name)

    # Ensure upload folder exists
    app.logger.info('startup: makedirs UPLOAD_FOLDER')
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Initialize extensions
    app.logger.info('startup: db.init_app')
    db.init_app(app)
    app.logger.info('startup: migrate.init_app')
    migrate.init_app(app, db)
    app.logger.info('startup: login_manager.init_app')
    login_manager.init_app(app)
    app.logger.info('startup: bcrypt.init_app')
    bcrypt.init_app(app)
    app.logger.info('startup: mail.init_app')
    mail.init_app(app)
    app.logger.info('startup: cors.init_app')
    cors.init_app(app, resources={r'/api/*': {'origins': '*'}})

    login_manager.login_view = 'pages.login_page'
    login_manager.login_message_category = 'info'

    # Register blueprints
    app.logger.info('startup: importing blueprints')
    from app.blueprints.auth import auth_bp
    app.logger.info('startup: auth_bp imported')
    from app.blueprints.resumes import resumes_bp
    app.logger.info('startup: resumes_bp imported')
    from app.blueprints.simulations import simulations_bp
    app.logger.info('startup: simulations_bp imported')
    from app.blueprints.collaboration import collaboration_bp
    app.logger.info('startup: collaboration_bp imported')
    from app.blueprints.payments import payments_bp
    app.logger.info('startup: payments_bp imported')
    from app.blueprints.admin import admin_bp
    app.logger.info('startup: admin_bp imported')
    from app.blueprints.partners import partners_bp
    app.logger.info('startup: partners_bp imported')
    from app.blueprints.layer6 import layer6_bp
    app.logger.info('startup: layer6_bp imported')
    from app.blueprints.artifacts import artifacts_bp
    app.logger.info('startup: artifacts_bp imported')
    from app.blueprints.profile import profile_bp
    app.logger.info('startup: profile_bp imported')
    from app.blueprints.public import public_bp
    app.logger.info('startup: public_bp imported')
    from app.blueprints.feedback import feedback_bp
    app.logger.info('startup: feedback_bp imported')
    from app.blueprints.advisor import advisor_bp
    app.logger.info('startup: advisor_bp imported')
    from app.blueprints.contacts import contacts_bp
    app.logger.info('startup: contacts_bp imported')
    from app.blueprints.income import income_bp
    app.logger.info('startup: income_bp imported')
    from app.blueprints.chat import chat_bp
    app.logger.info('startup: chat_bp imported')
    from app.blueprints.integrations import integrations_bp
    app.logger.info('startup: integrations_bp imported')
    from app.blueprints.publishing import publishing_bp
    app.logger.info('startup: publishing_bp imported')
    from app.blueprints.notifications import notifications_bp
    app.logger.info('startup: notifications_bp imported')
    from app.blueprints.artifact_view import artifact_view_bp
    app.logger.info('startup: artifact_view_bp imported')
    from app.blueprints.bio import bio_bp
    app.logger.info('startup: bio_bp imported')
    from app.blueprints.bio_chat import bio_chat_bp
    app.logger.info('startup: bio_chat_bp imported')
    from app.blueprints.corporate import corporate_bp
    app.logger.info('startup: corporate_bp imported')
    from app.blueprints.social import social_bp
    app.logger.info('startup: social_bp imported')
    from app.blueprints.onboarding import onboarding_bp
    app.logger.info('startup: onboarding_bp imported')

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(resumes_bp, url_prefix='/api/resumes')
    app.register_blueprint(simulations_bp, url_prefix='/api/simulations')
    app.register_blueprint(collaboration_bp)
    app.register_blueprint(payments_bp, url_prefix='/api/payments')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    app.register_blueprint(partners_bp, url_prefix='/api/partners')
    app.register_blueprint(layer6_bp, url_prefix='/api/simulations')
    app.register_blueprint(artifacts_bp, url_prefix='/api/simulations')
    app.register_blueprint(profile_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(advisor_bp, url_prefix='/api/advisor')
    app.register_blueprint(contacts_bp, url_prefix='/api/contacts')
    app.register_blueprint(income_bp, url_prefix='/api/simulations')
    app.register_blueprint(chat_bp, url_prefix='/api/simulations')
    app.register_blueprint(integrations_bp)
    app.register_blueprint(publishing_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(artifact_view_bp)
    app.register_blueprint(bio_bp)
    app.register_blueprint(bio_chat_bp)
    app.register_blueprint(corporate_bp)
    app.register_blueprint(social_bp)
    app.register_blueprint(onboarding_bp)

    # Register page routes
    from app.blueprints.pages import pages_bp
    app.logger.info('startup: pages_bp imported')
    app.register_blueprint(pages_bp)
    app.logger.info('startup: all blueprints registered')

    # Start in-process scheduler (replaces Celery Beat on shared hosting)
    from app.scheduler import start_scheduler
    start_scheduler(app)

    # Onboarding gate: redirect incomplete users to /onboarding
    @app.before_request
    def _onboarding_gate():
        from flask import redirect as _redirect, request as _req
        from flask_login import current_user as _cu

        # Skip API calls, static files, and the onboarding route itself
        if _req.path.startswith(('/api/', '/static/', '/onboarding')):
            return
        # Skip public/auth pages
        _public_prefixes = ('/legal/', '/u/', '/share/', '/samples/', '/ref/',
                            '/auth/', '/ping', '/sitemap.xml')
        if _req.path.startswith(_public_prefixes):
            return
        if _req.path in ('/login', '/register', '/verify-sent', '/forgot-password',
                         '/partners/apply', '/'):
            return

        if not _cu.is_authenticated or not _cu.email_verified:
            return

        if getattr(_cu, 'onboarding_completed_at', None) is None:
            step = getattr(_cu, 'onboarding_step', 1) or 1
            return _redirect(f'/onboarding?step={step}')

    # Catch all unhandled exceptions and log the full traceback
    import traceback as _tb

    @app.errorhandler(Exception)
    def _handle_exception(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        app.logger.error('Unhandled exception:\n' + _tb.format_exc())
        from flask import jsonify, request as _req
        if _req.path.startswith('/api/'):
            return jsonify({'error': 'Internal server error'}), 500
        from flask import render_template
        try:
            return render_template('500.html'), 500
        except Exception:
            return '<h1>500 Internal Server Error</h1>', 500

    # Shell context
    @app.shell_context_processor
    def make_shell_context():
        from app.models.user import User
        from app.models.resume import Resume
        from app.models.simulation import Simulation, SimulationLayer, IncomeStream
        from app.models.collaboration import Collaboration, CollabActivity
        from app.models.platform_settings import PlatformSetting
        from app.models.ai_interaction import AIInteraction
        from app.models.audit_log import AuditLog
        from app.models.agent_action import AgentAction
        from app.models.agent_context import AgentContext
        from app.models.layer6 import (
            Layer6Config, Layer6Cycle, Layer6ActionQueue,
            Layer6Outcome, Layer6Momentum, Layer6ExecutionLog,
        )
        from app.models.bayesian import BayesianPosterior
        from app.models.kajabi import KajabiProduct
        from app.models.prospect_research import ProspectResearchRun
        from app.models.artifact import (
            PrefillCorrection, ArtifactVersion, ArtifactBundle,
            ArtifactDependency, BundleTypeConfig,
        )
        from app.models.discount import SimulationDiscount
        return dict(
            db=db, User=User, Resume=Resume, Simulation=Simulation,
            SimulationLayer=SimulationLayer, IncomeStream=IncomeStream,
            Collaboration=Collaboration, CollabActivity=CollabActivity,
            PlatformSetting=PlatformSetting, AIInteraction=AIInteraction,
            AuditLog=AuditLog, AgentAction=AgentAction, AgentContext=AgentContext,
            Layer6Config=Layer6Config, Layer6Cycle=Layer6Cycle,
            PrefillCorrection=PrefillCorrection, ArtifactVersion=ArtifactVersion,
            ArtifactBundle=ArtifactBundle, ArtifactDependency=ArtifactDependency,
            BundleTypeConfig=BundleTypeConfig,
            BayesianPosterior=BayesianPosterior, KajabiProduct=KajabiProduct,
        )

    return app
