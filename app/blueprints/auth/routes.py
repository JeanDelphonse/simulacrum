import secrets
from datetime import datetime, timedelta
from flask import request, jsonify, current_app
from flask_login import login_user, logout_user, login_required, current_user
import jwt as pyjwt
from app.blueprints.auth import auth_bp
from app.extensions import db, bcrypt, login_manager
from app.models.user import User
from app.models.audit_log import AuditLog
from utils.id_gen import generate_id


@login_manager.user_loader
def load_user(user_id):
    user = User.query.get(user_id)
    if user and user.deleted_at:
        return None
    if user:
        from flask import session as flask_session, g
        jti = flask_session.get('jti')
        if jti:
            request._session_jti = jti
            from app.models.profile import UserSession
            s = UserSession.query.filter_by(jti=jti).first()
            if s and s.revoked_at:
                return None
            if s and s.is_active:
                s.last_active = datetime.utcnow()
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
    return user


def _make_jwt(user_id: str):
    jti = secrets.token_hex(16)
    payload = {
        'sub': user_id,
        'jti': jti,
        'iat': datetime.utcnow(),
        'exp': datetime.utcnow() + timedelta(days=current_app.config['JWT_EXPIRY_DAYS']),
    }
    token = pyjwt.encode(payload, current_app.config['JWT_SECRET_KEY'], algorithm='HS256')
    return token, jti


def _create_session(user_id: str, jti: str):
    from app.models.profile import UserSession
    session_record = UserSession(
        id=generate_id(),
        user_id=user_id,
        jti=jti,
        user_agent=(request.headers.get('User-Agent') or '')[:500],
        ip_address=request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()[:45],
        expires_at=datetime.utcnow() + timedelta(days=current_app.config['JWT_EXPIRY_DAYS']),
    )
    db.session.add(session_record)
    from flask import session as flask_session
    flask_session['jti'] = jti


@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password') or not data.get('full_name'):
        return jsonify({'error': 'email, password, and full_name are required'}), 400

    if User.query.filter_by(email=data['email'].lower()).first():
        return jsonify({'error': 'Email already registered'}), 409

    pw_hash = bcrypt.generate_password_hash(
        data['password'], rounds=current_app.config['BCRYPT_LOG_ROUNDS']
    ).decode('utf-8')

    verify_token = secrets.token_urlsafe(32)
    user = User(
        id=generate_id(),
        email=data['email'].lower(),
        password_hash=pw_hash,
        full_name=data['full_name'],
        email_verified=False,
        email_verify_token=verify_token,
        email_verify_token_expires=datetime.utcnow() + timedelta(hours=24),
    )
    db.session.add(user)
    db.session.flush()  # write user row before audit_log FK reference
    AuditLog.log('user_registered', user_id=user.id, resource_id=user.id)

    # Referral attribution — check session for referral code
    from flask import session
    ref_code = session.pop('referral_code', None) or data.get('referral_code')
    ref_clicked_at = session.pop('referral_clicked_at', None)
    if ref_code:
        try:
            from app.models.partner import ReferralPartner, ReferralSignup
            partner = ReferralPartner.query.filter_by(
                referral_code=ref_code, status=ReferralPartner.STATUS_ACTIVE,
            ).first()
            if partner:
                clicked_at = datetime.fromisoformat(ref_clicked_at) if ref_clicked_at else datetime.utcnow()
                signup = ReferralSignup(
                    id=generate_id(),
                    partner_id=partner.id,
                    referred_user_id=user.id,
                    referral_code=ref_code,
                    clicked_at=clicked_at,
                    registered_at=datetime.utcnow(),
                )
                db.session.add(signup)
        except Exception:
            pass  # referral attribution failure must never block registration

    db.session.commit()

    # Send verification email in a background thread so the response returns immediately.
    # SMTP on shared hosting can block for 30+ seconds and cause Passenger to kill the worker.
    import threading
    _app = current_app._get_current_object()
    _email, _name, _token = user.email, user.full_name, verify_token

    def _send():
        with _app.app_context():
            try:
                from app.services.email_service import send_verification_email
                send_verification_email(_email, _name, _token)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error('send_verification_email failed: %s', e, exc_info=True)

    threading.Thread(target=_send, daemon=True).start()

    return jsonify({
        'message': 'Registration successful. Check your email to verify your account.',
        'user_id': user.id,
    }), 201


@auth_bp.route('/verify/<token>', methods=['GET'])
def verify_email(token):
    from flask import render_template
    user = User.query.filter_by(email_verify_token=token).first()
    if not user:
        return render_template('auth/verify_email.html', status='invalid'), 400
    if user.email_verify_token_expires and user.email_verify_token_expires < datetime.utcnow():
        return render_template('auth/verify_email.html', status='expired', user_email=user.email), 400
    user.email_verified = True
    user.email_verify_token = None
    user.email_verify_token_expires = None
    db.session.commit()
    return render_template('auth/verify_email.html', status='success')


@auth_bp.route('/verify/resend', methods=['POST'])
def resend_verification():
    """Resend a verification email (needed when old token has expired)."""
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'email is required'}), 400
    user = User.query.filter_by(email=data['email'].lower()).first()
    if user and not user.email_verified:
        token = secrets.token_urlsafe(32)
        user.email_verify_token = token
        user.email_verify_token_expires = datetime.utcnow() + timedelta(hours=24)
        db.session.commit()
        try:
            from app.services.email_service import send_verification_email
            send_verification_email(user.email, user.full_name, token)
        except Exception:
            pass
    # Always 200 to avoid email enumeration
    return jsonify({'message': 'If that email is registered and unverified, a new link has been sent'}), 200


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data or not data.get('email') or not data.get('password'):
        return jsonify({'error': 'email and password are required'}), 400

    user = User.query.filter_by(email=data['email'].lower()).first()
    if not user or not user.password_hash:
        return jsonify({'error': 'Invalid email or password'}), 401
    if not bcrypt.check_password_hash(user.password_hash, data['password']):
        return jsonify({'error': 'Invalid email or password'}), 401
    if not user.email_verified:
        return jsonify({'error': 'Please verify your email before logging in'}), 403

    login_user(user, remember=True)
    token, jti = _make_jwt(user.id)
    _create_session(user.id, jti)
    user.last_login_at = datetime.utcnow()
    user.retention_warned_at = None  # reset warning on sign-in (FR-TOS-13)
    AuditLog.log('user_login', user_id=user.id)
    db.session.commit()

    return jsonify({
        'token': token,
        'user': {
            'id': user.id,
            'email': user.email,
            'full_name': user.full_name,
            'is_admin': user.is_admin,
            'simulation_count': user.simulation_count,
            'total_spend_cents': user.total_spend,
        },
    }), 200


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    AuditLog.log('user_logout', user_id=current_user.id)
    db.session.commit()
    logout_user()
    return jsonify({'message': 'Logged out successfully'}), 200


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password_request():
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'email is required'}), 400

    user = User.query.filter_by(email=data['email'].lower()).first()
    if user:
        token = secrets.token_urlsafe(32)
        user.password_reset_token = token
        user.password_reset_expires = datetime.utcnow() + timedelta(hours=24)
        db.session.commit()
        try:
            from app.services.email_service import send_password_reset_email
            send_password_reset_email(user.email, user.full_name, token)
        except Exception:
            pass

    # Always return 200 to prevent email enumeration
    return jsonify({'message': 'If that email is registered, a reset link has been sent'}), 200


@auth_bp.route('/reset-password/<token>', methods=['POST'])
def reset_password_confirm(token):
    data = request.get_json()
    if not data or not data.get('password'):
        return jsonify({'error': 'New password is required'}), 400

    user = User.query.filter_by(password_reset_token=token).first()
    if not user or not user.password_reset_expires or user.password_reset_expires < datetime.utcnow():
        return jsonify({'error': 'Invalid or expired reset token'}), 400

    user.password_hash = bcrypt.generate_password_hash(
        data['password'], rounds=current_app.config['BCRYPT_LOG_ROUNDS']
    ).decode('utf-8')
    user.password_reset_token = None
    user.password_reset_expires = None
    db.session.commit()
    return jsonify({'message': 'Password reset successfully'}), 200


@auth_bp.route('/google', methods=['GET'])
def google_oauth_start():
    """Redirect browser to Google's OAuth consent screen."""
    import urllib.parse
    state = secrets.token_urlsafe(16)
    from flask import session
    session['google_oauth_state'] = state
    params = {
        'client_id': current_app.config['GOOGLE_CLIENT_ID'],
        'redirect_uri': current_app.config['GOOGLE_REDIRECT_URI'],
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    }
    auth_url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urllib.parse.urlencode(params)
    return jsonify({'auth_url': auth_url}), 200


@auth_bp.route('/google/callback', methods=['GET'])
def google_oauth_callback():
    """Exchange Google code for tokens, then log in or register the user."""
    import requests as http
    from flask import session, redirect, url_for

    error = request.args.get('error')
    if error:
        return jsonify({'error': f'Google OAuth error: {error}'}), 400

    code = request.args.get('code')
    state = request.args.get('state')
    if not code:
        return jsonify({'error': 'No authorization code received'}), 400
    if state != session.pop('google_oauth_state', None):
        return jsonify({'error': 'Invalid OAuth state'}), 400

    # Exchange code for tokens
    try:
        token_resp = http.post('https://oauth2.googleapis.com/token', data={
            'code': code,
            'client_id': current_app.config['GOOGLE_CLIENT_ID'],
            'client_secret': current_app.config['GOOGLE_CLIENT_SECRET'],
            'redirect_uri': current_app.config['GOOGLE_REDIRECT_URI'],
            'grant_type': 'authorization_code',
        }, timeout=10)
        token_resp.raise_for_status()
        access_token = token_resp.json().get('access_token')
    except Exception as e:
        return jsonify({'error': f'Token exchange failed: {str(e)}'}), 500

    # Fetch user profile
    try:
        profile_resp = http.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10,
        )
        profile_resp.raise_for_status()
        profile = profile_resp.json()
    except Exception as e:
        return jsonify({'error': f'Failed to fetch Google profile: {str(e)}'}), 500

    google_id = profile.get('sub')
    email = profile.get('email', '').lower()
    full_name = profile.get('name', email)

    if not google_id or not email:
        return jsonify({'error': 'Incomplete profile from Google'}), 400

    # Find existing account by google_id or email
    user = User.query.filter_by(google_id=google_id).first()
    if not user:
        user = User.query.filter_by(email=email).first()
        if user:
            user.google_id = google_id
            user.email_verified = True  # Google-authenticated = verified
        else:
            user = User(
                id=generate_id(),
                email=email,
                full_name=full_name,
                google_id=google_id,
                email_verified=True,  # Google-authenticated = verified
            )
            db.session.add(user)
            db.session.flush()  # write user row before audit_log FK reference
            AuditLog.log('user_registered', user_id=user.id, resource_id=user.id)

    login_user(user, remember=True)
    _, jti = _make_jwt(user.id)
    _create_session(user.id, jti)
    AuditLog.log('user_login_google', user_id=user.id)
    db.session.commit()

    return redirect(url_for('pages.dashboard'))
