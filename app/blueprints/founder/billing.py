"""Founder Track payment (FOUNDER_PLAN.md WP3).

  GET  /founder/v/<id>/checkout           price and what unlocks (venture owner only)
  POST /founder/v/<id>/checkout           creates a one-time Stripe Checkout Session
  GET  /founder/v/<id>/checkout/success   verifies the session (webhook fallback)

The webhook (payments/routes.py, checkout.session.completed with product
founder_track | founder_addon) and the success return both call
fulfil_founder_session(), which marks the venture paid once. A second paid
session for a venture that is already paid is flagged (log + AuditLog
founder_duplicate_payment) for an admin to refund in Stripe. A full refund
(payments/routes.py, charge.refunded) calls revoke_founder_payment(), which
locks the venture again.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from flask import abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.founder import founder_bp
from app.blueprints.founder.common import founder_required
from app.extensions import db

logger = logging.getLogger(__name__)

PRODUCTS = ('founder_track', 'founder_addon')

UNLOCKS = (
    ('The full readiness report', 'every finding in full, with the passage it cites and its confidence.'),
    ('Your agent team', 'agents matched to your gaps, with settings you control.'),
    ('The plan', 'one clear next step at a time across Validate, Build, Sell, Operate and Capitalize.'),
)

# A Stripe Checkout Session stays open for 24 hours; reuse one younger than this.
OPEN_SESSION_REUSE = timedelta(hours=23)


def _g(obj, key, default=None):
    """Read a field from a Stripe object or a plain dict."""
    if obj is None:
        return default
    try:
        v = obj.get(key, default)
    except AttributeError:
        v = getattr(obj, key, default)
    return default if v is None else v


def fulfil_founder_session(session) -> bool:
    """Mark the venture in a paid Founder Track Checkout Session as paid.
    Idempotent: True only for the call that recorded the payment. Never raises
    for a session that doesn't belong to the Founder Track; the caller commits
    nothing extra."""
    from app.models.founder import FounderVenture
    from app.services import founder_ventures
    meta = _g(session, 'metadata', {}) or {}
    product = _g(meta, 'product')
    if product not in PRODUCTS:
        return False
    if _g(session, 'payment_status') != 'paid':
        logger.info('founder checkout %s not paid yet (%s)', _g(session, 'id'), _g(session, 'payment_status'))
        return False
    venture = FounderVenture.query.get(_g(meta, 'venture_id') or '-')
    if venture is None:
        logger.error('founder checkout %s names an unknown venture %s', _g(session, 'id'), _g(meta, 'venture_id'))
        return False
    if str(_g(meta, 'user_id') or '') != str(venture.owner_user_id):
        logger.error('founder checkout %s: payer %s is not the owner of venture %s', _g(session, 'id'),
                     _g(meta, 'user_id'), venture.id)
        return False
    pi = _g(session, 'payment_intent')
    pi = _g(pi, 'id') if pi is not None and not isinstance(pi, str) else pi
    try:
        amount = int(_g(session, 'amount_total') or _g(meta, 'amount_cents') or 0)
    except (TypeError, ValueError):
        amount = 0
    now = datetime.utcnow()
    # Conditional update: the webhook and the success return can race; only one wins.
    n = FounderVenture.query.filter(FounderVenture.id == venture.id, FounderVenture.paid_at.is_(None)).update(
        {'paid_at': now, 'payment_intent_id': pi or None, 'amount_cents': amount or None,
         'track': 'addon' if product == 'founder_addon' else 'standalone', 'updated_at': now},
        synchronize_session=False)
    if not n:
        db.session.rollback()
        _flag_duplicate(venture.id, pi, session, product, amount)
        return False
    founder_ventures.log_event(venture.id, 'paid', '%s %d cents (checkout %s)' % (product, amount, _g(session, 'id')),
                               user_id=venture.owner_user_id)
    db.session.commit()
    db.session.expire_all()
    logger.info('Founder Track paid for venture %s (%s, %d cents)', venture.id, product, amount)
    return True


def _flag_duplicate(venture_id: str, pi, session, product, amount) -> None:
    """The venture was already paid. A different payment intent means the
    owner paid twice (two tabs, a retry): record it once so an admin can
    refund it. The same intent is the webhook/success-return race: nothing."""
    from app.models.audit_log import AuditLog
    from app.models.founder import FounderVenture
    if not pi:
        return
    v = FounderVenture.query.get(venture_id)
    if v is None or v.paid_at is None or v.payment_intent_id == pi:
        return
    flagged = (AuditLog.query.filter_by(action='founder_duplicate_payment', resource_id=venture_id)
               .filter(AuditLog._metadata.like('%%"%s"%%' % pi.replace('%', ''))).first())
    if flagged is not None:
        return                                  # webhook replay or the success return: already flagged
    logger.error('Founder Track duplicate payment for venture %s: %s paid again (%s, %d cents, checkout %s); '
                 'refund it in Stripe', venture_id, pi, product, amount, _g(session, 'id'))
    try:
        AuditLog.log('founder_duplicate_payment', user_id=v.owner_user_id, resource_id=venture_id,
                     metadata={'payment_intent': pi, 'checkout': _g(session, 'id'), 'product': product,
                               'amount_cents': amount, 'kept_payment_intent': v.payment_intent_id})
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error('could not audit-log the duplicate payment for venture %s: %s', venture_id, exc)


def revoke_founder_payment(payment_intent_id: str, fully_refunded: bool = True) -> bool:
    """Called from the charge.refunded webhook. When the refunded payment is
    the one that unlocked a venture and the charge is fully refunded, the
    venture is locked again (paid_at cleared; the intent and amount stay for
    the record). A partial refund is only logged. True when a venture was
    locked. Commits."""
    from app.models.founder import FounderVenture
    from app.services import founder_ventures
    if not payment_intent_id:
        return False
    v = FounderVenture.query.filter_by(payment_intent_id=payment_intent_id).first()
    if v is None:
        return False
    if v.paid_at is None:
        return False                              # already revoked (webhook replay)
    if not fully_refunded:
        founder_ventures.log_event(v.id, 'partial_refund', 'payment %s partly refunded; access kept'
                                   % payment_intent_id, user_id=v.owner_user_id)
        db.session.commit()
        return False
    now = datetime.utcnow()
    n = FounderVenture.query.filter(FounderVenture.id == v.id, FounderVenture.paid_at.isnot(None)).update({'paid_at': None, 'updated_at': now}, synchronize_session=False)
    if not n:
        db.session.rollback()
        return False
    founder_ventures.log_event(v.id, 'refunded', 'payment %s refunded (%s cents); venture locked again'
                               % (payment_intent_id, v.amount_cents), user_id=v.owner_user_id)
    db.session.commit()
    db.session.expire_all()
    logger.info('Founder Track refund: venture %s locked again (%s)', v.id, payment_intent_id)
    return True


def _open_session(venture, product: str, amount_cents: int):
    """The venture's last checkout session, if Stripe still has it open for
    the same product and price (so a double-click, second tab or back-button
    retry doesn't create a second chargeable session). If that session was
    paid, it is fulfilled here and 'paid' is returned. Else None."""
    from app.models.audit_log import AuditLog
    from app.services import stripe_service
    row = AuditLog.query.filter(AuditLog.action == 'founder_checkout_started', AuditLog.resource_id == venture.id,
                                AuditLog.created_at >= datetime.utcnow() - OPEN_SESSION_REUSE).order_by(
        AuditLog.created_at.desc()).first()
    sid = (row.extra or {}).get('session_id') if row is not None else None
    if not sid:
        return None
    try:
        cs = stripe_service._stripe().checkout.Session.retrieve(sid)
    except Exception as exc:
        logger.info('founder checkout %s could not be reused: %s', sid, exc)
        return None
    meta = _g(cs, 'metadata', {}) or {}
    if _g(meta, 'venture_id') != venture.id:
        return None
    if _g(cs, 'payment_status') == 'paid':
        fulfil_founder_session(cs)
        db.session.refresh(venture)
        return 'paid' if venture.paid_at is not None else None
    if (_g(cs, 'status') == 'open' and _g(meta, 'product') == product
            and str(_g(cs, 'amount_total') or _g(meta, 'amount_cents') or '') == str(amount_cents) and _g(cs, 'url')):
        return _g(cs, 'url')
    return None


def _owner_venture(venture_id):
    from app.services import founder_ventures
    v = founder_ventures.require_member(venture_id, current_user)
    if v.owner_user_id != current_user.id:
        abort(403)
    return v


def _report_url(venture_id: str) -> str:
    """The report page (WP2). Its endpoint name isn't fixed by the spec, so try
    the likely ones and fall back to the path."""
    for ep in ('founder.report', 'founder.report_page', 'founder.venture_report'):
        try:
            return url_for(ep, venture_id=venture_id)
        except Exception:
            continue
    return request.script_root + '/founder/v/%s/report' % venture_id


def _offer(user) -> dict:
    from app.services import founder_config, stripe_service
    prices = founder_config.pricing()
    addon = stripe_service.founder_addon_eligible(user.id)
    return {'product': 'founder_addon' if addon else 'founder_track', 'addon': addon,
            'amount_cents': prices['addon_price_cents'] if addon else prices['price_cents'],
            'full_cents': prices['price_cents'], 'refund_window_days': prices['refund_window_days'],
            'currency': (prices.get('currency') or 'usd').lower()}


def _page(venture, error=None, notice=None, code=200):
    return render_template('founder/checkout.html', venture=venture, offer=_offer(current_user), unlocks=UNLOCKS,
                           error=error, notice=notice), code


@founder_bp.route('/founder/v/<venture_id>/checkout', methods=['GET'])
@founder_required
def checkout_page(venture_id):
    v = _owner_venture(venture_id)
    if v.paid_at is not None:
        return redirect(_report_url(v.id))
    notice = 'Checkout was cancelled. Nothing was charged.' if request.args.get('cancelled') else None
    return _page(v, notice=notice)


@founder_bp.route('/founder/v/<venture_id>/checkout', methods=['POST'])
@founder_required
def checkout_start(venture_id):
    from app.services import stripe_service
    v = _owner_venture(venture_id)
    if v.paid_at is not None:
        return redirect(_report_url(v.id))
    if not current_app.config.get('STRIPE_SECRET_KEY'):
        return _page(v, error='Payments are not available right now. Please try again later.', code=503)
    offer = _offer(current_user)
    product = offer['product']
    reuse = _open_session(v, product, offer['amount_cents'])
    if reuse == 'paid':
        return redirect(_report_url(v.id))
    if reuse:
        return redirect(reuse, code=303)
    try:
        result = stripe_service.create_founder_checkout(v, current_user, product)
    except ValueError as exc:
        return _page(v, error=str(exc), code=400)
    except Exception as exc:
        logger.error('founder checkout failed for venture %s: %s', v.id, exc)
        return _page(v, error='We could not start checkout. Please try again in a minute.', code=502)
    from app.models.audit_log import AuditLog
    AuditLog.log('founder_checkout_started', user_id=current_user.id, resource_id=v.id,
                 metadata={'note': '%s %d cents' % (product, result['amount_cents']),
                           'session_id': result.get('session_id')})
    db.session.commit()
    return redirect(result['checkout_url'], code=303)


@founder_bp.route('/founder/v/<venture_id>/checkout/success')
@founder_required
def checkout_success(venture_id):
    v = _owner_venture(venture_id)
    session_id = (request.args.get('session_id') or '').strip()
    if v.paid_at is None and session_id and current_app.config.get('STRIPE_SECRET_KEY'):
        # Webhook fallback: verify the session with Stripe and record the payment.
        try:
            from app.services import stripe_service
            cs = stripe_service._stripe().checkout.Session.retrieve(session_id)
            meta = _g(cs, 'metadata', {}) or {}
            if _g(meta, 'venture_id') == v.id and str(_g(meta, 'user_id')) == str(current_user.id):
                fulfil_founder_session(cs)
        except Exception as exc:
            logger.warning('founder checkout verify failed for venture %s: %s', v.id, exc)
        db.session.refresh(v)
    if v.paid_at is not None:
        return redirect(_report_url(v.id))
    return _page(v, notice='We are confirming your payment with the card processor. '
                           'Refresh this page in a minute; you will not be charged twice.')
