"""/admin/founder: Founder Track admin (FOUNDER_PLAN.md WP3).

One page with tabs (Overview, Rubric, Readiness, Autonomy, Caps, Targets,
Pricing, Preview, Investors, Name checks, Counsel) and JSON endpoints under
/api/admin/founder/. The page works whatever the founder_track switch says
(an admin has to be able to turn it on), but only for admins.

Every change is audit-logged: settings through founder_config.set, switches
through founder_feature.set_mode, and the rest here.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, jsonify, render_template, request
from flask_login import current_user, login_required

from app.blueprints.founder import founder_bp
from app.extensions import db

TABS = (('overview', 'Overview'), ('rubric', 'Rubric'), ('readiness', 'Readiness'), ('autonomy', 'Autonomy'),
        ('caps', 'Caps'), ('targets', 'Targets'), ('pricing', 'Pricing'), ('preview', 'Preview'),
        ('investors', 'Investors'), ('names', 'Name checks'), ('counsel', 'Counsel'))

SWITCHES = (('founder_track', 'Founder Track', 'The whole track: intake, report, agents, plan and startup pages.'),
            ('founder_pitch', 'Spoken pitch', 'Founders may talk their pitch through instead of uploading a deck. '
                                              'Only matters while the Founder Track is enabled.'),
            ('founder_investor', 'Investor connection (Phase 3)',
             'Investor matching, introductions, investor access and updates.'))

LEVEL_LABELS = {0: 'Not permitted', 1: 'Advisor only', 2: 'Approve each action', 3: 'Approve the campaign',
                4: 'Act within limits'}

ACTION_LABELS = {
    'research': 'Research', 'intro_request': 'Introduction request', 'followup': 'Follow-up',
    'first_contact': 'First contact', 'application': 'Application', 'financial_message': 'Financial message',
    'customer_message': 'Customer message', 'negotiate_terms': 'Negotiate or accept terms',
}

TARGET_LABELS = {
    'eval_completion': ('Evaluation completion', '% of ventures that finish an evaluation'),
    'finding_accuracy': ('Finding accuracy', '% of rated findings marked fair'),
    'paid_poc_rate': ('Paid proof of concept', '% of ventures with a paid proof result'),
    'step_completion_per_week': ('Steps completed per week', 'per active venture'),
    'intro_acceptance': ('Introduction acceptance', '% of introduction requests accepted'),
    'application_submission': ('Application submission', '% of drafted applications submitted'),
    'alignment_agreement': ('Co-founder alignment', '% of alignment questions in agreement'),
}

PREVIEW_DESCRIPTIONS = {
    'rung': 'The evidence rung only.',
    'scores': 'The rung, a score band per rubric dimension, and counts of findings by type and severity.',
    'top3': 'Everything in "scores", plus the 3 most severe findings in full.',
}

# Settings the generic endpoint may change. The counsel date has its own endpoint.
EDITABLE = frozenset((
    'fnd_rubric_weights', 'fnd_gate_rung_applications', 'fnd_gate_rung_intros', 'fnd_next_alternates',
    'fnd_preview_level', 'fnd_price_cents', 'fnd_addon_price_cents', 'fnd_investor_access_cents',
    'fnd_refund_window_days', 'fnd_fee_mode', 'fnd_action_cap_per_day', 'fnd_budget_cap_cents',
    'fnd_autonomy_matrix', 'fnd_first_contact_cap', 'fnd_targets', 'fnd_alert_error_rate_pct',
    'fnd_alert_cost_per_venture_cents', 'fnd_alert_stuck_days', 'fnd_retention_days',
))

CSV_COLUMNS = ('name', 'firm', 'kind', 'stages', 'check_min', 'check_max', 'sectors', 'geographies', 'apply_url',
               'accepts_applications', 'auto_submit_allowed', 'notes')
CSV_MAX_ROWS = 5000
CSV_MAX_BYTES = 2 * 1024 * 1024

CARD_MINIMUM_CENTS = 50          # Stripe's minimum charge in USD
PRICE_KEYS = (('fnd_price_cents', 'The Founder Track price'), ('fnd_addon_price_cents', 'The add-on price'))


def _err(message, code=400):
    return jsonify({'error': 'refused', 'message': str(message)}), code


def _same_origin() -> bool:
    """A browser sends Origin (or at least Referer) on a cross-site POST, and
    multipart uploads need no preflight, so mutating admin calls must come
    from this host. Requests with neither header (scripts, the test client)
    pass; the admin session still has to be valid."""
    src = request.headers.get('Origin') or request.headers.get('Referer')
    if not src:
        return True
    if src == 'null':
        return False
    return urlsplit(src).netloc.lower() == (request.host or '').lower()


def admin_api(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not _same_origin():
            return jsonify({'error': 'refused', 'message': 'Cross-site request refused.'}), 403
        return f(*args, **kwargs)
    return decorated


# ── Validation shared by the settings endpoint ───────────────────────────────

def validate_settings(changes: dict) -> dict:
    """Clean every changed setting, then check the rules that span settings.
    Returns {key: clean}. Raises founder_config.FounderConfigError."""
    from app.services import founder_config as fc
    if not isinstance(changes, dict) or not changes:
        raise fc.FounderConfigError('Nothing to save.')
    clean = {}
    for key, value in changes.items():
        if key not in EDITABLE:
            raise fc.FounderConfigError('%s cannot be changed here.' % key)
        clean[key] = fc._validate(key, value)

    def after(k):
        return clean[k] if k in clean else fc.get(k)

    matrix, cap = after('fnd_autonomy_matrix'), after('fnd_first_contact_cap')
    fc_row = matrix.get('first_contact') or {}
    if fc_row.get('max', 0) > cap or fc_row.get('default', 0) > cap:
        raise fc.FounderConfigError('First contact cannot go above the first-contact cap (level %d). '
                                    'Lower the first-contact row or raise the cap.' % cap)
    if (matrix.get('negotiate_terms') or {}).get('max', 0) != 0:
        raise fc.FounderConfigError('Negotiating or accepting terms is never permitted.')
    if after('fnd_gate_rung_intros') < after('fnd_gate_rung_applications'):
        raise fc.FounderConfigError('The introductions gate cannot be below the applications gate.')
    # Checkout refuses anything under the card minimum, and there is no free unlock.
    for key, label in PRICE_KEYS:
        if after(key) < CARD_MINIMUM_CENTS:
            raise fc.FounderConfigError('%s must be at least $%.2f (the card minimum).'
                                        % (label, CARD_MINIMUM_CENTS / 100))
    inv = after('fnd_investor_access_cents')
    if 0 < inv < CARD_MINIMUM_CENTS:
        raise fc.FounderConfigError('Investor access must be $0 or at least $%.2f (the card minimum).'
                                    % (CARD_MINIMUM_CENTS / 100))
    if after('fnd_addon_price_cents') > after('fnd_price_cents'):
        raise fc.FounderConfigError('The add-on (member) price cannot be above the Founder Track price.')
    return clean


# ── Investor directory CSV import ────────────────────────────────────────────

def _list_cell(v: str) -> list:
    parts = re.split(r'[;|]', v or '') if (';' in (v or '') or '|' in (v or '')) else (v or '').split(',')
    return [p.strip() for p in parts if p.strip()][:50]


def _money_cell(v: str):
    s = (v or '').strip().lower().replace('$', '').replace(',', '').replace(' ', '')
    if not s:
        return None
    mult = 1
    if s[-1:] in ('k', 'm'):
        mult = 1000 if s[-1] == 'k' else 1000000
        s = s[:-1]
    try:
        n = float(s) * mult
    except ValueError:
        raise ValueError('"%s" is not an amount' % v)
    if n < 0 or n > 10 ** 11:
        raise ValueError('"%s" is out of range' % v)
    return int(round(n))


def _bool_cell(v: str) -> bool:
    s = (v or '').strip().lower()
    if s in ('', '0', 'no', 'n', 'false', 'f'):
        return False
    if s in ('1', 'yes', 'y', 'true', 't'):
        return True
    raise ValueError('"%s" is not yes or no' % v)


def import_investor_csv(text: str, admin_id: str) -> dict:
    """Upsert admin-curated investor_profile rows from CSV text (header row
    required; columns CSV_COLUMNS, only name is required). A row matching an
    existing admin row on name + firm (case-insensitive) updates it.
    Returns {created, updated, errors: [{row, message}]}. Commits."""
    from app.models.audit_log import AuditLog
    from app.models.founder import InvestorProfile
    text = (text or '').lstrip('﻿')
    if not text.strip():
        raise ValueError('The file is empty.')
    reader = csv.DictReader(io.StringIO(text))
    header = [(h or '').strip().lower() for h in (reader.fieldnames or [])]
    if 'name' not in header:
        raise ValueError('The first row must be a header with at least a "name" column. Columns: '
                         + ', '.join(CSV_COLUMNS) + '.')
    unknown = [h for h in header if h and h not in CSV_COLUMNS]
    if unknown:
        raise ValueError('Unknown columns: %s. Expected: %s.' % (', '.join(unknown), ', '.join(CSV_COLUMNS)))
    reader.fieldnames = header

    existing = {((p.name or '').strip().lower(), (p.firm or '').strip().lower()): p
                for p in InvestorProfile.query.filter_by(source='admin').all()}
    created = updated = 0
    errors = []
    for n, raw in enumerate(reader, start=2):
        if n - 1 > CSV_MAX_ROWS:
            errors.append({'row': n, 'message': 'Stopped after %d rows.' % CSV_MAX_ROWS})
            break
        row = {k: (v or '').strip() for k, v in raw.items() if k}
        if not any(row.values()):
            continue
        try:
            name = row.get('name', '')
            if not name:
                raise ValueError('name is required')
            if len(name) > 200 or len(row.get('firm', '')) > 200:
                raise ValueError('name and firm are 200 characters at most')
            kind = (row.get('kind') or 'investor').lower()
            if kind not in InvestorProfile.KINDS:
                raise ValueError('kind must be one of: ' + ', '.join(InvestorProfile.KINDS))
            check_min, check_max = _money_cell(row.get('check_min')), _money_cell(row.get('check_max'))
            if check_min is not None and check_max is not None and check_min > check_max:
                raise ValueError('check_min is above check_max')
            url = row.get('apply_url', '')
            if url and not re.match(r'^https?://[^\s]+$', url, re.I):
                raise ValueError('apply_url must start with http:// or https://')
            if len(url) > 500:
                raise ValueError('apply_url is too long')
            vals = dict(name=name, firm=row.get('firm') or None, kind=kind,
                        stages=_list_cell(row.get('stages')), check_min=check_min, check_max=check_max,
                        sectors=_list_cell(row.get('sectors')), geographies=_list_cell(row.get('geographies')),
                        apply_url=url or None, accepts_applications=_bool_cell(row.get('accepts_applications')),
                        auto_submit_allowed=_bool_cell(row.get('auto_submit_allowed')),
                        notes=(row.get('notes') or '')[:5000] or None)
        except ValueError as exc:
            errors.append({'row': n, 'message': str(exc)})
            continue
        key = (name.lower(), (vals['firm'] or '').lower())
        p = existing.get(key)
        if p is None:
            p = InvestorProfile(source='admin', added_by=admin_id, created_at=datetime.utcnow())
            db.session.add(p)
            existing[key] = p
            created += 1
        else:
            updated += 1
        for k, v in vals.items():
            setattr(p, k, v)
    AuditLog.log('founder_investor_import', user_id=admin_id,
                 metadata={'created': created, 'updated': updated, 'errors': len(errors)})
    db.session.commit()
    return {'created': created, 'updated': updated, 'errors': errors[:200]}


def _investor_dict(p) -> dict:
    return {'id': p.id, 'name': p.name, 'firm': p.firm or '', 'kind': p.kind, 'stages': p.stages or [],
            'check_min': p.check_min, 'check_max': p.check_max, 'sectors': p.sectors or [],
            'geographies': p.geographies or [], 'apply_url': p.apply_url or '',
            'accepts_applications': bool(p.accepts_applications), 'auto_submit_allowed': bool(p.auto_submit_allowed),
            'source': p.source}


# ── Page ─────────────────────────────────────────────────────────────────────

def _overrides() -> dict:
    from app.models.mail_crm import UserFeatureOverride
    from app.models.user import User
    keys = [k for k, _, _ in SWITCHES]
    rows = UserFeatureOverride.query.filter(UserFeatureOverride.feature_key.in_(keys),
                                            UserFeatureOverride.state != 'follow_global').all()
    users = {u.id: u for u in User.query.filter(User.id.in_([r.user_id for r in rows] or ['-'])).all()}
    out = {k: [] for k in keys}
    for r in rows:
        out[r.feature_key].append({'email': getattr(users.get(r.user_id), 'email', r.user_id), 'state': r.state})
    return out


def _name_checks() -> list:
    """Startup pages whose name still needs an admin look (pending or held)."""
    from app.models.founder import FounderVenture, StartupPage
    pages = (StartupPage.query.filter(StartupPage.name_check.in_(('pending', 'held')))
             .order_by(StartupPage.created_at).limit(500).all())
    owners = {v.id: v for v in FounderVenture.query.filter(
        FounderVenture.id.in_([p.venture_id for p in pages] or ['-'])).all()}
    return [{'id': p.id, 'name': p.name, 'slug': p.slug, 'status': p.status, 'name_check': p.name_check,
             'venture': getattr(owners.get(p.venture_id), 'name', ''),
             'created_at': p.created_at.strftime('%Y-%m-%d') if p.created_at else ''} for p in pages]


def _metrics() -> dict:
    from app.models.audit_log import AuditLog
    from app.models.founder import FounderVenture, InvestorProfile
    paid = FounderVenture.query.filter(FounderVenture.paid_at.isnot(None))
    return {
        'ventures': FounderVenture.query.count(),
        'paid': paid.count(),
        'addon': paid.filter(FounderVenture.track == 'addon').count(),
        'revenue_cents': int(db.session.query(db.func.coalesce(db.func.sum(FounderVenture.amount_cents), 0))
                             .filter(FounderVenture.paid_at.isnot(None)).scalar() or 0),
        'investors': InvestorProfile.query.count(),
        'duplicates': AuditLog.query.filter_by(action='founder_duplicate_payment').count(),
        'refunded': AuditLog.query.filter_by(action='founder_refunded').count(),
    }


@founder_bp.route('/admin/founder')
@login_required
def admin_page():
    if not current_user.is_admin:
        abort(403)
    from app.models.founder import InvestorProfile
    from app.services import founder_config as fc, founder_feature as ff
    tab = request.args.get('tab', 'overview')
    if tab not in dict(TABS):
        tab = 'overview'
    settings = fc.all_settings()
    investors = InvestorProfile.query.order_by(InvestorProfile.source, InvestorProfile.name).limit(1000).all()
    return render_template(
        'admin/founder.html', tab=tab, tabs=TABS, settings=settings,
        modes={k: ff.mode(k) for k, _, _ in SWITCHES}, switches=SWITCHES, overrides=_overrides(),
        counsel_at=settings.get('fnd_counsel_signoff_at') or '', metrics=_metrics(),
        dimensions=fc.DIMENSIONS, level_labels=LEVEL_LABELS, action_labels=ACTION_LABELS,
        target_labels=TARGET_LABELS, preview_descriptions=PREVIEW_DESCRIPTIONS, pricing=fc.pricing(),
        investors=[_investor_dict(p) for p in investors], csv_columns=CSV_COLUMNS, name_checks=_name_checks(),
        today=date.today().isoformat(),
    )


# ── JSON API ─────────────────────────────────────────────────────────────────

@founder_bp.route('/api/admin/founder/settings', methods=['GET'])
@admin_api
def api_admin_settings_get():
    from app.services import founder_config as fc
    return jsonify({'settings': fc.all_settings()})


@founder_bp.route('/api/admin/founder/settings', methods=['PUT', 'POST'])
@admin_api
def api_admin_settings():
    from app.services import founder_config as fc
    body = request.get_json(silent=True) or {}
    changes = body.get('settings') if isinstance(body.get('settings'), dict) else None
    if changes is None:
        return _err('Send {"settings": {key: value}}.')
    try:
        clean = validate_settings(changes)
        # The batch was checked as a whole; founder_config.set also checks the
        # first-contact cap per key, so save a rising cap before the matrix and
        # a falling cap after it.
        order = list(clean)
        if 'fnd_first_contact_cap' in clean:
            order.remove('fnd_first_contact_cap')
            rising = int(clean['fnd_first_contact_cap']) >= int(fc.get('fnd_first_contact_cap'))
            order = (['fnd_first_contact_cap'] + order) if rising else (order + ['fnd_first_contact_cap'])
        for key in order:
            fc.set(key, clean[key], current_user.id)  # audit-logged and committed per key
    except fc.FounderConfigError as exc:
        db.session.rollback()
        return _err(exc)
    return jsonify({'saved': sorted(clean), 'settings': fc.all_settings()})


@founder_bp.route('/api/admin/founder/mode', methods=['POST'])
@admin_api
def api_admin_mode():
    from app.services import founder_feature as ff
    b = request.get_json(silent=True) or {}
    key = b.get('key') or ff.TRACK
    try:
        ff.set_mode(key, b.get('mode'), current_user.id)
    except ff.FounderError as exc:
        db.session.rollback()
        return _err(exc)
    return jsonify({'key': key, 'mode': ff.mode(key)})


@founder_bp.route('/api/admin/founder/users', methods=['POST'])
@admin_api
def api_admin_users():
    from app.models.audit_log import AuditLog
    from app.models.user import User
    from app.services import founder_feature as ff
    b = request.get_json(silent=True) or {}
    key = b.get('key') or ff.TRACK
    emails = [str(e).strip().lower() for e in (b.get('emails') or []) if e and str(e).strip()]
    if not emails:
        return _err('Enter at least one email.')
    found = User.query.filter(db.func.lower(User.email).in_(emails)).all()
    missing = sorted(set(emails) - {u.email.lower() for u in found})
    if missing:
        return _err('No user with: ' + ', '.join(missing))
    state = b.get('state') or 'allowed'
    try:
        if state not in ff.STATES:
            raise ff.FounderError('Unknown state.')
        AuditLog.log('founder_access', user_id=current_user.id,
                     metadata={'key': key, 'state': state, 'users': [u.id for u in found][:200]})
        n = ff.set_user_access([u.id for u in found], state, current_user.id, key=key)   # commits
    except ff.FounderError as exc:
        db.session.rollback()
        return _err(exc)
    return jsonify({'updated': n, 'overrides': _overrides().get(key, [])})


@founder_bp.route('/api/admin/founder/counsel', methods=['POST'])
@admin_api
def api_admin_counsel():
    from app.services import founder_config as fc
    b = request.get_json(silent=True) or {}
    if b.get('confirm') is not True:
        return _err('Confirm the change first.')
    action = b.get('action')
    reference = ''
    if action == 'record':
        reference = ' '.join(str(b.get('reference') or '').split())[:300]
        if len(reference) < 3:
            return _err('Name counsel or the sign-off reference (memo, email or matter number).')
        when = str(b.get('date') or '').strip()
        try:
            d = datetime.fromisoformat(when).date() if when else None
        except ValueError:
            d = None
        if d is None:
            return _err('Enter the sign-off date (YYYY-MM-DD).')
        if d > date.today():
            return _err('The sign-off date cannot be in the future.')
        value = d.isoformat()
    elif action == 'clear':
        value = ''
    else:
        return _err('Unknown action.')
    try:
        from app.models.audit_log import AuditLog
        AuditLog.log('founder_counsel_' + action, user_id=current_user.id,
                     metadata={'date': value, 'reference': reference} if action == 'record' else None)
        fc.set('fnd_counsel_signoff_at', value, current_user.id)
    except fc.FounderConfigError as exc:
        db.session.rollback()
        return _err(exc)
    return jsonify({'counsel_signoff_at': fc.get('fnd_counsel_signoff_at')})


@founder_bp.route('/api/admin/founder/name-checks/<page_id>', methods=['POST'])
@admin_api
def api_admin_name_check(page_id):
    """Clear or hold a startup page's name (trademark or impersonation look)."""
    from app.models.audit_log import AuditLog
    from app.models.founder import StartupPage
    b = request.get_json(silent=True) or {}
    state = b.get('state')
    if state not in ('clear', 'held'):
        return _err('Choose clear or held.')
    p = StartupPage.query.get(page_id)
    if p is None:
        return _err('Not found.', 404)
    before = p.name_check
    p.name_check = state
    AuditLog.log('founder_name_check', user_id=current_user.id, resource_id=p.id,
                 metadata={'from': before, 'to': state, 'name': p.name})
    db.session.commit()
    return jsonify({'id': p.id, 'name_check': p.name_check})


@founder_bp.route('/api/admin/founder/investors', methods=['GET'])
@admin_api
def api_admin_investors():
    from app.models.founder import InvestorProfile
    rows = InvestorProfile.query.order_by(InvestorProfile.source, InvestorProfile.name).limit(1000).all()
    return jsonify({'investors': [_investor_dict(p) for p in rows]})


@founder_bp.route('/api/admin/founder/investors/import', methods=['POST'])
@admin_api
def api_admin_investors_import():
    f = request.files.get('file')
    if f is not None:
        raw = f.read(CSV_MAX_BYTES + 1)
    else:
        raw = ((request.get_json(silent=True) or {}).get('csv') or '').encode('utf-8')
    if len(raw) > CSV_MAX_BYTES:
        return _err('The file is over 2 MB.')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return _err('Save the file as UTF-8 CSV and try again.')
    try:
        result = import_investor_csv(text, current_user.id)
    except ValueError as exc:
        db.session.rollback()
        return _err(exc)
    except csv.Error as exc:
        db.session.rollback()
        return _err('The CSV could not be read: %s' % exc)
    return jsonify(result)


@founder_bp.route('/api/admin/founder/investors/<investor_id>', methods=['DELETE'])
@admin_api
def api_admin_investor_delete(investor_id):
    from app.models.audit_log import AuditLog
    from app.models.founder import InvestorProfile, InvestorTarget
    p = InvestorProfile.query.get(investor_id)
    if p is None:
        return _err('Not found.', 404)
    if p.source != 'admin':
        return _err('Investors a founder added belong to that founder; they are not deleted here.')
    # Founders' targets keep their own copy of the name and firm; only the link is cleared.
    InvestorTarget.query.filter_by(investor_profile_id=p.id).update({'investor_profile_id': None},
                                                                  synchronize_session=False)
    AuditLog.log('founder_investor_delete', user_id=current_user.id, resource_id=p.id,
                 metadata={'name': p.name, 'firm': p.firm or ''})
    db.session.delete(p)
    db.session.commit()
    return jsonify({'deleted': investor_id})
