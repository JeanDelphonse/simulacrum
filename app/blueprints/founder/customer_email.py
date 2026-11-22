"""Founder Track customer email: contact book and send flow
(CUSTOMER_EMAIL_PLAN.md section 4).

Pages
  /founder/v/<id>/contacts                       contact book, outbox, do-not-contact list
  /founder/v/<id>/send/<action_id>               send one customer draft through SimiSI

JSON (founders of the venture only; paid ventures only)
  POST /api/founder/v/<id>/contacts                         add one
  POST /api/founder/v/<id>/contacts/<cid>                   edit fields / poc flag
  POST /api/founder/v/<id>/contacts/<cid>/<decision>        approve | reject | remove | suppress | restore | replied
  POST /api/founder/v/<id>/contacts/<cid>/access            {agent, allowed}
  POST /api/founder/v/<id>/contacts/import                  CSV upload (multipart 'file' or JSON 'text')
  GET  /api/founder/v/<id>/contacts/mailbox                 mailbox import candidates
  POST /api/founder/v/<id>/contacts/mailbox                 {ids: [...]}
  POST /api/founder/v/<id>/messages/<mid>/cancel            cancel a scheduled email
  POST /api/founder/actions/<aid>/customer/edit             {subject, body}
  POST /api/founder/actions/<aid>/customer/settings         {scheduling_link?, locked_prices?}
  POST /api/founder/actions/<aid>/customer/preview          {contact_ids}
  POST /api/founder/actions/<aid>/customer/send             {contact_ids, approved_each, approve_campaign, hash, schedule_at?}
"""
from __future__ import annotations

from flask import abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.blueprints.founder.common import founder_required


def _svc():
    from app.services import founder_customer_email
    return founder_customer_email


def _venture(venture_id):
    from app.services import founder_ventures
    return founder_ventures.require_member(venture_id, current_user)


def _err(message, status=400, **extra):
    body = {'ok': False, 'error': message}
    body.update(extra)
    return jsonify(body), status


def _payload() -> dict:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict() if data is None else {}


def _paid_api(v):
    from app.services import founder_ventures
    if not founder_ventures.is_paid(v):
        return _err('Customer email unlocks with the Founder Track.', 402,
                    checkout_url=url_for('founder.checkout_page', venture_id=v.id))
    return None


def _call(fn, *args, **kwargs):
    from app.extensions import db
    svc = _svc()
    try:
        return fn(*args, **kwargs), None
    except svc.CustomerEmailError as exc:
        db.session.rollback()
        return None, _err(str(exc), exc.status)


def _nav(v):
    from app.blueprints.founder.common import venture_nav
    return venture_nav(v)


def _action(action_id):
    """(venture, action) for a customer draft of a venture the user is on."""
    from app.extensions import db
    from app.models.founder import VentureOutreachAction
    a = db.session.get(VentureOutreachAction, action_id)
    if a is None:
        abort(404)
    v = _venture(a.venture_id)
    if _svc().action_agent(a) is None:
        abort(404)
    return v, a


# ── Pages ────────────────────────────────────────────────────────────────────

@founder_bp.route('/founder/v/<venture_id>/contacts')
@founder_required
def ce_contacts_page(venture_id):
    from app.services import founder_ventures
    v = _venture(venture_id)
    if not founder_ventures.is_paid(v):
        if v.owner_user_id == current_user.id:
            return redirect(url_for('founder.checkout_page', venture_id=v.id))
        return redirect(url_for('founder.report_page', venture_id=v.id))
    return render_template('founder/contacts.html', venture=v, nav=_nav(v), active='contacts',
                           book=_svc().book_view(v, current_user))


@founder_bp.route('/founder/v/<venture_id>/send/<action_id>')
@founder_required
def ce_send_page(venture_id, action_id):
    v, a = _action(action_id)
    if v.id != venture_id:
        abort(404)
    from app.services import founder_ventures
    if not founder_ventures.is_paid(v):
        return redirect(url_for('founder.report_page', venture_id=v.id))
    return render_template('founder/send.html', venture=v, nav=_nav(v), active='contacts', action=a,
                           view=_svc().send_view(v, a, current_user))


# ── Contact book API ─────────────────────────────────────────────────────────

@founder_bp.route('/api/founder/v/<venture_id>/contacts', methods=['POST'])
@founder_required
def ce_add_contact(venture_id):
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    d = _payload()
    c, err = _call(_svc().add_contact, v, current_user, d.get('email'), name=d.get('name'), company=d.get('company'),
                   role=d.get('role'), segment=d.get('segment'), note=d.get('note'))
    if err:
        return err
    agents = d.get('agents') or []
    if isinstance(agents, str):
        agents = [agents]
    for k in agents:
        _call(_svc().set_access, v, current_user, c.id, k, True)
    return jsonify({'ok': True, 'contact': c.to_dict(), 'message': 'Added %s.' % c.email}), 201


@founder_bp.route('/api/founder/v/<venture_id>/contacts/import', methods=['POST'])
@founder_required
def ce_import_csv(venture_id):
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    f = request.files.get('file')
    if f is not None:
        raw = f.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return _err('The file is larger than 2 MB.')
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('latin-1')
        d = request.form.to_dict()
        agents = request.form.getlist('agents')
    else:
        d = _payload()
        text = d.get('text') or ''
        agents = d.get('agents') or []
    own = str(d.get('own_list') or '').lower() in ('1', 'true', 'on', 'yes')
    out, err = _call(_svc().import_csv, v, current_user, text, own, agents)
    if err:
        return err
    return jsonify(dict(out, ok=True, message='Added %d contact%s%s.' % (
        out['added'], '' if out['added'] == 1 else 's',
        '; %d row%s skipped' % (out['skipped_count'], '' if out['skipped_count'] == 1 else 's')
        if out['skipped_count'] else ''))), 200


@founder_bp.route('/api/founder/v/<venture_id>/contacts/mailbox', methods=['GET', 'POST'])
@founder_required
def ce_mailbox(venture_id):
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    if request.method == 'GET':
        out, err = _call(_svc().mailbox_candidates, v, current_user)
        return err or jsonify({'ok': True, 'candidates': out})
    ids = _payload().get('ids') or []
    out, err = _call(_svc().import_mailbox, v, current_user, ids if isinstance(ids, list) else [ids])
    if err:
        return err
    return jsonify(dict(out, ok=True, message='Added %d contact%s.' % (out['added'], '' if out['added'] == 1 else 's')))


@founder_bp.route('/api/founder/v/<venture_id>/contacts/<contact_id>', methods=['POST'])
@founder_required
def ce_edit_contact(venture_id, contact_id):
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    c, err = _call(_svc().update_contact, v, current_user, contact_id, _payload())
    return err or jsonify({'ok': True, 'contact': c.to_dict(), 'message': 'Saved.'})


@founder_bp.route('/api/founder/v/<venture_id>/contacts/<contact_id>/access', methods=['POST'])
@founder_required
def ce_contact_access(venture_id, contact_id):
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    d = _payload()
    allowed = d.get('allowed') not in (False, 'false', '0', 0, None, 'off')
    out, err = _call(_svc().set_access, v, current_user, contact_id, d.get('agent'), allowed)
    return err or jsonify({'ok': True, 'allowed': out})


@founder_bp.route('/api/founder/v/<venture_id>/contacts/<contact_id>/<decision>', methods=['POST'])
@founder_required
def ce_contact_decision(venture_id, contact_id, decision):
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    svc = _svc()
    if decision == 'replied':
        c, err = _call(svc.mark_replied, v, current_user, contact_id)
        return err or jsonify({'ok': True, 'contact': c.to_dict(), 'message': 'Marked as replied.'})
    if decision not in ('approve', 'reject', 'remove', 'suppress', 'restore'):
        abort(404)
    c, err = _call(svc.decide_contact, v, current_user, contact_id, decision)
    if err:
        return err
    msgs = {'approve': 'Approved.', 'reject': 'Rejected and removed.', 'remove': 'Removed.',
            'suppress': 'Added to your do-not-contact list.', 'restore': 'Restored.'}
    return jsonify({'ok': True, 'contact': c.to_dict() if c is not None else None, 'message': msgs[decision]})


@founder_bp.route('/api/founder/v/<venture_id>/messages/<message_id>/cancel', methods=['POST'])
@founder_required
def ce_cancel_message(venture_id, message_id):
    v = _venture(venture_id)
    m, err = _call(_svc().cancel, v, current_user, message_id)
    return err or jsonify({'ok': True, 'message': 'Cancelled.'})


# ── Send flow API ────────────────────────────────────────────────────────────

@founder_bp.route('/api/founder/actions/<action_id>/customer/edit', methods=['POST'])
@founder_required
def ce_edit_draft(action_id):
    v, a = _action(action_id)
    d = _payload()
    out, err = _call(_svc().edit_draft, v, a, current_user, d.get('subject'), d.get('body'))
    return err or jsonify({'ok': True, 'message': 'Draft saved.'})


@founder_bp.route('/api/founder/actions/<action_id>/customer/settings', methods=['POST'])
@founder_required
def ce_draft_settings(action_id):
    v, a = _action(action_id)
    d = _payload()
    out, err = _call(_svc().set_meta, v, a, current_user, scheduling_link=d.get('scheduling_link'),
                     locked_prices=d.get('locked_prices'))
    return err or jsonify({'ok': True, 'meta': out, 'message': 'Saved.'})


def _ids(d, key='contact_ids'):
    ids = d.get(key) or []
    return ids if isinstance(ids, list) else [ids]


@founder_bp.route('/api/founder/actions/<action_id>/customer/preview', methods=['POST'])
@founder_required
def ce_preview(action_id):
    v, a = _action(action_id)
    out, err = _call(_svc().preview, v, a, current_user, _ids(_payload()))
    return err or jsonify(dict(out, ok=True))


@founder_bp.route('/api/founder/actions/<action_id>/customer/send', methods=['POST'])
@founder_required
def ce_send(action_id):
    v, a = _action(action_id)
    d = _payload()
    out, err = _call(_svc().send, v, a, current_user, _ids(d), approved_each=_ids(d, 'approved_each'),
                     approve_campaign=d.get('approve_campaign') in (True, 'true', '1', 1, 'on'),
                     confirm_hash=str(d.get('hash') or ''), schedule_at=d.get('schedule_at') or None)
    if err:
        return err
    parts = []
    if out['sent']:
        parts.append('Sent %d' % out['sent'])
    if out['scheduled']:
        parts.append('%d scheduled' % out['scheduled'])
    if out['skipped'] or out['failed']:
        parts.append('%d not sent (%s)' % (out['skipped'] + out['failed'], '; '.join(out['reasons']) or 'see Contacts'))
    msg = ', '.join(parts) or 'Nothing sent.'
    return jsonify(dict(out, ok=True, message=msg + '. Replies go to %s.' % (current_user.email or 'you'))), 200
