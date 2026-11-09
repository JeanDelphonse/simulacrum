"""Members invite their contacts to try SimiSI, from /contacts.

The invite promotes SimiSI, so SimiSI is the sender for the commercial-email
rules (COMPLIANCE_PLAN C1-C3, C7): the one suppression check, an unsubscribe
link with one-click headers, SimiSI's postal address in the legal footer,
reply STOP through the inbound parse address, a daily cap per member (Admin
setting contact_invite_daily_cap) and an outreach_send audit row. One invite
per member per address; it turns into 'joined' when that person signs up.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from html import escape

logger = logging.getLogger(__name__)

SESSION_KEY = 'contact_invite_token'
SITE_LABEL = 'SimiSI.io'                 # how the address reads in the email body
SITE_URL = 'https://SimiSI.io'


class InviteError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── Status shown on /contacts ────────────────────────────────────────────────

def statuses(user_id: str, contacts) -> dict:
    """{contact_id: {'status': 'not_invited'|'invited'|'joined'|'member'|'blocked', ...}}.

    'member' means the address already has a SimiSI account and was never
    invited by this member; 'blocked' means it can't be emailed (opted out,
    bounced, Do Not Contact).
    """
    from app.models.contact_invite import ContactInvite
    from app.models.user import User
    from app.services import suppression_service
    contacts = list(contacts)
    emails = {(c.email or '').strip().lower() for c in contacts if c.email}
    invites = {i.email: i for i in ContactInvite.query.filter(
        ContactInvite.user_id == user_id, ContactInvite.email.in_(emails)).all()} if emails else {}
    members = {e for (e,) in User.query.with_entities(User.email).filter(User.email.in_(emails)).all()} if emails else set()
    members = {(e or '').lower() for e in members}
    out = {}
    for c in contacts:
        e = (c.email or '').strip().lower()
        inv = invites.get(e)
        if inv:
            out[c.id] = inv.to_dict()
        elif e and e in members:
            out[c.id] = {'status': 'member'}
        elif not e:
            out[c.id] = {'status': 'blocked', 'reason': 'No email address'}
        else:
            reason = suppression_service.check(user_id, e, c)
            out[c.id] = ({'status': 'blocked', 'reason': suppression_service.REASON_LABELS.get(reason, 'Cannot be emailed')}
                         if reason else {'status': 'not_invited'})
    return out


# ── Sending ──────────────────────────────────────────────────────────────────

def _first(name: str) -> str:
    return (name or '').strip().split(' ')[0] or 'A friend'


# ── The email copy (Admin -> Outreach -> Contact invite) ─────────────────────
# Stored as the outreach_templates row TEMPLATE_KEY; these are the defaults until
# an admin saves an edit. Plain text: paragraphs separated by a blank line.
TEMPLATE_KEY = 'contact_invite'
TOKENS = ('first_name', 'member_name', 'member_first_name', 'site')
DEFAULT_TEMPLATE = {
    'name': 'Contact invite',
    'subject': '{{member_first_name}} thinks you might like SimiSI',
    'preview_text': '{{member_first_name}} invited you to try SimiSI.',
    'body': ('Hi {{first_name}},\n\n'
             '{{member_name}} uses SimiSI and thought you might find it useful too.\n\n'
             'SimiSI turns your experience into a 24-month career plan, with AI agents that draft the work '
             'and wait for your approval. Setting up a bio page is free.\n\n'
             'Take a look at {{site}}\n\n'
             '{{member_first_name}}'),
}
MAX_SUBJECT, MAX_PREVIEW, MAX_BODY = 150, 150, 1500


def get_template() -> dict:
    """The copy in use: the admin's saved edit, else the default."""
    try:
        from app.models.outreach_campaign import OutreachTemplate
        row = OutreachTemplate.query.filter_by(template_key=TEMPLATE_KEY).first()
        if row:
            return {'subject': row.subject, 'preview_text': row.preview_text or '', 'body': row.body,
                    'updated_at': row.updated_at.isoformat() if row.updated_at else None, 'is_default': False}
    except Exception:
        logger.exception('contact invite template read failed; using the default')
    return dict(DEFAULT_TEMPLATE, updated_at=None, is_default=True)


def validate_template(subject: str, preview_text: str, body: str) -> list:
    """Problems that stop a save (empty list = fine)."""
    import re
    from app.services import compliance_filter
    errors = []
    subject, preview_text, body = (subject or '').strip(), (preview_text or '').strip(), (body or '').strip()
    if not subject or not body:
        errors.append('Subject and body are required.')
    if len(subject) > MAX_SUBJECT or len(preview_text) > MAX_PREVIEW or len(body) > MAX_BODY:
        errors.append('Keep it brief: subject and preview up to %d characters, body up to %d.' % (MAX_SUBJECT, MAX_BODY))
    if '{{site}}' not in body:
        errors.append('The body must include {{site}}, the SimiSI.io link.')
    unknown = sorted({t for t in re.findall(r'{{\s*([^}]*?)\s*}}', subject + preview_text + body)} - set(TOKENS))
    if unknown:
        errors.append('Unknown token%s: %s. Use %s.' % ('s' if len(unknown) > 1 else '',
                      ', '.join('{{%s}}' % u for u in unknown), ', '.join('{{%s}}' % t for t in TOKENS)))
    found = compliance_filter.banned_phrases(' '.join((subject, preview_text, body)))
    if found:
        errors.append('Remove earnings-claim wording: %s.' % ', '.join('"%s"' % f for f in found))
    if re.search(r'<[a-z!/]', body, re.I):
        errors.append('Plain text only: no HTML tags.')
    return errors


def render(template: dict, member_name: str, first_name: str, join_url: str) -> tuple:
    """(subject, preview, plain, inner_html) for one recipient.

    {{site}} reads SimiSI.io: a link through the personal invite link in HTML,
    https://SimiSI.io in plain text. The button follows the paragraph with {{site}}.
    """
    from app.services.email_service import _btn, _p
    who = (member_name or '').strip() or 'A friend'
    values = {'first_name': (first_name or '').strip() or 'there', 'member_name': who,
              'member_first_name': _first(who)}

    def fill(text, site):
        out = text or ''
        for k, v in dict(values, site=site).items():
            out = out.replace('{{%s}}' % k, v)
        return out.strip()

    subject = ' '.join(fill(template['subject'], SITE_LABEL).split())
    preview = ' '.join(fill(template.get('preview_text') or '', SITE_LABEL).split())
    plain = fill(template['body'], SITE_URL)
    marker = '\x00SITE\x00'
    link = '<a href="%s" style="color:#0F7B72;font-weight:600;">%s</a>' % (escape(join_url, quote=True), SITE_LABEL)
    parts, button_done = [], False
    for block in [b.strip() for b in fill(template['body'], marker).split('\n\n') if b.strip()]:
        has_site = marker in block
        parts.append(_p(escape(block).replace('\n', '<br>').replace(escape(marker), link)))
        if has_site and not button_done:
            parts.append(_btn(join_url, 'Visit %s' % SITE_LABEL))
            button_done = True
    return subject, preview, plain, ''.join(parts)


def send_invite(user, contact) -> 'ContactInvite':
    """Invite one contact. Raises InviteError (with an HTTP status) when it can't."""
    from flask import current_app, url_for
    from app.extensions import db
    from app.models.contact_invite import ContactInvite
    from app.models.user import User
    from app.services import send_limits, sender_identity, suppression_service
    from app.services.outreach_campaign_service import unsubscribe_url_for

    if contact is None or contact.user_id != user.id:
        raise InviteError('Contact not found.', 404)
    email = (contact.email or '').strip().lower()
    if not email:
        raise InviteError('This contact has no email address.', 400)
    if email == (user.email or '').strip().lower():
        raise InviteError("That's your own address.", 400)
    trail = {'unsubscribe': True}

    def refuse(message, status, reason):
        sender_identity.record_send('contact_invite', {'status': 'skipped', 'reason': reason}, trail,
                                    user_id=user.id, resource_id=contact.id)
        raise InviteError(message, status)

    existing = ContactInvite.query.filter_by(user_id=user.id, email=email).first()
    if existing:
        raise InviteError('Already invited on %s.' % existing.sent_at.strftime('%b %d, %Y'), 409)
    if User.query.filter(db.func.lower(User.email) == email).first():
        raise InviteError('%s already has a SimiSI account.' % (contact.first_name or 'This contact'), 409)

    suppressed = suppression_service.check(user.id, email, contact)
    trail['suppression'] = suppressed or 'clear'
    if suppressed:
        refuse("This contact can't be emailed (%s)." % suppression_service.REASON_LABELS.get(suppressed, suppressed).lower(),
               409, 'suppressed:' + suppressed)

    cap = int(send_limits.setting('contact_invite_daily_cap'))
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today = ContactInvite.query.filter(ContactInvite.user_id == user.id, ContactInvite.sent_at >= today).count()
    trail['cap_check'] = 'ok' if sent_today < cap else 'over_invite_cap'
    if sent_today >= cap:
        refuse("You've sent today's %d invites. You can send more tomorrow." % cap, 429, 'over_invite_cap')

    address = sender_identity.business_address()
    trail['postal_address_set'] = bool(address)
    if not address:
        refuse('Invites are paused for now. Please try again later.', 503, 'no_business_address')

    api_key = current_app.config.get('SENDGRID_API_KEY')
    if not api_key:
        refuse("Email isn't set up yet, so invites can't be sent.", 503, 'no_api_key')

    token = secrets.token_urlsafe(24)
    join_url = url_for('pages.contact_invite_join', token=token, _external=True)
    unsub_url = unsubscribe_url_for(email)
    subject, preview, plain, inner = render(get_template(), user.full_name or '', contact.first_name, join_url)
    footer = sender_identity.footer_html(user.full_name or 'A SimiSI member', address, unsub_url)
    plain += '\n\n' + _plain_footer(user.full_name, address, unsub_url)
    from app.services.email_service import _html_wrap
    html = _html_wrap(inner, preheader=escape(preview), footer_html=footer)

    from utils.id_gen import generate_pk
    invite = ContactInvite(id=generate_pk(), user_id=user.id, contact_id=contact.id, email=email, token=token,
                           status=ContactInvite.STATUS_INVITED, sent_at=datetime.utcnow())
    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import CustomArg, From, Header, Mail
        message = Mail(from_email=From(current_app.config.get('MAIL_DEFAULT_SENDER', 'simi@simulacrumai.io'),
                                       '%s via SimiSI' % (user.full_name or 'A SimiSI member')),
                       to_emails=email, subject=subject, plain_text_content=plain, html_content=html)
        message.header = [Header('List-Unsubscribe', '<%s>' % unsub_url),
                          Header('List-Unsubscribe-Post', 'List-Unsubscribe=One-Click')]
        message.custom_arg = [CustomArg('contact_invite_id', invite.id), CustomArg('contact_id', contact.id)]
        domain = current_app.config.get('INBOUND_PARSE_DOMAIN', 'mail.simulacrumai.io')
        message.reply_to = 'reply-%s@%s' % (contact.id, domain)      # reply STOP opts them out
        response = sg_module.SendGridAPIClient(api_key).send(message)
        if getattr(response, 'status_code', 202) >= 400:
            raise RuntimeError('SendGrid returned %s' % response.status_code)
        invite.provider_message_id = (getattr(response, 'headers', None) or {}).get('X-Message-Id')
    except Exception as exc:
        logger.error('contact invite to %s failed: %s', contact.id, exc, exc_info=True)
        db.session.rollback()
        sender_identity.record_send('contact_invite', {'status': 'error', 'reason': str(exc)[:200]}, trail,
                                    user_id=user.id, resource_id=contact.id)
        raise InviteError("The invite couldn't be sent. Please try again.", 502)

    db.session.add(invite)
    from app.models.contact import ContactActivity
    db.session.add(ContactActivity(contact_id=contact.id, activity_type='simisi_invite',
                                   notes='Invited to try SimiSI'))
    db.session.commit()
    sender_identity.record_send('contact_invite', {'status': 'sent'}, trail, user_id=user.id, resource_id=invite.id)
    return invite


def _plain_footer(sender_name, address, unsub_url) -> str:
    from app.services import legal_text
    return legal_text.get('email_footer', sender_name=sender_name or 'A SimiSI member',
                          sender_postal_address=address, unsubscribe_url=unsub_url)


# ── Joining ──────────────────────────────────────────────────────────────────

def remember_token(session, token: str) -> bool:
    """The invite link was opened: keep the token for registration."""
    from app.models.contact_invite import ContactInvite
    if not token or not ContactInvite.query.filter_by(token=token).first():
        return False
    session[SESSION_KEY] = token
    return True


def mark_joined(new_user, token: str | None = None) -> int:
    """A new account was created: every invite to that address (and the one
    whose link was used) becomes 'joined'. Never raises; returns rows updated."""
    try:
        from app.extensions import db
        from app.models.contact_invite import ContactInvite
        email = (new_user.email or '').strip().lower()
        q = ContactInvite.query.filter(ContactInvite.status == ContactInvite.STATUS_INVITED)
        conds = [ContactInvite.email == email] if email else []
        if token:
            conds.append(ContactInvite.token == token)
        if not conds:
            return 0
        rows = q.filter(db.or_(*conds)).all()
        now = datetime.utcnow()
        for r in rows:
            r.status, r.joined_at, r.joined_user_id = ContactInvite.STATUS_JOINED, now, new_user.id
        return len(rows)
    except Exception:
        logger.exception('contact invite join marking failed')
        return 0



# ── Admin preview and test send ──────────────────────────────────────────────

SAMPLE = {'member_name': 'Jordan Rivera', 'first_name': 'Alex'}


def preview_html(template: dict) -> dict:
    """The email as a contact would see it, with sample names (no send)."""
    from app.services import sender_identity
    from app.services.email_service import _html_wrap
    subject, preview, plain, inner = render(template, SAMPLE['member_name'], SAMPLE['first_name'], SITE_URL)
    address = sender_identity.business_address() or "[SimiSI's postal address, Admin -> Compliance]"
    footer = sender_identity.footer_html(SAMPLE['member_name'], address, SITE_URL + '/outreach/unsubscribe/sample')
    return {'subject': subject, 'preview_text': preview, 'plain': plain,
            'html': _html_wrap(inner, preheader=escape(preview), footer_html=footer)}


def send_test(admin_user, template: dict) -> None:
    """Send the edited copy to the admin's own address. No invite row is written;
    links point at SimiSI.io without a token. Raises InviteError."""
    from flask import current_app
    from app.services import sender_identity
    api_key = current_app.config.get('SENDGRID_API_KEY')
    if not api_key:
        raise InviteError("Email isn't set up yet (no SendGrid key).", 503)
    p = preview_html(template)
    try:
        import sendgrid as sg_module
        from sendgrid.helpers.mail import From, Mail
        msg = Mail(from_email=From(current_app.config.get('MAIL_DEFAULT_SENDER', 'simi@simulacrumai.io'),
                                   '%s via SimiSI' % SAMPLE['member_name']),
                   to_emails=admin_user.email, subject='[Test] ' + p['subject'],
                   plain_text_content=p['plain'] + '\n\n' + _plain_footer(
                       SAMPLE['member_name'], sender_identity.business_address() or '[postal address]',
                       SITE_URL + '/outreach/unsubscribe/sample'),
                   html_content=p['html'])
        r = sg_module.SendGridAPIClient(api_key).send(msg)
        if getattr(r, 'status_code', 202) >= 400:
            raise RuntimeError('SendGrid returned %s' % r.status_code)
    except InviteError:
        raise
    except Exception as exc:
        logger.error('contact invite test send failed: %s', exc, exc_info=True)
        raise InviteError("The test email couldn't be sent: %s" % str(exc)[:120], 502)
