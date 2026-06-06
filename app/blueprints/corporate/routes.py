"""Corporate Outplacement Licensing — /api/corporate/*

Admin creates CorporateAccount, sets seat count and tier.
Corp admin (contact) provisions employees via JSON list or CSV upload.
Each employee receives an invite email → creates account → simulation auto-queued.
Dashboard at /corporate/<org_id> shows progress for the HR firm.
"""
import csv
import io
from datetime import datetime
from functools import wraps

from flask import request, jsonify, render_template, current_app
from flask_login import login_required, current_user

from app.blueprints.corporate import corporate_bp
from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.corporate import CorporateAccount, CorporateEmployee
from utils.id_gen import generate_id


# ── Decorators ────────────────────────────────────────────────────────────────

def _is_platform_admin():
    return getattr(current_user, 'is_admin', False)


def corp_access_required(f):
    """Allow platform admins OR the org's designated admin_user_id."""
    @wraps(f)
    @login_required
    def decorated(org_id, *args, **kwargs):
        org = CorporateAccount.query.get_or_404(org_id)
        if not _is_platform_admin() and org.admin_user_id != current_user.id:
            return jsonify({'error': 'Access denied'}), 403
        return f(org, *args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not _is_platform_admin():
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


# ── Admin: org lifecycle ──────────────────────────────────────────────────────

@corporate_bp.route('/api/corporate/orgs', methods=['GET'])
@admin_required
def list_orgs():
    status = request.args.get('status')
    q = CorporateAccount.query
    if status:
        q = q.filter_by(status=status)
    orgs = q.order_by(CorporateAccount.created_at.desc()).all()
    return jsonify([o.to_dict() for o in orgs]), 200


@corporate_bp.route('/api/corporate/orgs', methods=['POST'])
@admin_required
def create_org():
    data = request.get_json() or {}
    required = ['org_name', 'contact_name', 'contact_email']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Missing: {", ".join(missing)}'}), 400

    tier = data.get('license_tier', CorporateAccount.TIER_STARTER)
    if tier not in CorporateAccount.TIER_SEAT_LIMITS:
        return jsonify({'error': f'Invalid license_tier. Use: {list(CorporateAccount.TIER_SEAT_LIMITS)}'}), 400

    seat_count = data.get('seat_count') or CorporateAccount.TIER_SEAT_LIMITS[tier]
    max_seats = CorporateAccount.TIER_SEAT_LIMITS[tier]
    if seat_count > max_seats:
        return jsonify({'error': f'{tier} tier supports max {max_seats} seats'}), 400

    org = CorporateAccount(
        id=generate_id(),
        org_name=data['org_name'].strip(),
        contact_name=data['contact_name'].strip(),
        contact_email=data['contact_email'].strip().lower(),
        license_tier=tier,
        seat_count=seat_count,
        white_label_name=data.get('white_label_name'),
        white_label_logo_url=data.get('white_label_logo_url'),
        notes=data.get('notes'),
        status=CorporateAccount.STATUS_PENDING,
    )
    db.session.add(org)
    AuditLog.log('corporate_org_created', user_id=current_user.id, resource_id=org.id,
                 metadata={'org_name': org.org_name, 'tier': tier})
    db.session.commit()
    return jsonify(org.to_dict()), 201


@corporate_bp.route('/api/corporate/orgs/<org_id>/activate', methods=['PUT'])
@admin_required
def activate_org(org_id):
    org = CorporateAccount.query.get_or_404(org_id)
    org.status = CorporateAccount.STATUS_ACTIVE
    org.activated_at = datetime.utcnow()
    AuditLog.log('corporate_org_activated', user_id=current_user.id, resource_id=org_id)
    db.session.commit()
    return jsonify(org.to_dict()), 200


@corporate_bp.route('/api/corporate/orgs/<org_id>/suspend', methods=['PUT'])
@admin_required
def suspend_org(org_id):
    org = CorporateAccount.query.get_or_404(org_id)
    org.status = CorporateAccount.STATUS_SUSPENDED
    org.suspended_at = datetime.utcnow()
    AuditLog.log('corporate_org_suspended', user_id=current_user.id, resource_id=org_id)
    db.session.commit()
    return jsonify(org.to_dict()), 200


@corporate_bp.route('/api/corporate/orgs/<org_id>', methods=['GET'])
@corp_access_required
def get_org(org, **kwargs):
    employees = org.employees.order_by(CorporateEmployee.provisioned_at.desc()).all()
    data = org.to_dict()
    data['employees'] = [e.to_dict() for e in employees]
    return jsonify(data), 200


@corporate_bp.route('/api/corporate/orgs/<org_id>', methods=['PUT'])
@corp_access_required
def update_org(org, **kwargs):
    data = request.get_json() or {}
    for field in ('white_label_name', 'white_label_logo_url', 'notes', 'contact_name'):
        if field in data:
            setattr(org, field, data[field])
    db.session.commit()
    return jsonify(org.to_dict()), 200


# ── Employee provisioning ─────────────────────────────────────────────────────

@corporate_bp.route('/api/corporate/orgs/<org_id>/provision', methods=['POST'])
@corp_access_required
def provision_employees(org, **kwargs):
    """Provision employees from a JSON list or CSV upload.

    JSON body: {"employees": [{"email": "...", "full_name": "..."}, ...]}
    CSV upload: multipart/form-data field 'file' with columns email, full_name (optional)
    """
    if org.status != CorporateAccount.STATUS_ACTIVE:
        return jsonify({'error': 'Organization must be active to provision employees'}), 403

    # Parse employee list from JSON or CSV
    employees_input = []
    if request.content_type and 'multipart' in request.content_type:
        f = request.files.get('file')
        if not f:
            return jsonify({'error': 'No file uploaded'}), 400
        stream = io.StringIO(f.stream.read().decode('utf-8-sig'))
        reader = csv.DictReader(stream)
        for row in reader:
            email = (row.get('email') or row.get('Email') or '').strip().lower()
            name = (row.get('full_name') or row.get('Full Name') or row.get('name') or '').strip()
            if email and '@' in email:
                employees_input.append({'email': email, 'full_name': name})
    else:
        data = request.get_json() or {}
        employees_input = data.get('employees', [])

    if not employees_input:
        return jsonify({'error': 'No employees provided'}), 400

    # Seat guard
    new_count = len(employees_input)
    if org.seats_available < new_count:
        return jsonify({
            'error': f'Only {org.seats_available} seat(s) available, {new_count} requested',
        }), 400

    created = []
    skipped = []

    for item in employees_input:
        email = (item.get('email') or '').strip().lower()
        name = (item.get('full_name') or '').strip()
        if not email or '@' not in email:
            skipped.append({'email': email, 'reason': 'invalid email'})
            continue

        existing = CorporateEmployee.query.filter_by(org_id=org.id, email=email).first()
        if existing:
            skipped.append({'email': email, 'reason': 'already provisioned'})
            continue

        token = CorporateEmployee.generate_invite_token()
        emp = CorporateEmployee(
            id=generate_id(),
            org_id=org.id,
            email=email,
            full_name=name or None,
            status=CorporateEmployee.STATUS_INVITED,
            invite_token=token,
        )
        db.session.add(emp)
        db.session.flush()
        created.append(emp)
        org.seats_used = (org.seats_used or 0) + 1

        # Send invite email
        _send_invite_email(emp, org)

    AuditLog.log('corporate_employees_provisioned', user_id=current_user.id, resource_id=org.id,
                 metadata={'created': len(created), 'skipped': len(skipped)})
    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('provision_employees commit failed: %s', exc)
        return jsonify({'error': 'Database error'}), 500

    return jsonify({
        'created': len(created),
        'skipped': len(skipped),
        'skipped_details': skipped,
        'employees': [e.to_dict() for e in created],
    }), 201


@corporate_bp.route('/api/corporate/orgs/<org_id>/employees', methods=['GET'])
@corp_access_required
def list_employees(org, **kwargs):
    status = request.args.get('status')
    q = org.employees
    if status:
        q = q.filter_by(status=status)
    employees = q.order_by(CorporateEmployee.provisioned_at.desc()).all()
    return jsonify([e.to_dict() for e in employees]), 200


@corporate_bp.route('/api/corporate/orgs/<org_id>/employees/<emp_id>', methods=['DELETE'])
@corp_access_required
def remove_employee(org, emp_id, **kwargs):
    emp = CorporateEmployee.query.filter_by(id=emp_id, org_id=org.id).first_or_404()
    if emp.status == CorporateEmployee.STATUS_ACTIVE:
        org.seats_used = max(0, (org.seats_used or 1) - 1)
    db.session.delete(emp)
    AuditLog.log('corporate_employee_removed', user_id=current_user.id, resource_id=org.id,
                 metadata={'emp_id': emp_id, 'email': emp.email})
    db.session.commit()
    return jsonify({'message': 'Employee removed'}), 200


# ── Invite acceptance (public) ────────────────────────────────────────────────

@corporate_bp.route('/corporate/invite/<token>', methods=['GET'])
def accept_invite(token):
    emp = CorporateEmployee.query.filter_by(invite_token=token).first_or_404()
    org = emp.org
    return render_template(
        'corporate/invite.html',
        emp=emp,
        org=org,
        token=token,
    )


@corporate_bp.route('/api/corporate/invite/<token>/accept', methods=['POST'])
def accept_invite_api(token):
    """Employee completes registration via invite token. Links their user account."""
    emp = CorporateEmployee.query.filter_by(invite_token=token).first()
    if not emp:
        return jsonify({'error': 'Invalid or expired invite token'}), 404
    if emp.status != CorporateEmployee.STATUS_INVITED:
        return jsonify({'error': 'Invite already used'}), 409

    data = request.get_json() or {}
    user_id = data.get('user_id')  # set by frontend after login/register

    if not user_id:
        return jsonify({'error': 'user_id is required'}), 400

    emp.user_id = user_id
    emp.status = CorporateEmployee.STATUS_ACTIVE
    emp.activated_at = datetime.utcnow()
    emp.invite_token = None  # consume token

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('accept_invite commit failed: %s', exc)
        return jsonify({'error': 'Database error'}), 500

    return jsonify({'ok': True, 'org_id': emp.org_id, 'emp_id': emp.id}), 200


# ── HTML dashboard ────────────────────────────────────────────────────────────

@corporate_bp.route('/corporate/<org_id>')
@login_required
def dashboard(org_id):
    org = CorporateAccount.query.get_or_404(org_id)
    if not _is_platform_admin() and org.admin_user_id != current_user.id:
        return render_template('public/profile_unpublished.html', username=''), 403

    employees = org.employees.order_by(CorporateEmployee.provisioned_at.desc()).all()

    stats = {
        'total': len(employees),
        'invited': sum(1 for e in employees if e.status == CorporateEmployee.STATUS_INVITED),
        'active': sum(1 for e in employees if e.status == CorporateEmployee.STATUS_ACTIVE),
        'complete': sum(1 for e in employees if e.status == CorporateEmployee.STATUS_COMPLETE),
        'seats_available': org.seats_available,
    }

    return render_template(
        'corporate/dashboard.html',
        org=org,
        employees=employees,
        stats=stats,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _send_invite_email(emp: CorporateEmployee, org: CorporateAccount):
    """Send outplacement invite email. Silent failure — non-blocking."""
    try:
        from app.services.email_service import _send
        org_display = org.white_label_name or org.org_name
        invite_url = (
            f'{current_app.config.get("BASE_URL", "").rstrip("/")}'
            f'/corporate/invite/{emp.invite_token}'
        )
        subject = f'Your career transition resources are ready — {org_display}'
        body = (
            f'Hi {emp.full_name or "there"},\n\n'
            f'{org_display} has prepared a personalized career wealth simulation for you '
            f'as part of your transition support package.\n\n'
            f'Click the link below to access your resources:\n{invite_url}\n\n'
            f'Your simulation will analyze your career history and show you '
            f'3–6 income opportunities across 5 layers — consulting, group programs, '
            f'digital products, automated systems, and wealth building.\n\n'
            f'This link is unique to you. It expires when you activate your account.\n\n'
            f'— The {org_display} Transition Team'
        )
        _send(to=emp.email, subject=subject, body=body)
    except Exception as exc:
        current_app.logger.warning('Invite email failed for %s: %s', emp.email, exc)
