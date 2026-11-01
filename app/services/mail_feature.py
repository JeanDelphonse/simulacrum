"""The Email-to-CRM feature switch (PRD-SIM-MAIL-001 "Admin controls").

Global mode (off | allowlist | on) plus per-user overrides (follow_global |
allowed | blocked) and per-provider switches. Rules:

- A global off always wins; no per-user setting turns the feature back on.
- Fails closed: if the switch can't be read, the feature is off.
- Turning it off (globally, for users, or for one provider) pauses or revokes
  the affected connections and marks every contact loaded through them Do Not
  Contact, in the same call, before anything else can send. Scheduled sends to
  those contacts are cancelled and unsent drafts expire.
- Turning it back on lifts nothing. An admin may lift only the flags one
  change set; DNC for any other reason (unsubscribe, bounce, complaint, the
  user's own choice) is never lifted by the switch.

Also holds the feature's admin settings (PlatformSetting `mail_*`, nothing
hardcoded) with the PRD's proposed defaults.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

FEATURE = 'email_to_crm'
PROVIDER_KEYS = {'gmail': FEATURE + '.gmail', 'm365': FEATURE + '.m365', 'imap': FEATURE + '.imap'}
ENRICHMENT_KEY = FEATURE + '.enrichment'

# Phase 1 ships allowlist-only; providers on (Microsoft 365 isn't built yet);
# enrichment off until legal sign-off.
DEFAULT_MODES = {FEATURE: 'allowlist', PROVIDER_KEYS['gmail']: 'on', PROVIDER_KEYS['imap']: 'on',
                 PROVIDER_KEYS['m365']: 'off', ENRICHMENT_KEY: 'off'}

DEFAULT_SETTINGS = {
    'mail_max_accounts_per_user': 5,
    'mail_lookback_months': 24,
    'mail_score_import': 70,
    'mail_score_review': 40,
    'mail_signature_lines': 15,
    'mail_claude_per_sync': 2000,
    'mail_batch_messages': 200,
    'mail_sync_seconds': 40,
    'mail_backfill_min_scanned': 2000,
    'mail_backfill_min_yield': 2,          # new contacts per 1,000 messages
    'mail_automated_patterns': 'noreply,no-reply,donotreply,do-not-reply,notifications,notification,'
                               'mailer-daemon,postmaster,bounce,bounces,alerts,newsletter,news,updates,'
                               'automated,auto-confirm,system',
    'mail_marketing_domains': 'mailchimp.com,mcsv.net,mcdlv.net,sendgrid.net,mailgun.org,hubspotemail.net,'
                              'hs-email.net,constantcontact.com,klaviyomail.com,sendinblue.com,brevo.com,'
                              'mailerlite.com,amazonses.com,exacttarget.com,salesforce-mc.com,cmail19.com,'
                              'createsend.com,list-manage.com,substack.com,convertkit-mail.com,beehiiv.com',
    'mail_freemail_domains': 'gmail.com,googlemail.com,yahoo.com,ymail.com,hotmail.com,outlook.com,live.com,'
                             'msn.com,icloud.com,me.com,mac.com,aol.com,proton.me,protonmail.com,zoho.com,'
                             'gmx.com,mail.com,yandex.com,fastmail.com',
    'mail_role_prefixes': 'info,sales,support,help,hello,contact,admin,billing,accounts,office,team,jobs,'
                          'careers,hr,press,media,marketing,legal,privacy,security,abuse,webmaster',
}


class FeatureOff(Exception):
    """The switch says no; nothing was connected or synced."""


def setting(key: str):
    default = DEFAULT_SETTINGS[key]
    try:
        from app.models.platform_settings import PlatformSetting
        raw = PlatformSetting.get(key, None)
    except Exception:
        return default
    if raw is None or raw == '':
        return default
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


def setting_list(key: str) -> list:
    return [x.strip().lower() for x in str(setting(key)).split(',') if x.strip()]


def mode(feature_key: str) -> str:
    """The global mode, or 'off' if it can't be read (fail closed)."""
    try:
        from app.models.mail_crm import FeatureFlag
        row = FeatureFlag.query.filter_by(feature_key=feature_key).first()
        return row.mode if row else DEFAULT_MODES.get(feature_key, 'off')
    except Exception as exc:
        logger.error('feature switch %s unreadable, treating as off: %s', feature_key, exc)
        return 'off'


def user_state(user_id: str, feature_key: str = FEATURE) -> str:
    from app.models.mail_crm import UserFeatureOverride
    row = UserFeatureOverride.query.filter_by(user_id=user_id, feature_key=feature_key).first()
    return row.state if row else UserFeatureOverride.STATE_FOLLOW


def is_enabled(user_id: str, provider: str | None = None) -> bool:
    """May this user connect or sync (optionally: through this provider)?"""
    try:
        m = mode(FEATURE)
        if m == 'off':
            return False
        state = user_state(user_id)
        if state == 'blocked':
            return False
        if m == 'allowlist' and state != 'allowed':
            return False
        if provider is not None and mode(PROVIDER_KEYS.get(provider, '')) != 'on':
            return False
        return True
    except Exception as exc:
        logger.error('email-to-crm gate failed closed for %s: %s', user_id, exc)
        return False


def require(user_id: str, provider: str | None = None) -> None:
    if not is_enabled(user_id, provider):
        raise FeatureOff('Email import is not available on your account.')


def status_for(user_id: str) -> dict:
    """What the user's settings page shows."""
    return {'enabled': is_enabled(user_id),
            'providers': {p: is_enabled(user_id, p) for p in ('imap', 'gmail')},
            'enrichment': mode(ENRICHMENT_KEY) == 'on'}


# ---------------------------------------------------------------------------
# Switch changes (admin)
# ---------------------------------------------------------------------------

def _record(feature_key, scope, scope_ref, old, new, action, reason, admin_id):
    from app.extensions import db
    from app.models.mail_crm import FeatureChange
    ch = FeatureChange(feature_key=feature_key, scope=scope, scope_ref=(scope_ref or '')[:300] or None,
                       old_value=old, new_value=new, connection_action=action, reason=reason[:300],
                       changed_by=admin_id)
    db.session.add(ch)
    db.session.flush()
    return ch


def _require_reason(reason: str) -> str:
    reason = (reason or '').strip()
    if len(reason) < 3:
        raise ValueError('A reason is required.')
    return reason


def _accounts_q(user_ids=None, provider=None):
    from app.models.mail_crm import EmailAccount
    q = EmailAccount.query.filter(EmailAccount.status != EmailAccount.STATUS_DISCONNECTED)
    if user_ids is not None:
        q = q.filter(EmailAccount.user_id.in_(list(user_ids) or ['-']))
    if provider:
        q = q.filter(EmailAccount.provider == provider)
    return q


def _shut_down(accounts: list, action: str, change, admin_id: str) -> int:
    """Pause/revoke these accounts and mark their contacts DNC. Returns contacts marked."""
    from app.extensions import db
    from app.models.mail_crm import ContactAccountLink, EmailAccount
    for acc in accounts:
        if action == 'revoke':
            acc.token_enc = None
            acc.status = EmailAccount.STATUS_DISCONNECTED
        elif acc.status in (EmailAccount.STATUS_ACTIVE, EmailAccount.STATUS_REAUTH, EmailAccount.STATUS_PAUSED):
            acc.status = EmailAccount.STATUS_ADMIN_PAUSED
    db.session.flush()
    ids = [a.id for a in accounts]
    contact_ids = sorted({l.contact_id for l in ContactAccountLink.query.filter(
        ContactAccountLink.account_id.in_(ids or ['-'])).all()})
    marked = mark_dnc(contact_ids, 'integration_disabled', change.id, admin_id)
    change.accounts_affected = len(accounts)
    change.contacts_marked = marked
    _notify_users({a.user_id for a in accounts}, change)
    return marked


def mark_dnc(contact_ids: list, reason: str, change_id: str | None, admin_id: str | None) -> int:
    """Flag contacts DNC for `reason`; cancel scheduled sends and expire drafts.
    Returns how many contacts got a new flag."""
    from app.extensions import db
    from app.models.contact import Contact
    from app.models.mail_crm import ContactDncFlag
    n = 0
    for i in range(0, len(contact_ids), 500):
        chunk = contact_ids[i:i + 500]
        open_flags = {(f.contact_id) for f in ContactDncFlag.query.filter(
            ContactDncFlag.contact_id.in_(chunk), ContactDncFlag.reason == reason,
            ContactDncFlag.lifted_at.is_(None)).all()}
        any_flags = {f.contact_id for f in ContactDncFlag.query.filter(
            ContactDncFlag.contact_id.in_(chunk), ContactDncFlag.lifted_at.is_(None)).all()}
        for c in Contact.query.filter(Contact.id.in_(chunk)).all():
            if c.id in open_flags:
                continue
            db.session.add(ContactDncFlag(contact_id=c.id, reason=reason, source_change_id=change_id,
                                          prior_dnc=bool(c.do_not_contact) and c.id not in any_flags,
                                          set_by=admin_id))
            c.do_not_contact = True
            n += 1
    db.session.flush()
    _stop_sends(contact_ids)
    return n


def _stop_sends(contact_ids: list) -> None:
    """Cancel scheduled steps and expire unsent drafts (EXEC P2/P5) to these contacts."""
    from app.extensions import db
    try:
        from app.models.action_step import ActionStep
        for i in range(0, len(contact_ids), 500):
            # Steps name their contact in condition_ref; any others are caught by
            # the Do Not Contact check in send_outreach_email at send time.
            ActionStep.query.filter(ActionStep.condition_ref.in_(contact_ids[i:i + 500]),
                                    ActionStep.status == ActionStep.STATUS_SCHEDULED).update(
                {'status': ActionStep.STATUS_CANCELLED}, synchronize_session=False)
    except Exception as exc:
        logger.warning('could not cancel scheduled steps: %s', exc)
    try:
        from app.models.artifact_exec import ArtifactUnit
        for i in range(0, len(contact_ids), 500):
            ArtifactUnit.query.filter(ArtifactUnit.contact_id.in_(contact_ids[i:i + 500]),
                                      ArtifactUnit.status == ArtifactUnit.STATUS_DRAFT).update(
                {'status': ArtifactUnit.STATUS_DISCARDED}, synchronize_session=False)
    except Exception as exc:
        logger.warning('could not expire send drafts: %s', exc)
    db.session.flush()


def _notify_users(user_ids: set, change) -> None:
    try:
        from app.services.notification_service import send_notification
    except Exception:
        return
    for uid in user_ids:
        try:
            send_notification(
                user_id=uid, notification_type='email_import_disabled',
                title='Email import is turned off',
                body=('Email import has been turned off for your account. Contacts it imported are now '
                      'marked Do Not Contact, so no campaign or agent will email them. Questions? '
                      'Contact simi@simulacrumai.io.'),
                cta_url='/settings/email-import', cta_label='See details', priority='high')
        except Exception as exc:
            logger.warning('email-import off notice failed for %s: %s', uid, exc)


def _audit(admin_id, change):
    from app.models.audit_log import AuditLog
    AuditLog.log('feature_switch_changed', user_id=admin_id, resource_id=change.id, metadata=change.to_dict())


def set_global(feature_key: str, new_mode: str, connection_action: str, reason: str, admin_id: str):
    """Change a global or provider switch. Restricting it shuts down every
    connection the new mode no longer allows."""
    from app.extensions import db
    from app.models.mail_crm import FeatureFlag, UserFeatureOverride
    if feature_key != FEATURE and feature_key not in PROVIDER_KEYS.values() and feature_key != ENRICHMENT_KEY:
        raise ValueError('Unknown feature.')
    if new_mode not in ('off', 'allowlist', 'on') or (feature_key != FEATURE and new_mode == 'allowlist'):
        raise ValueError('Unknown mode.')
    if connection_action not in ('pause', 'revoke'):
        raise ValueError('Choose pause or revoke.')
    reason = _require_reason(reason)
    old = mode(feature_key)
    row = FeatureFlag.query.filter_by(feature_key=feature_key).first()
    if row is None:
        row = FeatureFlag(feature_key=feature_key, mode=new_mode)
        db.session.add(row)
    row.mode, row.connection_action, row.reason = new_mode, connection_action, reason
    row.updated_by, row.updated_at = admin_id, datetime.utcnow()
    scope = 'global' if feature_key == FEATURE else 'provider'
    change = _record(feature_key, scope, feature_key.split('.')[-1] if scope == 'provider' else None,
                     old, new_mode, connection_action, reason, admin_id)

    if feature_key == ENRICHMENT_KEY:
        pass                                   # phase 4; nothing to shut down yet
    elif feature_key == FEATURE and new_mode != 'on' and old != new_mode:
        accounts = _accounts_q().all()
        if new_mode == 'allowlist':
            allowed = {o.user_id for o in UserFeatureOverride.query.filter_by(
                feature_key=FEATURE, state=UserFeatureOverride.STATE_ALLOWED).all()}
            accounts = [a for a in accounts if a.user_id not in allowed]
        _shut_down(accounts, connection_action, change, admin_id)
    elif feature_key in PROVIDER_KEYS.values() and new_mode == 'off' and old != 'off':
        provider = feature_key.split('.')[-1]
        _shut_down(_accounts_q(provider=provider).all(), connection_action, change, admin_id)
    elif new_mode == 'on' or (feature_key == FEATURE and new_mode == 'allowlist'):
        _resume_admin_paused(None if feature_key == FEATURE else feature_key.split('.')[-1])
    _audit(admin_id, change)
    db.session.commit()
    return change


def set_user(user_ids: list, state: str, reason: str, admin_id: str, connection_action: str = 'pause'):
    """Per-user override (one or many). Blocking shuts their connections down."""
    from app.extensions import db
    from app.models.mail_crm import UserFeatureOverride
    if state not in ('follow_global', 'allowed', 'blocked'):
        raise ValueError('Unknown state.')
    if connection_action not in ('pause', 'revoke'):
        raise ValueError('Choose pause or revoke.')
    reason = _require_reason(reason)
    user_ids = [u for u in dict.fromkeys(user_ids or []) if u]
    if not user_ids:
        raise ValueError('Pick at least one user.')
    was_enabled = {u: is_enabled(u) for u in user_ids}
    for uid in user_ids:
        row = UserFeatureOverride.query.filter_by(user_id=uid, feature_key=FEATURE).first()
        if row is None:
            row = UserFeatureOverride(user_id=uid, feature_key=FEATURE)
            db.session.add(row)
        row.state, row.reason, row.set_by, row.set_at = state, reason, admin_id, datetime.utcnow()
        row.connection_action = connection_action
    db.session.flush()
    change = _record(FEATURE, 'user', ','.join(user_ids), None, state, connection_action, reason, admin_id)
    now_off = [u for u in user_ids if was_enabled[u] and not is_enabled(u)]
    if now_off:
        _shut_down(_accounts_q(user_ids=now_off).all(), connection_action, change, admin_id)
    now_on = [u for u in user_ids if is_enabled(u)]
    if now_on:
        _resume_admin_paused(None, user_ids=now_on)
    _audit(admin_id, change)
    db.session.commit()
    return change


def _resume_admin_paused(provider: str | None, user_ids: list | None = None) -> None:
    """Admin-paused connections resume where they stopped once allowed again.
    Revoked connections stay disconnected (the user must reconnect)."""
    from app.models.mail_crm import EmailAccount
    q = EmailAccount.query.filter_by(status=EmailAccount.STATUS_ADMIN_PAUSED)
    if provider:
        q = q.filter_by(provider=provider)
    if user_ids is not None:
        q = q.filter(EmailAccount.user_id.in_(user_ids or ['-']))
    for acc in q.all():
        if is_enabled(acc.user_id, acc.provider):
            acc.status = EmailAccount.STATUS_ACTIVE


def lift_flags(change_id: str, reason: str, admin_id: str) -> int:
    """Lift only the DNC flags one switch change set. A contact stays DNC if it
    has any other open flag, was DNC before the switch, or is suppressed."""
    from app.extensions import db
    from app.models.contact import Contact
    from app.models.mail_crm import ContactDncFlag, FeatureChange, UserSuppression, email_hash
    from app.models.outreach_email import EmailSuppression
    from app.models.audit_log import AuditLog
    reason = _require_reason(reason)
    change = FeatureChange.query.get(change_id)
    if change is None:
        raise ValueError('Unknown change.')
    flags = ContactDncFlag.query.filter_by(source_change_id=change_id, lifted_at=None).all()
    now, cleared = datetime.utcnow(), 0
    for f in flags:
        f.lifted_at, f.lifted_by = now, admin_id
    db.session.flush()
    for f in flags:
        c = Contact.query.get(f.contact_id)
        if c is None or f.prior_dnc:
            continue
        still = ContactDncFlag.query.filter(ContactDncFlag.contact_id == c.id,
                                            ContactDncFlag.lifted_at.is_(None)).count()
        suppressed = (EmailSuppression.is_suppressed(c.email or '') or UserSuppression.query.filter_by(
            user_id=c.user_id, email_hash=email_hash(c.email)).first() is not None)
        if not still and not suppressed:
            c.do_not_contact = False
            cleared += 1
    change.flags_lifted_at = now
    AuditLog.log('feature_dnc_lifted', user_id=admin_id, resource_id=change_id,
                 metadata={'reason': reason, 'flags': len(flags), 'contacts_cleared': cleared})
    db.session.commit()
    return cleared


def active_dnc_reasons(contact_id: str) -> list:
    from app.models.mail_crm import ContactDncFlag
    return [f.reason for f in ContactDncFlag.query.filter(ContactDncFlag.contact_id == contact_id,
                                                          ContactDncFlag.lifted_at.is_(None)).all()]
