"""Corporate Outplacement Licensing — /api/corporate/*

Admin creates CorporateAccount, sets seat count and tier.
Corp admin (contact) provisions employees via JSON list or CSV upload.
Each employee receives an invite email → creates account → simulation auto-queued.
Dashboard at /corporate/<org_id> shows progress for the HR firm.
"""
import csv
import io
from datetime import datetime, date, timedelta
from functools import wraps

from flask import request, jsonify, render_template, current_app, Response, url_for
from flask_login import login_required, current_user

from app.blueprints.corporate import corporate_bp
from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.corporate import (
    CorporateAccount, CorporateEmployee, CreditRedemption, OrgInvoice, OrgSmePod,
)
from app.services import org_service
from utils.id_gen import generate_id


def _parse_date(val):
    if not val:
        return None
    try:
        return datetime.strptime(str(val)[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


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
    """Provision an organization as a credit pool (SIM-PRD-ORG-001).

    Accepts org_type (pilot/cohort/enterprise/partner) and a credit pool; the
    legacy seat model is still honored when credits are not supplied.
    """
    data = request.get_json() or {}
    required = ['org_name', 'contact_name', 'contact_email']
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f'Missing: {", ".join(missing)}'}), 400

    org_type = (data.get('org_type') or CorporateAccount.ORG_PILOT).strip()
    if org_type not in CorporateAccount.ORG_TYPES:
        return jsonify({'error': f'Invalid org_type. Use: {list(CorporateAccount.ORG_TYPES)}'}), 400

    # Credit value is locked at contract time (FR-ORG-04). Default to the
    # current simulation price so later price changes never alter this contract.
    credit_value_cents = data.get('credit_value_cents')
    if not credit_value_cents:
        try:
            from app.services.pricing_service import get_current_price
            credit_value_cents = get_current_price()['base_price_cents']
        except Exception:
            credit_value_cents = current_app.config.get('SIMULATION_PRICE_CENTS', 69500)

    credits = int(data.get('credits_purchased') or data.get('credits') or 0)

    contract_start = _parse_date(data.get('contract_start')) or date.today()
    contract_end = _parse_date(data.get('contract_end'))
    if not contract_end:
        days = CorporateAccount.DEFAULT_EXPIRY_DAYS.get(org_type, 365)
        contract_end = contract_start + timedelta(days=days)

    domains = data.get('auto_join_domains')
    if isinstance(domains, str):
        domains = [d.strip().lower() for d in domains.replace(',', ' ').split() if d.strip()]
    elif isinstance(domains, list):
        domains = [str(d).strip().lower() for d in domains if str(d).strip()]
    else:
        domains = None

    org = CorporateAccount(
        id=generate_id(),
        org_name=data['org_name'].strip(),
        contact_name=data['contact_name'].strip(),
        contact_email=data['contact_email'].strip().lower(),
        org_type=org_type,
        license_tier=data.get('license_tier', CorporateAccount.TIER_STARTER),
        seat_count=int(data.get('seat_count') or credits or 25),
        credits_purchased=credits,
        credits_remaining=credits,
        credit_value_cents=int(credit_value_cents),
        discount_pct=data.get('discount_pct') or 0,
        auto_join_domains=domains,
        provisioning_trigger=data.get('provisioning_trigger', CorporateAccount.PROVISION_ON_ISSUE),
        contract_start=contract_start,
        contract_end=contract_end,
        white_label_name=data.get('white_label_name'),
        white_label_logo_url=data.get('white_label_logo_url'),
        notes=data.get('notes'),
        status=CorporateAccount.STATUS_PENDING,
    )
    db.session.add(org)
    AuditLog.log('corporate_org_created', user_id=current_user.id, resource_id=org.id,
                 metadata={'org_name': org.org_name, 'org_type': org_type, 'credits': credits})
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
    if _is_platform_admin():
        # Internal notes are deal context — platform admins only.
        data['notes'] = org.notes
    return jsonify(data), 200


@corporate_bp.route('/api/corporate/orgs/<org_id>', methods=['PUT'])
@corp_access_required
def update_org(org, **kwargs):
    """Edit an organization.

    Org Admins may edit co-branding and their own contact details. Platform
    admins may additionally correct the whole contract — name, offer tier,
    pool size, locked credit value, discount, dates, domains, invite cap.
    """
    data = request.get_json() or {}
    changed = []

    def _set(field, value):
        if getattr(org, field) != value:
            setattr(org, field, value)
            changed.append(field)

    def _bad(message):
        """Reject the whole edit — discard any field already applied."""
        db.session.rollback()
        return jsonify({'error': message}), 400

    # Optional free-text: blank clears the field.
    for field in ('white_label_name', 'white_label_logo_url', 'notes'):
        if field in data:
            _set(field, (str(data[field]).strip() or None) if data[field] else None)

    if 'contact_name' in data:
        name = str(data['contact_name'] or '').strip()
        if not name:
            return _bad('contact_name cannot be blank')
        _set('contact_name', name)

    if _is_platform_admin():
        if 'org_name' in data:
            org_name = str(data['org_name'] or '').strip()
            if not org_name:
                return _bad('org_name cannot be blank')
            _set('org_name', org_name)

        if 'contact_email' in data:
            email = str(data['contact_email'] or '').strip().lower()
            if '@' not in email:
                return _bad('A valid contact_email is required')
            _set('contact_email', email)

        if 'org_type' in data:
            org_type = str(data['org_type'] or '').strip()
            if org_type not in CorporateAccount.ORG_TYPES:
                return _bad(f'Invalid org_type. Use: {list(CorporateAccount.ORG_TYPES)}')
            _set('org_type', org_type)

        if 'license_tier' in data and data['license_tier']:
            tier = str(data['license_tier']).strip()
            if tier not in CorporateAccount.TIER_SEAT_LIMITS:
                return _bad(f'Invalid license_tier. Use: {list(CorporateAccount.TIER_SEAT_LIMITS)}')
            _set('license_tier', tier)

        if 'provisioning_trigger' in data:
            trigger = str(data['provisioning_trigger'] or '').strip()
            if trigger not in (CorporateAccount.PROVISION_ON_ISSUE,
                               CorporateAccount.PROVISION_ON_PAYMENT):
                return _bad('provisioning_trigger must be issue or payment')
            _set('provisioning_trigger', trigger)

        for field in ('discount_pct', 'credit_value_cents', 'seat_count', 'invite_cap'):
            if field not in data:
                continue
            raw = data[field]
            if raw in (None, ''):
                if field == 'invite_cap':      # NULL = unlimited
                    _set('invite_cap', None)
                continue
            try:
                value = float(raw) if field == 'discount_pct' else int(raw)
            except (TypeError, ValueError):
                return _bad(f'{field} must be a number')
            if value < 0:
                return _bad(f'{field} cannot be negative')
            if field == 'discount_pct' and value > 100:
                return _bad('discount_pct cannot exceed 100')
            _set(field, value)

        # Resize the pool by the delta so credits already redeemed stay used.
        if data.get('credits_purchased') not in (None, ''):
            try:
                new_total = int(data['credits_purchased'])
            except (TypeError, ValueError):
                return _bad('credits_purchased must be a number')
            if new_total < 0:
                return _bad('credits_purchased cannot be negative')
            delta = new_total - (org.credits_purchased or 0)
            if delta:
                org.credits_purchased = new_total
                org.credits_remaining = max(0, (org.credits_remaining or 0) + delta)
                changed.append('credits_purchased')

        if 'auto_join_domains' in data:
            d = data['auto_join_domains']
            if isinstance(d, str):
                d = [x.strip().lower() for x in d.replace(',', ' ').split() if x.strip()]
            elif isinstance(d, list):
                d = [str(x).strip().lower() for x in d if str(x).strip()]
            _set('auto_join_domains', d or None)

        for field in ('contract_start', 'contract_end'):
            if field in data:
                _set(field, _parse_date(data[field]))
        if org.contract_start and org.contract_end and org.contract_end < org.contract_start:
            return _bad('contract_end cannot precede contract_start')

    if changed:
        AuditLog.log('corporate_org_updated', user_id=current_user.id, resource_id=org.id,
                     metadata={'fields': changed})
    db.session.commit()
    return jsonify(org.to_dict()), 200


# ── Credit pool management (admin) ────────────────────────────────────────────

@corporate_bp.route('/api/corporate/orgs/<org_id>/credits/topup', methods=['POST'])
@admin_required
def topup_credits(org_id):
    org = CorporateAccount.query.get_or_404(org_id)
    qty = int((request.get_json() or {}).get('quantity') or 0)
    if qty <= 0:
        return jsonify({'error': 'quantity must be positive'}), 400
    org_service.add_credits(org, qty, actor_id=current_user.id)
    db.session.commit()
    return jsonify(org.to_dict()), 200


@corporate_bp.route('/api/corporate/orgs/<org_id>/extend', methods=['POST'])
@admin_required
def extend_contract(org_id):
    """Extend the contract end date (renewal / grace) — keeps credits alive."""
    org = CorporateAccount.query.get_or_404(org_id)
    data = request.get_json() or {}
    new_end = _parse_date(data.get('contract_end'))
    if not new_end and data.get('days'):
        base = org.contract_end or date.today()
        new_end = base + timedelta(days=int(data['days']))
    if not new_end:
        return jsonify({'error': 'contract_end or days required'}), 400
    org.contract_end = new_end
    if org.status == CorporateAccount.STATUS_EXPIRED:
        org.status = CorporateAccount.STATUS_ACTIVE
    AuditLog.log('org_contract_extended', user_id=current_user.id, resource_id=org.id,
                 metadata={'contract_end': new_end.isoformat()})
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

    # Seat guard applies only to legacy seat-based orgs. Credit-pool orgs
    # (ORG-001) never cap invitations — the pool funds SIMULATIONS, not seats,
    # and bio pages are free/unlimited (FR-ORG-06).
    new_count = len(employees_input)
    if not org.is_credit_pool and org.seats_available < new_count:
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
            join_source=CorporateEmployee.JOIN_CSV,
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

    # Link the member's profile to the org (co-branding + credit redemption).
    try:
        from app.models.profile import UserProfile
        prof = UserProfile.query.filter_by(user_id=user_id).first()
        if prof and not prof.org_id:
            prof.org_id = emp.org_id
    except Exception as exc:
        current_app.logger.warning('accept_invite profile link failed: %s', exc)

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error('accept_invite commit failed: %s', exc)
        return jsonify({'error': 'Database error'}), 500

    # Route the member into the standard onboarding wizard (FR-ORG-08).
    return jsonify({'ok': True, 'org_id': emp.org_id, 'emp_id': emp.id,
                    'next': '/onboarding'}), 200


# ── Billing & invoicing (FR-ORG-13) ──────────────────────────────────────────

@corporate_bp.route('/api/corporate/orgs/<org_id>/invoices', methods=['GET'])
@corp_access_required
def list_invoices(org, **kwargs):
    invoices = (OrgInvoice.query.filter_by(org_id=org.id)
                .order_by(OrgInvoice.created_at.desc()).all())
    return jsonify([i.to_dict() for i in invoices]), 200


@corporate_bp.route('/api/corporate/orgs/<org_id>/invoices', methods=['POST'])
@admin_required
def create_invoice(org_id):
    """Create an invoice via Stripe Invoicing (ACH/wire/card + PO/net terms).

    Provisions credits on ISSUE or on PAYMENT per the org's provisioning_trigger.
    Stripe is best-effort — the OrgInvoice record is the source of truth so a
    PO/manual invoice still works when Stripe Invoicing is unconfigured.
    """
    org = CorporateAccount.query.get_or_404(org_id)
    data = request.get_json() or {}
    credits = int(data.get('credits') or 0)
    unit_price_cents = int(data.get('unit_price_cents') or org.credit_value_cents or 0)
    if credits <= 0:
        return jsonify({'error': 'credits must be positive'}), 400
    amount_cents = int(data.get('amount_cents') or credits * unit_price_cents)

    inv = OrgInvoice(
        id=generate_id(), org_id=org.id,
        po_number=data.get('po_number'),
        credits=credits, unit_price_cents=unit_price_cents, amount_cents=amount_cents,
        net_terms=int(data.get('net_terms') or 30),
        status=OrgInvoice.STATUS_ISSUED, issued_at=datetime.utcnow(),
    )
    db.session.add(inv)

    # Best-effort Stripe Invoicing.
    try:
        from app.services.stripe_service import create_org_invoice
        ref = create_org_invoice(org, inv)
        if ref:
            inv.stripe_ref = ref
    except Exception as exc:
        current_app.logger.warning('Stripe invoicing skipped for org %s: %s', org.id, exc)

    # Provision on issue (trust-based, faster time-to-value).
    if org.provisioning_trigger == CorporateAccount.PROVISION_ON_ISSUE:
        _provision_from_invoice(org, inv)

    AuditLog.log('org_invoice_created', user_id=current_user.id, resource_id=org.id,
                 metadata={'invoice_id': inv.id, 'credits': credits, 'amount_cents': amount_cents})
    db.session.commit()
    return jsonify(inv.to_dict()), 201


@corporate_bp.route('/api/corporate/orgs/<org_id>/invoices/<invoice_id>/pay', methods=['POST'])
@admin_required
def mark_invoice_paid(org_id, invoice_id):
    org = CorporateAccount.query.get_or_404(org_id)
    inv = OrgInvoice.query.filter_by(id=invoice_id, org_id=org.id).first_or_404()
    if inv.status == OrgInvoice.STATUS_PAID:
        return jsonify(inv.to_dict()), 200
    inv.status = OrgInvoice.STATUS_PAID
    inv.paid_at = datetime.utcnow()
    # Provision on payment (for larger contracts).
    if org.provisioning_trigger == CorporateAccount.PROVISION_ON_PAYMENT:
        _provision_from_invoice(org, inv)
    AuditLog.log('org_invoice_paid', user_id=current_user.id, resource_id=org.id,
                 metadata={'invoice_id': inv.id})
    db.session.commit()
    return jsonify(inv.to_dict()), 200


def _provision_from_invoice(org, inv):
    """Add an invoice's credits to the pool and activate the org."""
    org.credits_purchased = (org.credits_purchased or 0) + inv.credits
    org.credits_remaining = (org.credits_remaining or 0) + inv.credits
    if org.status == CorporateAccount.STATUS_PENDING:
        org.status = CorporateAccount.STATUS_ACTIVE
        org.activated_at = org.activated_at or datetime.utcnow()


# ── Bulk-invite link (shareable) ──────────────────────────────────────────────

@corporate_bp.route('/api/corporate/orgs/<org_id>/invite-link', methods=['POST'])
@corp_access_required
def create_invite_link(org, **kwargs):
    """Generate (or rotate) the org's shareable join link (FR-ORG-08)."""
    data = request.get_json() or {}
    cap = data.get('cap')
    expires = _parse_date(data.get('expires_at'))
    expires_dt = datetime.combine(expires, datetime.min.time()) if expires else None
    if data.get('rotate'):
        org.invite_token = CorporateAccount.generate_invite_token()
        org.invite_uses = 0
    token = org_service.ensure_invite_link(org, cap=int(cap) if cap else None,
                                            expires_at=expires_dt)
    db.session.commit()
    base = current_app.config.get('BASE_URL', '').rstrip('/')
    return jsonify({'token': token, 'url': f'{base}/corporate/join/{token}',
                    'cap': org.invite_cap, 'uses': org.invite_uses}), 200


@corporate_bp.route('/corporate/join/<token>', methods=['GET'])
def join_via_link(token):
    """Public landing for the shareable org join link."""
    org = CorporateAccount.query.filter_by(invite_token=token).first_or_404()
    if not org_service.invite_link_ok(org):
        return render_template('corporate/invite.html', emp=None, org=org,
                               token=None, link_closed=True), 410
    return render_template('corporate/invite.html', emp=None, org=org,
                           token=token, via_link=True)


@corporate_bp.route('/api/corporate/join/<token>/accept', methods=['POST'])
@login_required
def join_via_link_accept(token):
    """Logged-in user joins an org via the shareable link."""
    org = CorporateAccount.query.filter_by(invite_token=token).first()
    if not org or not org_service.invite_link_ok(org):
        return jsonify({'error': 'This invite link is no longer active.'}), 410
    org_service.link_member(org, current_user.id, current_user.email,
                            getattr(current_user, 'name', None),
                            CorporateEmployee.JOIN_LINK)
    org.invite_uses = (org.invite_uses or 0) + 1
    db.session.commit()
    return jsonify({'ok': True, 'org_id': org.id, 'next': '/onboarding'}), 200


# ── Activation reminders (FR-ORG-09) ──────────────────────────────────────────

@corporate_bp.route('/api/corporate/orgs/<org_id>/remind', methods=['POST'])
@corp_access_required
def send_reminders(org, **kwargs):
    """Manually nudge non-activated members (Org Admin action)."""
    targets = [e for e in org.employees.filter_by(status=CorporateEmployee.STATUS_INVITED).all()
               if e.invite_token]
    sent = 0
    for emp in targets:
        if _send_invite_email(emp, org, reminder=True):
            emp.reminder_count = (emp.reminder_count or 0) + 1
            emp.last_reminded_at = datetime.utcnow()
            sent += 1
    db.session.commit()
    return jsonify({'sent': sent}), 200


# ── Cohort SME pod (admin) ────────────────────────────────────────────────────

@corporate_bp.route('/api/corporate/orgs/<org_id>/sme-pod', methods=['GET'])
@corp_access_required
def get_sme_pod(org, **kwargs):
    """SMEs covering this cohort (names + zones). Org Admin sees WHO covers
    them as a value signal — never the recommendations SMEs make (FR-ORG-11)."""
    from app.models.sme import SimiSME
    ids = [r[0] for r in OrgSmePod.query.filter_by(org_id=org.id)
           .with_entities(OrgSmePod.sme_id).all()]
    smes = SimiSME.query.filter(SimiSME.id.in_(ids)).all() if ids else []
    return jsonify([{'id': s.id, 'name': f'{s.first_name} {s.last_name}'.strip(),
                     'zones': s.zones} for s in smes]), 200


@corporate_bp.route('/api/corporate/orgs/<org_id>/sme-pod', methods=['PUT'])
@admin_required
def set_sme_pod(org_id):
    """Assign the SME pod for an org and report cohort coverage (FR-ORG-11)."""
    org = CorporateAccount.query.get_or_404(org_id)
    sme_ids = (request.get_json() or {}).get('sme_ids') or []
    OrgSmePod.query.filter_by(org_id=org.id).delete()
    for sid in sme_ids:
        db.session.add(OrgSmePod(org_id=org.id, sme_id=sid))
    # Re-match existing members to the new pod.
    from app.models.profile import UserProfile
    from app.services import sme_service
    rematched = 0
    for uid in org_service.member_user_ids(org):
        prof = UserProfile.query.filter_by(user_id=uid).first()
        if prof and not prof.sme_opted_out and prof.sme_assignment_type != 'manual':
            sme_service.auto_assign_sme(prof, commit=False)
            rematched += 1
    AuditLog.log('org_sme_pod_set', user_id=current_user.id, resource_id=org.id,
                 metadata={'sme_ids': sme_ids, 'rematched': rematched})
    db.session.commit()
    return jsonify({'sme_ids': sme_ids, 'rematched': rematched,
                    'coverage': _pod_coverage(org, sme_ids)}), 200


def _pod_coverage(org, sme_ids):
    """Capacity-planning hint: cohort zone demand vs pod zone coverage."""
    from app.models.sme import SimiSME
    from app.models.profile import UserProfile
    zones_demand = {}
    for uid in org_service.member_user_ids(org):
        prof = UserProfile.query.filter_by(user_id=uid).first()
        if prof and prof.primary_zone:
            zones_demand[prof.primary_zone] = zones_demand.get(prof.primary_zone, 0) + 1
    covered = set()
    capacity = 0
    for s in (SimiSME.query.filter(SimiSME.id.in_(sme_ids)).all() if sme_ids else []):
        covered.update(s.zones)
        capacity += (s.capacity or 0)
    gaps = [z for z in zones_demand if z not in covered]
    return {'zone_demand': zones_demand, 'covered_zones': sorted(covered),
            'uncovered_zones': gaps, 'pod_capacity': capacity}


# ── Member co-brand opt-out (FR-ORG-12) ───────────────────────────────────────

@corporate_bp.route('/api/corporate/cobrand', methods=['POST'])
@login_required
def set_cobrand_pref():
    """Member hides/shows the sponsor badge on their bio page. The org sponsors
    access; it does not own the member's public identity."""
    from app.models.profile import UserProfile
    hide = bool((request.get_json() or {}).get('hide'))
    prof = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not prof:
        return jsonify({'error': 'No profile'}), 404
    prof.hide_org_cobrand = hide
    db.session.commit()
    return jsonify({'hide_org_cobrand': hide}), 200


# ── HTML dashboard — Partner / Org Admin console ──────────────────────────────

@corporate_bp.route('/corporate/<org_id>')
@login_required
def dashboard(org_id):
    org = CorporateAccount.query.get_or_404(org_id)
    if not _is_platform_admin() and org.admin_user_id != current_user.id:
        return render_template('public/profile_unpublished.html', username=''), 403

    metrics = org_service.dashboard_metrics(org)
    roster = org_service.roster(org)
    invoices = (OrgInvoice.query.filter_by(org_id=org.id)
                .order_by(OrgInvoice.created_at.desc()).all())
    return render_template(
        'corporate/dashboard.html',
        org=org,
        metrics=metrics,
        roster=roster,
        invoices=invoices,
    )


@corporate_bp.route('/corporate/<org_id>/roster.csv')
@login_required
def export_roster_csv(org_id):
    """CSV roster export (FR-ORG-10) — activation status only."""
    org = CorporateAccount.query.get_or_404(org_id)
    if not _is_platform_admin() and org.admin_user_id != current_user.id:
        from flask import abort
        abort(403)
    rows = org_service.roster(org)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Name', 'Email', 'Status', 'Bio published', 'Simulation started', 'Activated at'])
    for r in rows:
        w.writerow([r['name'], r['email'], r['status'],
                    'yes' if r['bio_published'] else 'no',
                    'yes' if r['sim_started'] else 'no', r['activated_at'] or ''])
    fname = f'{org.display_name.replace(" ", "_")}_roster.csv'
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{fname}"'})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _send_invite_email(emp: CorporateEmployee, org: CorporateAccount, reminder: bool = False) -> bool:
    """Send a co-branded org invite/reminder. Silent failure — non-blocking.

    Co-branded with the org logo/name (FR-ORG-09), sent via the platform email
    infrastructure, one clear CTA into the standard onboarding wizard.
    Returns True on send.
    """
    try:
        from app.services.email_service import _send
        org_display = org.display_name
        invite_url = (
            f'{current_app.config.get("BASE_URL", "").rstrip("/")}'
            f'/corporate/invite/{emp.invite_token}'
        )
        if reminder:
            subject = f'Reminder: your Simulacrum access from {org_display} is waiting'
            lead = (f'A quick nudge — {org_display} has sponsored your Simulacrum access '
                    f'and your invitation is still open.')
        else:
            subject = f'{org_display} has sponsored your Simulacrum access'
            lead = (f'{org_display} has sponsored your access to Simulacrum, a career '
                    f'wealth simulation platform.')
        logo_html = (f'<img src="{org.white_label_logo_url}" alt="{org_display}" '
                     f'style="max-height:48px;margin-bottom:16px">'
                     if org.white_label_logo_url else '')
        body = (
            f'Hi {emp.full_name or "there"},\n\n{lead}\n\n'
            f'Get started here:\n{invite_url}\n\n'
            f'Your simulation analyzes your career history and shows income '
            f'opportunities across 5 layers. Your account, bio page, and work are '
            f'yours — {org_display} sponsors the access, they do not see what you build.\n\n'
            f'— {org_display}'
        )
        html = (
            f'<div style="font-family:sans-serif;max-width:520px">{logo_html}'
            f'<p>Hi {emp.full_name or "there"},</p><p>{lead}</p>'
            f'<p><a href="{invite_url}" style="background:#4f46e5;color:#fff;'
            f'padding:12px 20px;border-radius:8px;text-decoration:none;display:inline-block">'
            f'Get started</a></p>'
            f'<p style="color:#666;font-size:.85rem">Your account, bio page, and work are '
            f'yours — {org_display} sponsors the access, they do not see what you build.</p>'
            f'<p style="color:#666;font-size:.85rem">— {org_display}</p></div>'
        )
        _send(subject=subject, recipients=[emp.email], body=body, html=html)
        return True
    except Exception as exc:
        current_app.logger.warning('Invite email failed for %s: %s', emp.email, exc)
        return False
