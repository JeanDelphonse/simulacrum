"""The safe send pipeline (EXEC_PLAN P5) — every send is hard-gated.

A batch of drafts (P2 "Send to contacts") goes out only when the user sends an
explicit confirmation for EXACTLY the drafts they previewed: the confirmation
carries batch_hash() of every recipient, subject and body, and any change
since the preview (re-prepared, discarded, edited) makes it fail. No autonomy
level, plan approval or orchestrator path can send through here.

At send time every recipient is re-checked (do-not-contact, unsubscribed,
placeholder, already contacted by this agent, still the user's contact). Each
email carries a one-click unsubscribe link + List-Unsubscribe headers, goes
through the observability guardrail check at the tool boundary, and is logged
(email_logs via send_outreach_email, which also enforces the recipient ledger).

Sending account — platform setting ``exec_send_provider``:
  simisi_sendgrid (default)  SimiSI's SendGrid, from the platform sender with the
                             user's name as display name
  own_sendgrid               the user's own SendGrid account: their API key and a
                             sender address verified in their SendGrid
Limits: exec_send_max_per_day (50 per user), exec_send_cost_cents (0) counted
against exec_budget_cents_per_month.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

PROVIDER_SIMISI = 'simisi_sendgrid'
PROVIDER_OWN = 'own_sendgrid'
PROVIDERS = {
    PROVIDER_SIMISI: "SimiSI's SendGrid account",
    PROVIDER_OWN: "The user's own SendGrid account",
}
OWN_PROVIDER_KEY = 'sendgrid'            # user_integrations.provider
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class SendRefused(Exception):
    """A user-facing refusal; nothing was sent."""


def _setting(key: str, default):
    try:
        from app.models.platform_settings import PlatformSetting
        return PlatformSetting.get(key, default)
    except Exception:
        return default


def provider() -> str:
    value = str(_setting('exec_send_provider', PROVIDER_SIMISI) or PROVIDER_SIMISI)
    return value if value in PROVIDERS else PROVIDER_SIMISI


def _int_setting(key: str, default: int) -> int:
    try:
        return max(0, int(_setting(key, default)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# The user's own SendGrid account
# ---------------------------------------------------------------------------

def own_account(user_id: str):
    from app.models.integration import UserIntegration
    rec = UserIntegration.query.filter_by(user_id=user_id, provider=OWN_PROVIDER_KEY).first()
    return rec if rec and rec.is_connected else None


def own_account_info(user_id: str) -> dict:
    rec = own_account(user_id)
    if rec is None:
        return {'connected': False}
    meta = json.loads(rec.meta_json) if rec.meta_json else {}
    return {'connected': True, 'from_email': meta.get('from_email'), 'from_name': meta.get('from_name')}


def connect_own_account(user_id: str, api_key: str, from_email: str, from_name: str = '') -> dict:
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.integration import UserIntegration
    from app.services.token_crypto import encrypt_token
    from utils.id_gen import generate_id
    api_key = (api_key or '').strip()
    from_email = (from_email or '').strip().lower()
    if not api_key.startswith('SG.') or len(api_key) < 20:
        raise SendRefused('That doesn\'t look like a SendGrid API key (it starts with "SG.").')
    if not _EMAIL_RE.match(from_email):
        raise SendRefused('Enter the sender address you verified in SendGrid.')
    rec = UserIntegration.query.filter_by(user_id=user_id, provider=OWN_PROVIDER_KEY).first()
    if rec is None:
        rec = UserIntegration(id=generate_id(), user_id=user_id, provider=OWN_PROVIDER_KEY)
        db.session.add(rec)
    rec.access_token_enc = encrypt_token(api_key)
    rec.meta_json = json.dumps({'from_email': from_email, 'from_name': (from_name or '').strip()[:100]})
    AuditLog.log('own_send_account_connected', user_id=user_id, metadata={'from_email': from_email})
    db.session.commit()
    return own_account_info(user_id)


def disconnect_own_account(user_id: str) -> None:
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.integration import UserIntegration
    UserIntegration.query.filter_by(user_id=user_id, provider=OWN_PROVIDER_KEY).delete()
    AuditLog.log('own_send_account_disconnected', user_id=user_id)
    db.session.commit()


def sender_for(user) -> dict:
    """{'api_key', 'from_email', 'from_name', 'provider'} for this user's sends.
    Raises SendRefused when the configured account isn't ready."""
    from flask import current_app
    name = (getattr(user, 'full_name', '') or 'SimiSI').strip()
    if provider() == PROVIDER_OWN:
        rec = own_account(user.id)
        if rec is None:
            raise SendRefused('Sending uses your own SendGrid account — connect it first '
                              '(API key and a sender verified in SendGrid).')
        meta = json.loads(rec.meta_json) if rec.meta_json else {}
        return {'api_key': rec.decrypt_access_token(), 'from_email': meta.get('from_email'),
                'from_name': meta.get('from_name') or name, 'provider': PROVIDER_OWN}
    if not current_app.config.get('SENDGRID_API_KEY'):
        raise SendRefused('Sending is not configured on this server.')
    return {'api_key': None,
            'from_email': current_app.config.get('MAIL_DEFAULT_SENDER', 'simi@simulacrumai.io'),
            'from_name': name, 'provider': PROVIDER_SIMISI}


# ---------------------------------------------------------------------------
# Confirmation bound to exact content
# ---------------------------------------------------------------------------

def batch_hash(units: list, emails: dict) -> str:
    """Hash of everything the user confirms: each draft's id, recipient,
    subject and body. Any change after the preview changes the hash."""
    h = hashlib.sha256()
    for u in sorted(units, key=lambda x: x.id):
        for part in (u.id, emails.get(u.contact_id) or '', u.subject or '', u.body_ref or ''):
            h.update(part.encode('utf-8'))
            h.update(b'\x00')
    return h.hexdigest()


def batches(action_id: str) -> list:
    """Open draft batches for the send view: [{batch_id, count, hash}]."""
    from app.models.artifact_exec import ArtifactUnit
    from app.models.contact import Contact
    units = ArtifactUnit.query.filter_by(artifact_id=action_id, kind=ArtifactUnit.KIND_VARIANT,
                                         status=ArtifactUnit.STATUS_DRAFT).all()
    emails = {c.id: c.email for c in Contact.query.filter(
        Contact.id.in_([u.contact_id for u in units if u.contact_id])).all()} if units else {}
    out = {}
    for u in units:
        out.setdefault(u.batch_id, []).append(u)
    return [{'batch_id': b, 'count': len(us), 'hash': batch_hash(us, emails)} for b, us in out.items()]


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def _sent_since(user_id: str, since) -> int:
    from app.models.agent_action import AgentAction
    from app.models.artifact_exec import ArtifactUnit
    from app.models.simulation import Simulation
    return (ArtifactUnit.query
            .join(AgentAction, AgentAction.id == ArtifactUnit.artifact_id)
            .join(Simulation, Simulation.id == AgentAction.simulation_id)
            .filter(Simulation.user_id == user_id, ArtifactUnit.status == ArtifactUnit.STATUS_SENT,
                    ArtifactUnit.sent_at >= since).count())


def _check_limits(user_id: str, n: int) -> None:
    cap = _int_setting('exec_send_max_per_day', 50)
    if _sent_since(user_id, datetime.utcnow() - timedelta(days=1)) + n > cap:
        raise SendRefused('Daily send limit is %d emails; this batch would exceed it.' % cap)
    per_email = _int_setting('exec_send_cost_cents', 0)
    if per_email:
        from app.models.agent_plan import ExecutionRun
        from app.services.plan_executor import limit
        month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = sum(r.cost_cents or 0 for r in ExecutionRun.query.filter(
            ExecutionRun.user_id == user_id, ExecutionRun.created_at >= month_start).all())
        spent += per_email * _sent_since(user_id, month_start)
        if spent + per_email * n > limit('exec_budget_cents_per_month'):
            raise SendRefused('Monthly execution budget reached.')


def send_batch(action, user, batch_id: str, confirm_hash: str, confirmed: bool) -> dict:
    """Send one confirmed batch of drafts. Returns per-recipient results."""
    from app.extensions import db
    from app.models.agent_plan import ActionApproval
    from app.models.artifact_exec import ArtifactUnit
    from app.models.audit_log import AuditLog
    from app.models.contact import Contact
    from app.services import agent_plans, artifact_actions
    from app.services import observability_service as obs
    from app.services.guardrail_service import ToolBlocked
    from app.services.outreach_email_service import send_outreach_email

    if confirmed is not True:
        raise SendRefused('Sending needs your explicit confirmation.')
    refusal = artifact_actions.send_refusal(action)
    if refusal:
        raise SendRefused(refusal)
    if agent_plans.effective_level(user.id, action.action_type) == 'L0':
        raise SendRefused('This agent is generate-only; it cannot send.')

    units = (ArtifactUnit.query.filter_by(artifact_id=action.id, batch_id=batch_id,
                                          kind=ArtifactUnit.KIND_VARIANT, status=ArtifactUnit.STATUS_DRAFT)
             .order_by(ArtifactUnit.seq_index).all())
    if not units:
        raise SendRefused('There are no drafts to send in this batch.')
    contacts = {c.id: c for c in Contact.query.filter(
        Contact.id.in_([u.contact_id for u in units]), Contact.user_id == user.id).all()}
    emails = {cid: c.email for cid, c in contacts.items()}
    if not confirm_hash or confirm_hash != batch_hash(units, emails):
        raise SendRefused('These drafts changed since you previewed them — review them again before sending.')
    sender = sender_for(user)
    _check_limits(user.id, len(units))

    db.session.add(ActionApproval(target_type='send_batch', target_id=batch_id, required_reason='hard_gate',
                                  decided_by=user.id, decision='approve'))
    db.session.commit()
    unit_ids = [u.id for u in units]
    eligibility = {c['id']: c for c in artifact_actions.contact_candidates(action, user.id, limit=100000)}

    obs_run = obs.start_run(agent_key=action.action_type, layer=action.layer_number, user_id=user.id,
                            simulation_id=action.simulation_id, action_id=action.id,
                            params={'batch_id': batch_id, 'recipients': len(units)},
                            dispatch_source='send')
    results, blocked = [], None
    for uid in unit_ids:
        unit = ArtifactUnit.query.get(uid)
        info = eligibility.get(unit.contact_id)
        if blocked:
            unit.status, unit.error = ArtifactUnit.STATUS_FAILED, ('Not sent: ' + blocked)[:300]
        elif info is None or not info['eligible']:
            unit.status = ArtifactUnit.STATUS_SKIPPED
            unit.error = (info or {}).get('reason') or 'No longer one of your contacts'
        elif unit.warnings:
            # A literal "[First Name]" must never reach a real inbox.
            unit.status = ArtifactUnit.STATUS_SKIPPED
            unit.error = '; '.join(unit.warnings)[:300]
        else:
            try:
                with obs.tool_span('send_email', {'contact_id': unit.contact_id, 'subject': unit.subject},
                                   side_effecting=True) as span:
                    res = send_outreach_email(
                        simulation_id=action.simulation_id, contact_id=unit.contact_id,
                        subject=unit.subject or '', html_body=_html(unit.body_ref),
                        from_email=sender['from_email'], from_name=sender['from_name'],
                        action_id=action.id, api_key=sender['api_key'], unsubscribe=True)
                    span['output'] = 'status=%s' % res.get('status')
                if res.get('status') == 'sent':
                    unit.status, unit.sent_at = ArtifactUnit.STATUS_SENT, datetime.utcnow()
                    unit.email_log_id = res.get('email_log_id')
                elif res.get('status') == 'skipped':
                    unit.status, unit.error = ArtifactUnit.STATUS_SKIPPED, str(res.get('reason'))[:300]
                else:
                    unit.status, unit.error = ArtifactUnit.STATUS_FAILED, str(res.get('reason'))[:300]
            except ToolBlocked as exc:
                blocked = str(exc)
                unit.status, unit.error = ArtifactUnit.STATUS_FAILED, ('Blocked: ' + blocked)[:300]
        db.session.commit()
        results.append({'unit_id': unit.id, 'contact_id': unit.contact_id,
                        'to': emails.get(unit.contact_id), 'status': unit.status, 'error': unit.error})
    if obs_run is not None:
        obs.finish_run(status=obs.STATUS_BLOCKED if blocked else obs.STATUS_SUCCESS)

    sent = sum(1 for r in results if r['status'] == ArtifactUnit.STATUS_SENT)
    AuditLog.log('send_batch', user_id=user.id, resource_id=action.id,
                 metadata={'batch_id': batch_id, 'sent': sent, 'total': len(results),
                           'provider': sender['provider']})
    db.session.commit()
    artifact_actions.log_action(action.id, 'send', actor_id=user.id,
                                status='blocked' if blocked else 'ok',
                                result={'batch_id': batch_id, 'sent': sent, 'total': len(results)})
    return {'batch_id': batch_id, 'sent': sent, 'total': len(results), 'blocked': blocked,
            'provider': sender['provider'], 'results': results}


def _html(body: str) -> str:
    """Plain-text draft → simple HTML paragraphs (escaped)."""
    import html
    paras = [p.strip() for p in re.split(r'\n{2,}', body or '') if p.strip()]
    return ''.join('<p>%s</p>' % html.escape(p).replace('\n', '<br>') for p in paras)
