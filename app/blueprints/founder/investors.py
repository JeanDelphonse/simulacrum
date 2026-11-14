"""Founder Track investor connection, founder side (FOUNDER_PLAN.md WP8).

Page:   GET  /founder/v/<id>/investors
APIs:   POST /api/founder/v/<id>/investors/exemption            {exemption, note}
        POST /api/founder/v/<id>/investors/consent              {kind: contacts|mailbox, grant: bool}
        POST /api/founder/v/<id>/investors/match
        POST /api/founder/v/<id>/investors/targets              add an investor {name, firm, kind, ...}
        POST /api/founder/v/<id>/investors/targets/<tid>/status {status: pursuing|passed|candidate}
        POST /api/founder/v/<id>/investors/targets/<tid>/application
        POST /api/founder/v/<id>/investors/warm-paths
        POST /api/founder/v/<id>/investors/paths/<pid>/draft
        POST /api/founder/v/<id>/investors/actions/<aid>/recipient {contact_id | email | linkedin}
        POST /api/founder/v/<id>/investors/actions/<aid>/send    approve (if pending) and send
        POST /api/founder/v/<id>/investors/updates               draft an update
        POST /api/founder/v/<id>/investors/updates/<uid>/edit    {subject, body}
        POST /api/founder/v/<id>/investors/updates/<uid>/approve
        POST /api/founder/v/<id>/investors/updates/<uid>/send
Public: GET|POST /outreach/founder-unsubscribe/<token>          member-scoped unsubscribe

Every founder route 404s unless investor connection (founder_investor) is on
for the user, which also needs the Founder Track on and the counsel sign-off
recorded; 404 for an unknown venture, 403 for a non-member. The venture must
be paid: the page redirects to checkout (owner) or the report, APIs answer 402.

The unsubscribe link must keep working for anyone who got an email, so it is
not behind the switch; it acts only on a valid signed token.
"""
from __future__ import annotations

from flask import abort, jsonify, redirect, render_template, render_template_string, request, url_for
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.blueprints.founder.common import founder_required

INVESTOR = 'founder_investor'


def _venture(venture_id: str):
    from app.services import founder_ventures
    return founder_ventures.require_member(venture_id, current_user)


def _payload() -> dict:
    return request.get_json(silent=True) or request.form.to_dict() or {}


def _err(message: str, status: int = 400, **extra):
    body = {'ok': False, 'error': message}
    body.update(extra)
    return jsonify(body), status


def _paid_api(v):
    from app.services import founder_ventures
    if founder_ventures.is_paid(v):
        return None
    return _err('Investor connection unlocks with the Founder Track.', 402,
                checkout_url=url_for('founder.checkout_page', venture_id=v.id))


def _api(venture_id, fn):
    """Resolve the venture, check payment, run fn(v) and map refusals."""
    from app.services.founder_autonomy import AutonomyError
    from app.services.founder_investors import InvestorError
    v = _venture(venture_id)
    locked = _paid_api(v)
    if locked:
        return locked
    try:
        return fn(v)
    except (InvestorError, AutonomyError) as exc:
        from app.extensions import db
        db.session.rollback()
        return _err(str(exc), getattr(exc, 'status', 400))


def _nav(v) -> list:
    from app.blueprints.founder.common import venture_nav
    nav = list(venture_nav(v))
    if not any(k == 'investors' for k, _l, _h in nav):
        nav.append(('investors', 'Investors', url_for('founder.investors_page', venture_id=v.id)))
    return nav


def _target(v, tid):
    from app.models.founder import InvestorTarget
    t = InvestorTarget.query.filter_by(id=tid, venture_id=v.id).first()
    if t is None:
        abort(404)
    return t


def _action(v, aid):
    from app.models.founder import VentureOutreachAction
    a = VentureOutreachAction.query.filter_by(id=aid, venture_id=v.id).first()
    if a is None:
        abort(404)
    return a


def _update(v, uid):
    from app.models.founder import InvestorUpdate
    u = InvestorUpdate.query.filter_by(id=uid, venture_id=v.id).first()
    if u is None:
        abort(404)
    return u


def _target_dict(t) -> dict:
    return {'id': t.id, 'name': t.name, 'firm': t.firm, 'kind': t.kind, 'status': t.status,
            'fit_score': t.fit_score, 'fit_reasons': t.fit_reasons or {}, 'position': t.position}


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@founder_bp.route('/founder/v/<venture_id>/investors')
@founder_required(key=INVESTOR)
def investors_page(venture_id):
    from app.services import founder_autonomy, founder_config, founder_investors, founder_ventures
    from app.services import legal_text
    v = _venture(venture_id)
    if not founder_ventures.is_paid(v):
        if v.owner_user_id == current_user.id:
            return redirect(url_for('founder.checkout_page', venture_id=v.id))
        return redirect(url_for('founder.report_page', venture_id=v.id))
    state = founder_investors.view(v, current_user)
    return render_template('founder/investors.html', venture=v, nav=_nav(v), active='investors', s=state,
                           level_labels=founder_autonomy.LEVEL_LABELS,
                           exemptions=founder_investors.EXEMPTION_LABELS,
                           first_contact_cap=founder_config.get('fnd_first_contact_cap'),
                           daily_cap=founder_autonomy.send_cap(), sent_today=founder_autonomy.sent_today(v),
                           gate_intros=founder_config.get('fnd_gate_rung_intros'),
                           gate_apps=founder_config.get('fnd_gate_rung_applications'),
                           not_advice=legal_text.get('founder_not_advice'),
                           linkedin_note=legal_text.get('linkedin_manual'),
                           on_behalf=legal_text.get('founder_on_behalf_note',
                                                    founder_name=current_user.full_name or 'you'))


# ---------------------------------------------------------------------------
# Exemption and consents
# ---------------------------------------------------------------------------

@founder_bp.route('/api/founder/v/<venture_id>/investors/exemption', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_exemption(venture_id):
    from app.services import founder_investors as fi

    def go(v):
        d = _payload()
        row = fi.record_exemption(v, current_user, d.get('exemption'), d.get('note') or '')
        return jsonify({'ok': True, 'exemption': row.exemption, 'label': fi.EXEMPTION_LABELS[row.exemption],
                        'message': 'Recorded: %s.' % fi.EXEMPTION_LABELS[row.exemption]})
    return _api(venture_id, go)


@founder_bp.route('/api/founder/v/<venture_id>/investors/consent', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_consent(venture_id):
    from app.services import founder_investors as fi

    def go(v):
        d = _payload()
        grant = d.get('grant') in (True, 'true', '1', 1, 'on', 'yes')
        out = fi.set_consent(v, current_user, d.get('kind'), grant)
        msg = ('%s access is on.' if grant else '%s access is off. Nothing more is read.') % (
            'Contacts' if out['kind'] == 'contacts' else 'Mailbox')
        return jsonify({'ok': True, 'message': msg, **out})
    return _api(venture_id, go)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@founder_bp.route('/api/founder/v/<venture_id>/investors/match', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_match(venture_id):
    from app.services import founder_investors as fi

    def go(v):
        rows = fi.match(v, current_user)
        cal = fi.calibration(v)
        return jsonify({'ok': True, 'targets': [_target_dict(t) for t in rows], 'calibration_size': cal['size'],
                        'message': '%d matches. Review the first %d to calibrate.' % (len(rows), cal['size'])})
    return _api(venture_id, go)


@founder_bp.route('/api/founder/v/<venture_id>/investors/targets', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_add(venture_id):
    from app.services import founder_investors as fi

    def go(v):
        t = fi.add_investor(v, current_user, _payload())
        return jsonify({'ok': True, 'target': _target_dict(t), 'message': '%s added to your list.' % t.name})
    return _api(venture_id, go)


@founder_bp.route('/api/founder/v/<venture_id>/investors/targets/<target_id>/status', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_target_status(venture_id, target_id):
    from app.services import founder_investors as fi

    def go(v):
        t = fi.set_target_status(v, current_user, target_id, (_payload().get('status') or '').strip())
        return jsonify({'ok': True, 'target': _target_dict(t)})
    return _api(venture_id, go)


@founder_bp.route('/api/founder/v/<venture_id>/investors/targets/<target_id>/application', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_application(venture_id, target_id):
    from app.services import founder_investors as fi

    def go(v):
        out = fi.draft_application(v, _target(v, target_id), current_user)
        flagged = sum(1 for a in out['answers'] if a['needs_evidence'])
        return jsonify({'ok': True, 'action': out['action'].to_dict(), 'answers': out['answers'],
                        'apply_url': out['apply_url'], 'auto_submit': False,
                        'message': 'Answers drafted%s. You submit the form yourself.'
                                   % ('; %d need evidence' % flagged if flagged else '')})
    return _api(venture_id, go)


# ---------------------------------------------------------------------------
# Warm paths
# ---------------------------------------------------------------------------

@founder_bp.route('/api/founder/v/<venture_id>/investors/warm-paths', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_warm_paths(venture_id):
    from app.services import founder_investors as fi

    def go(v):
        rows = fi.warm_paths(v, current_user)
        return jsonify({'ok': True, 'count': len(rows),
                        'message': '%d warm path%s found.' % (len(rows), '' if len(rows) == 1 else 's')})
    return _api(venture_id, go)


@founder_bp.route('/api/founder/v/<venture_id>/investors/paths/<path_id>/draft', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_draft_intro(venture_id, path_id):
    from app.models.founder import IntroPath
    from app.services import founder_investors as fi

    def go(v):
        p = IntroPath.query.filter_by(id=path_id, venture_id=v.id).first()
        if p is None:
            abort(404)
        a = fi.draft_intro(v, p, current_user)
        return jsonify({'ok': True, 'action': a.to_dict(), 'message': 'Intro request drafted. Review and approve it '
                                                                      'below.'})
    return _api(venture_id, go)


# ---------------------------------------------------------------------------
# Drafts: recipient, approve and send
# ---------------------------------------------------------------------------

@founder_bp.route('/api/founder/v/<venture_id>/investors/actions/<action_id>/recipient', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_recipient(venture_id, action_id):
    from app.services import founder_investors as fi

    def go(v):
        a = fi.set_recipient(v, _action(v, action_id), current_user, _payload())
        return jsonify({'ok': True, 'action': a.to_dict(), 'message': 'Recipient saved.'})
    return _api(venture_id, go)


@founder_bp.route('/api/founder/v/<venture_id>/investors/actions/<action_id>/send', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_send(venture_id, action_id):
    from app.services import founder_autonomy

    def go(v):
        a = _action(v, action_id)
        if a.status in founder_autonomy.PENDING:
            founder_autonomy.approve(a, current_user)
            a = _action(v, action_id)
        out = founder_autonomy.send(a, current_user)
        return jsonify(dict(out, action=_action(v, action_id).to_dict()))
    return _api(venture_id, go)


# ---------------------------------------------------------------------------
# Investor updates
# ---------------------------------------------------------------------------

def _update_dict(u) -> dict:
    return {'id': u.id, 'subject': u.subject, 'body': u.body, 'status': u.status, 'approved_by': u.approved_by,
            'sent_at': u.sent_at.isoformat() if u.sent_at else None, 'recipient_count': u.recipient_count}


@founder_bp.route('/api/founder/v/<venture_id>/investors/updates', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_update_draft(venture_id):
    from app.services import founder_investors as fi

    def go(v):
        u = fi.draft_update(v, current_user)
        return jsonify({'ok': True, 'update': _update_dict(u), 'message': 'Update drafted. Edit and approve it '
                                                                          'before it is sent.'})
    return _api(venture_id, go)


@founder_bp.route('/api/founder/v/<venture_id>/investors/updates/<update_id>/edit', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_update_edit(venture_id, update_id):
    from app.services import founder_investors as fi

    def go(v):
        d = _payload()
        u = fi.edit_update(v, _update(v, update_id), current_user, d.get('subject'), d.get('body'))
        return jsonify({'ok': True, 'update': _update_dict(u), 'message': 'Saved.'})
    return _api(venture_id, go)


@founder_bp.route('/api/founder/v/<venture_id>/investors/updates/<update_id>/approve', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_update_approve(venture_id, update_id):
    from app.services import founder_investors as fi

    def go(v):
        u = fi.approve_update(v, _update(v, update_id), current_user)
        return jsonify({'ok': True, 'update': _update_dict(u), 'message': 'Approved. You can send it now.'})
    return _api(venture_id, go)


@founder_bp.route('/api/founder/v/<venture_id>/investors/updates/<update_id>/send', methods=['POST'])
@founder_required(key=INVESTOR)
def investors_update_send(venture_id, update_id):
    from app.services import founder_investors as fi

    def go(v):
        out = fi.send_update(v, _update(v, update_id), current_user)
        return jsonify({'ok': True, 'message': 'Sent to %d of %d investor%s.' % (
            out['sent'], out['total'], '' if out['total'] == 1 else 's'), **out})
    return _api(venture_id, go)


# ---------------------------------------------------------------------------
# Member-scoped unsubscribe (public)
# ---------------------------------------------------------------------------

_UNSUB_PAGE = """{% extends 'base.html' %}
{% block title %}Unsubscribe{% endblock %}
{% block content %}
<div class="wrap" style="max-width:560px;padding:3.5rem 1rem;text-align:center">
  {% if ok %}
  <h1 style="font-size:1.5rem;margin:0 0 .75rem">You are unsubscribed</h1>
  <p style="color:var(--ink-2);line-height:1.6"><strong>{{ email }}</strong> will no longer get emails sent through
  SimiSI on behalf of {{ who }}.</p>
  <p style="color:var(--mute);font-size:.9rem;line-height:1.6">This applies to this sender only. Other SimiSI members
  are not affected.</p>
  {% else %}
  <h1 style="font-size:1.5rem;margin:0 0 .75rem">This link did not work</h1>
  <p style="color:var(--ink-2);line-height:1.6">The unsubscribe link is invalid. To stop these emails, reply STOP or
  write to <a href="mailto:simi@simisi.io">simi@simisi.io</a>.</p>
  {% endif %}
</div>
{% endblock %}"""


@founder_bp.route('/outreach/founder-unsubscribe/<token>', methods=['GET', 'POST'])
def investor_unsubscribe(token):
    from app.services import founder_investors as fi
    out = fi.apply_unsubscribe(token)
    if out is None:
        return render_template_string(_UNSUB_PAGE, ok=False), 400
    v = out.get('venture')
    who = 'the founders of %s' % v.name if v is not None else 'this founder'
    return render_template_string(_UNSUB_PAGE, ok=True, email=out['email'], who=who)
