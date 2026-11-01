"""Email-to-CRM routes (PRD-SIM-MAIL-001 phase 1). Every user route checks the
feature switch first; it fails closed."""
from __future__ import annotations

import secrets
from functools import wraps

from flask import jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.blueprints.mail import mail_bp
from app.extensions import db


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return decorated


def _err(exc, code=400):
    return jsonify({'error': 'refused', 'message': str(exc)}), code


def _state() -> dict:
    from app.models.mail_crm import EmailAccount, ReviewItem, SyncRun
    from app.services import mail_feature
    accounts = EmailAccount.query.filter(EmailAccount.user_id == current_user.id,
                                         EmailAccount.status != EmailAccount.STATUS_DISCONNECTED).order_by(
        EmailAccount.created_at).all()
    out = []
    for a in accounts:
        d = a.to_dict()
        last = SyncRun.query.filter_by(account_id=a.id).order_by(SyncRun.started_at.desc()).first()
        d['last_run'] = last.to_dict() if last else None
        from app.models.mail_crm import ContactAccountLink
        d['contacts'] = ContactAccountLink.query.filter_by(account_id=a.id).count()
        out.append(d)
    return {'feature': mail_feature.status_for(current_user.id), 'accounts': out,
            'max_accounts': mail_feature.setting('mail_max_accounts_per_user'),
            'review_pending': ReviewItem.query.filter_by(user_id=current_user.id,
                                                         status=ReviewItem.STATUS_PENDING).count()}


# ── User: settings page and accounts ─────────────────────────────────────────

@mail_bp.route('/settings/email-import')
@login_required
def settings_page():
    from app.services import mail_feature
    imported_dnc = None
    if not mail_feature.is_enabled(current_user.id):
        from app.models.contact import Contact
        from app.models.mail_crm import ContactDncFlag
        imported_dnc = (db.session.query(ContactDncFlag.contact_id)
                        .join(Contact, Contact.id == ContactDncFlag.contact_id)
                        .filter(Contact.user_id == current_user.id,
                                ContactDncFlag.reason == 'integration_disabled',
                                ContactDncFlag.lifted_at.is_(None)).distinct().count())
    return render_template('mail/settings.html', state=_state(), imported_dnc=imported_dnc,
                           gmail_error=request.args.get('gmail_error'))


@mail_bp.route('/api/mail/accounts', methods=['GET'])
@login_required
def api_accounts():
    return jsonify(_state())


@mail_bp.route('/api/mail/accounts/imap', methods=['POST'])
@login_required
def api_connect_imap():
    from app.services import mail_feature, mail_sync
    b = request.get_json(silent=True) or {}
    try:
        mail_sync.connect_imap(current_user.id, b.get('address'), b.get('password'),
                               b.get('host') or '', b.get('port') or 993)
    except mail_feature.FeatureOff as exc:
        return _err(exc, 403)
    except mail_sync.SyncError as exc:
        return _err(exc)
    return jsonify(_state())


@mail_bp.route('/mail/connect/gmail')
@login_required
def gmail_connect():
    from app.services import mail_feature, mail_sync
    state = secrets.token_urlsafe(24)
    session['mail_gmail_state'] = state
    try:
        url = mail_sync.gmail_auth_url(current_user.id, url_for('mail.gmail_callback', _external=True), state)
    except (mail_feature.FeatureOff, mail_sync.SyncError) as exc:
        return redirect(url_for('mail.settings_page', gmail_error=str(exc)))
    return redirect(url)


@mail_bp.route('/mail/connect/gmail/callback')
@login_required
def gmail_callback():
    from app.services import mail_feature, mail_sync
    expected = session.pop('mail_gmail_state', None)
    if not expected or request.args.get('state') != expected:
        return redirect(url_for('mail.settings_page', gmail_error='The Google sign-in expired. Try again.'))
    if request.args.get('error') or not request.args.get('code'):
        return redirect(url_for('mail.settings_page', gmail_error='Google access was not granted.'))
    try:
        mail_sync.gmail_complete(current_user.id, request.args['code'],
                                 url_for('mail.gmail_callback', _external=True))
    except (mail_feature.FeatureOff, mail_sync.SyncError) as exc:
        return redirect(url_for('mail.settings_page', gmail_error=str(exc)))
    return redirect(url_for('mail.settings_page'))


@mail_bp.route('/api/mail/accounts/<account_id>/pause', methods=['POST'])
@login_required
def api_pause(account_id):
    from app.services import mail_feature, mail_sync
    paused = (request.get_json(silent=True) or {}).get('paused', True)
    try:
        mail_sync.set_paused(current_user.id, account_id, bool(paused))
    except (mail_feature.FeatureOff, mail_sync.SyncError) as exc:
        return _err(exc)
    return jsonify(_state())


@mail_bp.route('/api/mail/accounts/<account_id>/disconnect', methods=['POST'])
@login_required
def api_disconnect(account_id):
    from app.services import mail_sync
    b = request.get_json(silent=True) or {}
    try:
        res = mail_sync.disconnect(current_user.id, account_id, bool(b.get('delete_contacts')))
    except mail_sync.SyncError as exc:
        return _err(exc)
    return jsonify(dict(_state(), **res))


@mail_bp.route('/api/mail/accounts/<account_id>/exclusions', methods=['POST'])
@login_required
def api_add_exclusion(account_id):
    from app.services import mail_sync
    b = request.get_json(silent=True) or {}
    try:
        mail_sync.add_exclusion(current_user.id, account_id, b.get('kind') or 'domain', b.get('value'))
    except mail_sync.SyncError as exc:
        return _err(exc)
    return jsonify(_state())


@mail_bp.route('/api/mail/accounts/<account_id>/exclusions/<exclusion_id>', methods=['DELETE'])
@login_required
def api_remove_exclusion(account_id, exclusion_id):
    from app.services import mail_sync
    try:
        mail_sync.remove_exclusion(current_user.id, account_id, exclusion_id)
    except mail_sync.SyncError as exc:
        return _err(exc)
    return jsonify(_state())


@mail_bp.route('/api/mail/accounts/<account_id>/sync', methods=['POST'])
@login_required
def api_sync_now(account_id):
    """Run one batch now instead of waiting for the next cron minute."""
    from app.models.mail_crm import EmailAccount
    from app.services import mail_feature, mail_sync
    try:
        acc = mail_sync.owned_account(current_user.id, account_id)
        mail_feature.require(current_user.id, acc.provider)
    except (mail_feature.FeatureOff, mail_sync.SyncError) as exc:
        return _err(exc)
    if acc.status != EmailAccount.STATUS_ACTIVE:
        return _err('This account is %s.' % acc.status.replace('_', ' '))
    run = mail_sync.run_batch(acc)
    return jsonify(dict(_state(), run=run))


# ── User: review queue and field sources ─────────────────────────────────────

@mail_bp.route('/api/mail/review', methods=['GET'])
@login_required
def api_review():
    from app.models.mail_crm import ReviewItem
    items = ReviewItem.query.filter_by(user_id=current_user.id, status=ReviewItem.STATUS_PENDING).order_by(
        ReviewItem.created_at.desc()).limit(500).all()
    return jsonify({'items': [i.to_dict() for i in items]})


@mail_bp.route('/api/mail/review/decide', methods=['POST'])
@login_required
def api_review_decide():
    from app.services import mail_contacts, mail_feature
    if not mail_feature.is_enabled(current_user.id):
        return _err('Email import is not available on your account.', 403)
    b = request.get_json(silent=True) or {}
    try:
        res = mail_contacts.decide(current_user.id, b.get('ids') or [], b.get('decision'))
    except ValueError as exc:
        return _err(exc)
    return jsonify(res)


@mail_bp.route('/api/mail/contacts/<contact_id>/sources', methods=['GET'])
@login_required
def api_contact_sources(contact_id):
    from app.models.contact import Contact
    from app.models.mail_crm import ContactAccountLink, ContactFieldSource, EmailAccount
    from app.services import mail_contacts, mail_feature
    c = Contact.query.filter_by(id=contact_id, user_id=current_user.id).first_or_404()
    rows = ContactFieldSource.query.filter_by(contact_id=c.id).all()
    links = ContactAccountLink.query.filter_by(contact_id=c.id).all()
    addr = {a.id: a.address for a in EmailAccount.query.filter(
        EmailAccount.id.in_([l.account_id for l in links] or ['-'])).all()}
    return jsonify({
        'fields': [dict(r.to_dict(), label=mail_contacts.FIELD_LABELS.get(r.field, r.field)) for r in rows],
        'accounts': [{'address': addr.get(l.account_id), 'sent': l.sent_count, 'received': l.received_count,
                      'last_seen': l.last_seen.isoformat()} for l in links],
        'tier': c.tier, 'campaign_ready': c.campaign_ready, 'not_ready_reason': c.not_ready_reason,
        'dnc_reasons': mail_feature.active_dnc_reasons(c.id)})


# ── Admin: the switch ─────────────────────────────────────────────────────────

@mail_bp.route('/admin/email-crm')
@login_required
def admin_page():
    if not current_user.is_admin:
        from flask import abort
        abort(403)
    from app.services import mail_feature
    return render_template('mail/admin.html', state=_admin_state(),
                           settings={k: mail_feature.setting(k) for k in mail_feature.DEFAULT_SETTINGS})


def _admin_state() -> dict:
    from app.models.mail_crm import EmailAccount, FeatureChange, UserFeatureOverride
    from app.models.user import User
    from app.services import mail_feature
    keys = [mail_feature.FEATURE] + list(mail_feature.PROVIDER_KEYS.values()) + [mail_feature.ENRICHMENT_KEY]
    overrides = UserFeatureOverride.query.filter(UserFeatureOverride.state != 'follow_global').all()
    users = {u.id: u for u in User.query.filter(User.id.in_([o.user_id for o in overrides] or ['-'])).all()}
    return {
        'modes': {k: mail_feature.mode(k) for k in keys},
        'overrides': [{'user_id': o.user_id, 'email': getattr(users.get(o.user_id), 'email', None),
                       'name': getattr(users.get(o.user_id), 'full_name', None), 'state': o.state,
                       'reason': o.reason, 'set_at': o.set_at.isoformat()} for o in overrides],
        'changes': [c.to_dict() for c in FeatureChange.query.order_by(FeatureChange.changed_at.desc()).limit(50)],
        'accounts': {s: EmailAccount.query.filter_by(status=s).count()
                     for s in ('active', 'paused', 'admin_paused', 'reauth')},
    }


@mail_bp.route('/api/admin/email-crm/state', methods=['GET'])
@admin_required
def api_admin_state():
    return jsonify(_admin_state())


@mail_bp.route('/api/admin/email-crm/switch', methods=['POST'])
@admin_required
def api_admin_switch():
    from app.services import mail_feature
    b = request.get_json(silent=True) or {}
    try:
        change = mail_feature.set_global(b.get('feature_key') or mail_feature.FEATURE, b.get('mode'),
                                         b.get('connection_action') or 'pause', b.get('reason'), current_user.id)
    except ValueError as exc:
        return _err(exc)
    return jsonify(dict(_admin_state(), change=change.to_dict()))


@mail_bp.route('/api/admin/email-crm/users', methods=['POST'])
@admin_required
def api_admin_users():
    from app.models.user import User
    from app.services import mail_feature
    b = request.get_json(silent=True) or {}
    ids = list(b.get('user_ids') or [])
    emails = [e.strip().lower() for e in (b.get('emails') or []) if e and e.strip()]
    if emails:
        found = User.query.filter(db.func.lower(User.email).in_(emails)).all()
        missing = sorted(set(emails) - {u.email.lower() for u in found})
        if missing:
            return _err('No user with: ' + ', '.join(missing))
        ids += [u.id for u in found]
    try:
        change = mail_feature.set_user(ids, b.get('state'), b.get('reason'), current_user.id,
                                       b.get('connection_action') or 'pause')
    except ValueError as exc:
        return _err(exc)
    return jsonify(dict(_admin_state(), change=change.to_dict()))


@mail_bp.route('/api/admin/email-crm/changes/<change_id>/lift', methods=['POST'])
@admin_required
def api_admin_lift(change_id):
    from app.services import mail_feature
    try:
        n = mail_feature.lift_flags(change_id, (request.get_json(silent=True) or {}).get('reason'),
                                    current_user.id)
    except ValueError as exc:
        return _err(exc)
    return jsonify(dict(_admin_state(), contacts_cleared=n))
