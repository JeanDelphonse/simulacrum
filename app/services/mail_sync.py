"""Email-to-CRM sync (PRD-SIM-MAIL-001 phase 1): connections and the
resumable batch engine.

Production has no worker process and Passenger kills idle threads, so syncs
run as short batches from cron (cron_generate.py, every minute): each batch
reads the next slice from the account's cursor, scores the senders it saw and
saves the cursor. The switch is checked before every batch, so turning the
feature off stops syncing within a minute.

Providers:
  imap   read-only IMAP over TLS with an app password. Sent folder first (who
         the user wrote to), then the newest inbox mail; spam/junk folders are
         never read. Headers for every message; the body only for the newest
         message per sender, cut to the signature window and discarded.
  gmail  step 1: People API "Other contacts" and saved contacts only
         (contacts.readonly + contacts.other.readonly). No mail is read.
"""
from __future__ import annotations

import email as _email
import email.policy  # noqa: F401  (message_from_bytes policy=)
import imaplib
import logging
import re
import time
from datetime import datetime, timedelta
from email.utils import getaddresses, parseaddr

logger = logging.getLogger(__name__)

IMAP_FACTORY = imaplib.IMAP4_SSL          # tests substitute a fake
HTTP = None                               # tests substitute a fake requests module
AI_CLIENT = None                          # tests substitute a fake Anthropic client

_HEADER_FIELDS = ('FROM TO CC DATE MESSAGE-ID LIST-UNSUBSCRIBE LIST-ID AUTO-SUBMITTED PRECEDENCE '
                  'AUTHENTICATION-RESULTS')
_SENT_NAMES = ('sent', 'sent items', 'sent mail', 'sent messages', '[gmail]/sent mail', 'inbox.sent')
_JUNK_NAMES = ('spam', 'junk', 'junk e-mail', 'junk email', 'bulk mail', '[gmail]/spam', 'inbox.spam',
               'inbox.junk')
_MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
IMAP_PRESETS = {
    'yahoo.com': ('imap.mail.yahoo.com', 993), 'ymail.com': ('imap.mail.yahoo.com', 993),
    'icloud.com': ('imap.mail.me.com', 993), 'me.com': ('imap.mail.me.com', 993),
    'mac.com': ('imap.mail.me.com', 993), 'zoho.com': ('imap.zoho.com', 993),
    'aol.com': ('imap.aol.com', 993), 'fastmail.com': ('imap.fastmail.com', 993),
    'gmx.com': ('imap.gmx.com', 993),
}
GMAIL_DOMAINS = ('gmail.com', 'googlemail.com')
GOOGLE_SCOPES = ('openid email https://www.googleapis.com/auth/contacts.readonly '
                 'https://www.googleapis.com/auth/contacts.other.readonly')


class SyncError(Exception):
    """A user-facing connection or sync problem."""


class ReauthNeeded(SyncError):
    pass


def _http():
    if HTTP is not None:
        return HTTP
    import requests
    return requests


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

def _max_accounts_check(user_id: str) -> None:
    from app.models.mail_crm import EmailAccount
    from app.services import mail_feature
    n = EmailAccount.query.filter(EmailAccount.user_id == user_id,
                                  EmailAccount.status != EmailAccount.STATUS_DISCONNECTED).count()
    cap = mail_feature.setting('mail_max_accounts_per_user')
    if n >= cap:
        raise SyncError('You can connect up to %d email accounts.' % cap)


def connect_imap(user_id: str, address: str, password: str, host: str = '', port: int = 993):
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.mail_crm import EmailAccount
    from app.services import mail_feature
    from app.services.token_crypto import encrypt_token
    mail_feature.require(user_id, 'imap')
    address = (address or '').strip().lower()
    if not re.match(r'^[^@\s]+@[^@\s]+\.[a-z]{2,}$', address):
        raise SyncError('Enter the email address of the mailbox.')
    domain = address.rsplit('@', 1)[-1]
    if domain in GMAIL_DOMAINS:
        raise SyncError('For Gmail, use "Connect Gmail" instead. Reading Gmail messages needs Google\'s '
                        'security review, which is still in progress.')
    if not password:
        raise SyncError('Enter an app password for this mailbox.')
    host = (host or '').strip() or IMAP_PRESETS.get(domain, ('', 993))[0]
    port = int(port or IMAP_PRESETS.get(domain, ('', 993))[1] or 993)
    if not host:
        raise SyncError('Enter your provider\'s IMAP server (for example imap.yourdomain.com).')
    _max_accounts_check(user_id)
    if EmailAccount.query.filter(EmailAccount.user_id == user_id, EmailAccount.address == address,
                                 EmailAccount.provider == 'imap',
                                 EmailAccount.status != EmailAccount.STATUS_DISCONNECTED).first():
        raise SyncError('That mailbox is already connected.')
    conn = _imap_login(host, port, address, password)      # fail now, not at the first sync
    try:
        conn.logout()
    except Exception:
        pass
    acc = EmailAccount(user_id=user_id, provider='imap', address=address, access_level='mail_read',
                       imap_host=host, imap_port=port, token_enc=encrypt_token(password),
                       lookback_months=mail_feature.setting('mail_lookback_months'))
    db.session.add(acc)
    AuditLog.log('email_account_connected', user_id=user_id, metadata={'provider': 'imap', 'host': host})
    db.session.commit()
    return acc


def _imap_login(host, port, address, password):
    try:
        conn = IMAP_FACTORY(host, port)
        conn.login(address, password)
        return conn
    except imaplib.IMAP4.error as exc:
        raise ReauthNeeded('The mailbox rejected the sign-in. Check the address and use an app '
                           'password (not your normal password).') from exc
    except OSError as exc:
        raise SyncError('Could not reach %s:%s.' % (host, port)) from exc


def gmail_auth_url(user_id: str, redirect_uri: str, state: str) -> str:
    from flask import current_app
    from urllib.parse import urlencode
    from app.services import mail_feature
    mail_feature.require(user_id, 'gmail')
    if not current_app.config.get('GOOGLE_CLIENT_ID'):
        raise SyncError('Gmail connection is not configured on this server.')
    _max_accounts_check(user_id)
    return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
        'client_id': current_app.config['GOOGLE_CLIENT_ID'], 'redirect_uri': redirect_uri,
        'response_type': 'code', 'scope': GOOGLE_SCOPES, 'access_type': 'offline',
        'prompt': 'consent', 'include_granted_scopes': 'true', 'state': state})


def gmail_complete(user_id: str, code: str, redirect_uri: str):
    from flask import current_app
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.mail_crm import EmailAccount
    from app.services import mail_feature
    from app.services.token_crypto import encrypt_token
    mail_feature.require(user_id, 'gmail')
    r = _http().post('https://oauth2.googleapis.com/token', data={
        'code': code, 'client_id': current_app.config['GOOGLE_CLIENT_ID'],
        'client_secret': current_app.config['GOOGLE_CLIENT_SECRET'], 'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code'}, timeout=15)
    data = r.json() if r is not None else {}
    if not data.get('refresh_token'):
        raise SyncError('Google did not grant access. Try again and allow contacts access.')
    granted = set((data.get('scope') or '').split())
    if 'https://www.googleapis.com/auth/contacts.other.readonly' not in granted:
        raise SyncError('Allow "See and download contact info automatically saved" to import contacts.')
    info = _http().get('https://openidconnect.googleapis.com/v1/userinfo',
                       headers={'Authorization': 'Bearer ' + data['access_token']}, timeout=15).json()
    address = (info.get('email') or '').lower()
    _max_accounts_check(user_id)
    acc = EmailAccount.query.filter_by(user_id=user_id, provider='gmail', address=address).first()
    if acc is None:
        acc = EmailAccount(user_id=user_id, provider='gmail', address=address or 'gmail',
                           access_level='contacts', lookback_months=mail_feature.setting('mail_lookback_months'))
        db.session.add(acc)
    acc.token_enc = encrypt_token(data['refresh_token'])
    acc.status, acc.last_error = EmailAccount.STATUS_ACTIVE, None
    AuditLog.log('email_account_connected', user_id=user_id, metadata={'provider': 'gmail'})
    db.session.commit()
    return acc


def owned_account(user_id: str, account_id: str):
    from app.models.mail_crm import EmailAccount
    acc = EmailAccount.query.filter_by(id=account_id, user_id=user_id).first()
    if acc is None or acc.status == EmailAccount.STATUS_DISCONNECTED:
        raise SyncError('Account not found.')
    return acc


def set_paused(user_id: str, account_id: str, paused: bool):
    from app.extensions import db
    from app.models.mail_crm import EmailAccount
    from app.services import mail_feature
    acc = owned_account(user_id, account_id)
    if acc.status == EmailAccount.STATUS_ADMIN_PAUSED:
        raise SyncError('An administrator paused this account.')
    if not paused:
        mail_feature.require(user_id, acc.provider)
    acc.status = EmailAccount.STATUS_PAUSED if paused else EmailAccount.STATUS_ACTIVE
    db.session.commit()
    return acc


def disconnect(user_id: str, account_id: str, delete_contacts: bool) -> dict:
    """Revoke and delete the credentials now. Optionally delete contacts that
    came only from this account (contacts linked to another account, or
    already contacted, are kept)."""
    from app.extensions import db
    from app.models.audit_log import AuditLog
    from app.models.contact import Contact, ContactActivity
    from app.models.mail_crm import (ContactAccountLink, ContactFieldSource, EmailAccount, ReviewItem,
                                     SenderSignal)
    acc = owned_account(user_id, account_id)
    if acc.provider == 'gmail' and acc.token_enc:
        try:
            from app.services.token_crypto import decrypt_token
            _http().post('https://oauth2.googleapis.com/revoke', params={'token': decrypt_token(acc.token_enc)},
                         timeout=10)
        except Exception as exc:
            logger.warning('Google token revoke failed (deleting locally anyway): %s', exc)
    acc.token_enc = None
    acc.status = EmailAccount.STATUS_DISCONNECTED
    acc.cursor = None
    deleted = 0
    if delete_contacts:
        for link in ContactAccountLink.query.filter_by(account_id=acc.id).all():
            others = ContactAccountLink.query.filter(ContactAccountLink.contact_id == link.contact_id,
                                                     ContactAccountLink.account_id != acc.id).count()
            c = Contact.query.get(link.contact_id)
            if others or c is None or c.source != 'email_import' or (c.outreach_count or 0) > 0:
                continue
            if ContactActivity.query.filter_by(contact_id=c.id).count():
                continue
            ContactFieldSource.query.filter_by(contact_id=c.id).delete()
            db.session.delete(c)
            deleted += 1
    ContactAccountLink.query.filter_by(account_id=acc.id).delete()
    SenderSignal.query.filter_by(account_id=acc.id).delete()
    ReviewItem.query.filter_by(account_id=acc.id, status=ReviewItem.STATUS_PENDING).delete()
    AuditLog.log('email_account_disconnected', user_id=user_id,
                 metadata={'provider': acc.provider, 'contacts_deleted': deleted})
    db.session.commit()
    return {'contacts_deleted': deleted}


def add_exclusion(user_id: str, account_id: str, kind: str, value: str):
    from app.extensions import db
    from app.models.mail_crm import AccountExclusion
    acc = owned_account(user_id, account_id)
    value = (value or '').strip().lower().lstrip('@')
    if kind not in AccountExclusion.KINDS or not value:
        raise SyncError('Choose a domain, label or folder to exclude.')
    if not AccountExclusion.query.filter_by(account_id=acc.id, kind=kind, value=value).first():
        db.session.add(AccountExclusion(account_id=acc.id, kind=kind, value=value[:255]))
    db.session.commit()
    return acc


def remove_exclusion(user_id: str, account_id: str, exclusion_id: str):
    from app.extensions import db
    from app.models.mail_crm import AccountExclusion
    acc = owned_account(user_id, account_id)
    AccountExclusion.query.filter_by(id=exclusion_id, account_id=acc.id).delete()
    db.session.commit()
    return acc


# ---------------------------------------------------------------------------
# IMAP adapter
# ---------------------------------------------------------------------------

def _imap_date(d: datetime) -> str:
    return '%02d-%s-%d' % (d.day, _MONTHS[d.month - 1], d.year)


def _folders(conn) -> dict:
    """{'sent': name|None, 'junk': [names]} from LIST (special-use flags first)."""
    typ, data = conn.list()
    sent, junk = None, []
    for raw in data or []:
        line = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw)
        m = re.match(r'\((?P<flags>[^)]*)\)\s+"?(?P<delim>[^"\s]*)"?\s+(?P<name>.+)$', line)
        if not m:
            continue
        name = m.group('name').strip().strip('"')
        flags = m.group('flags').lower()
        if '\\sent' in flags or (sent is None and name.lower() in _SENT_NAMES):
            sent = name if '\\sent' in flags or sent is None else sent
        if '\\junk' in flags or name.lower() in _JUNK_NAMES:
            junk.append(name)
    return {'sent': sent, 'junk': junk}


def _select(conn, folder: str) -> int | None:
    typ, _ = conn.select('"%s"' % folder if ' ' in folder else folder, readonly=True)
    if typ != 'OK':
        return None
    try:
        typ, data = conn.response('UIDVALIDITY')
        return int(data[0]) if data and data[0] else None
    except Exception:
        return None


def _search(conn, since: datetime, below: int | None = None, above: int | None = None) -> list:
    criteria = ['SINCE', _imap_date(since)]
    if below is not None:
        if below <= 1:
            return []
        criteria += ['UID', '1:%d' % (below - 1)]
    if above is not None:
        criteria += ['UID', '%d:*' % (above + 1)]
    typ, data = conn.uid('SEARCH', None, *criteria)
    if typ != 'OK' or not data or not data[0]:
        return []
    uids = sorted({int(x) for x in data[0].split()}, reverse=True)
    if above is not None:
        uids = [u for u in uids if u > above]       # '*' can echo the highest existing UID
    return uids


def _fetch_headers(conn, uids: list) -> list:
    if not uids:
        return []
    typ, data = conn.uid('FETCH', ','.join(str(u) for u in uids),
                         '(BODY.PEEK[HEADER.FIELDS (%s)])' % _HEADER_FIELDS)
    out = []
    for part in data or []:
        if not isinstance(part, tuple):
            continue
        m = re.search(rb'UID (\d+)', part[0])
        if m:
            out.append((int(m.group(1)), _email.message_from_bytes(part[1])))
    return out


def _fetch_text(conn, uid: int) -> str:
    """The message's text, from the first 64 KB only. Used in memory, never stored."""
    typ, data = conn.uid('FETCH', str(uid), '(BODY.PEEK[]<0.65536>)')
    for part in data or []:
        if isinstance(part, tuple):
            msg = _email.message_from_bytes(part[1], policy=_email.policy.default)
            try:
                body = msg.get_body(preferencelist=('plain', 'html'))
                text = body.get_content() if body else ''
            except Exception:
                text = ''
            if body is not None and body.get_content_type() == 'text/html':
                text = re.sub(r'<br\s*/?>|</p>|</div>', '\n', text, flags=re.I)
                text = re.sub(r'<[^>]+>', ' ', text)
            return text or ''
    return ''


def _addr_list(msg, header: str) -> list:
    return [(n.strip(), a.strip().lower()) for n, a in getaddresses(msg.get_all(header, []) or []) if a and '@' in a]


def _inbound_obs(uid, msg) -> dict:
    name, addr = parseaddr(msg.get('From', ''))
    auth = (msg.get('Authentication-Results') or '').lower()
    auto = (msg.get('Auto-Submitted') or '').strip().lower()
    prec = (msg.get('Precedence') or '').strip().lower()
    return {'kind': 'inbound', 'uid': uid, 'name': name.strip(), 'email': (addr or '').strip().lower(),
            'message_id': (msg.get('Message-ID') or '').strip(),
            'recipients': len(_addr_list(msg, 'To')) + len(_addr_list(msg, 'Cc')),
            'list_headers': bool(msg.get('List-Unsubscribe') or msg.get('List-Id')),
            'automated_headers': bool((auto and auto != 'no') or prec in ('bulk', 'list', 'junk')),
            'auth_failed': 'dmarc=fail' in auth or ('spf=fail' in auth and 'dkim=pass' not in auth)}


class ImapSource:
    """Yields observations for one batch and advances the cursor."""

    def __init__(self, account, password: str, batch: int, excluded_folders: set):
        self.account, self.batch = account, batch
        self.conn = _imap_login(account.imap_host, account.imap_port or 993, account.address, password)
        self.excluded = excluded_folders

    def close(self):
        try:
            self.conn.logout()
        except Exception:
            pass

    def text_for(self, folder: str, uid: int) -> str:
        _select(self.conn, folder)
        return _fetch_text(self.conn, uid)

    def next_batch(self, cursor: dict) -> tuple:
        """(observations, cursor, initial_done)."""
        cursor = dict(cursor or {})
        if 'folders' not in cursor:
            f = _folders(self.conn)
            cursor['folders'] = {'sent': f['sent'], 'inbox': 'INBOX'}
            cursor['phase'] = 'sent' if f['sent'] else 'inbox'
            cursor['below'], cursor['max_seen'], cursor['uidvalidity'] = {}, {}, {}
        since = datetime.utcnow() - timedelta(days=30 * max(1, self.account.lookback_months or 24))
        obs = []
        if cursor['phase'] in ('sent', 'inbox'):          # first sync: newest first, Sent first
            key = cursor['phase']
            folder = cursor['folders'].get(key)
            if folder and folder.lower() not in self.excluded and not cursor.get('stop_' + key):
                obs = self._read(folder, key, cursor, since, initial=True)
            if not obs:
                cursor['phase'] = 'inbox' if key == 'sent' else 'idle'
                if cursor['phase'] == 'idle':
                    return obs, cursor, True
            return obs, cursor, False
        for key in ('sent', 'inbox'):                       # ongoing: only new mail
            folder = cursor['folders'].get(key)
            if folder and folder.lower() not in self.excluded:
                obs += self._read(folder, key, cursor, since, initial=False)
        return obs, cursor, True

    def _read(self, folder, key, cursor, since, initial: bool) -> list:
        uv = _select(self.conn, folder)
        if uv is not None and cursor['uidvalidity'].get(key) not in (None, uv):
            cursor['below'].pop(key, None)                  # mailbox was rebuilt: start over
            cursor['max_seen'].pop(key, None)
        cursor['uidvalidity'][key] = uv
        if initial:
            uids = _search(self.conn, since, below=cursor['below'].get(key))[:self.batch]
        else:
            uids = _search(self.conn, since, above=cursor['max_seen'].get(key, 0))[-self.batch:]
        if not uids:
            return []
        if initial:
            cursor['below'][key] = min(uids)
        cursor['max_seen'][key] = max([cursor['max_seen'].get(key, 0)] + uids)
        out = []
        for uid, msg in _fetch_headers(self.conn, uids):
            if key == 'sent':
                out.append({'kind': 'sent', 'uid': uid, 'message_id': (msg.get('Message-ID') or '').strip(),
                            'to': _addr_list(msg, 'To'), 'cc': _addr_list(msg, 'Cc')})
            else:
                o = _inbound_obs(uid, msg)
                o['folder'] = folder
                out.append(o)
        return out


# ---------------------------------------------------------------------------
# Gmail step 1 adapter (People API; no mail is read)
# ---------------------------------------------------------------------------

class GmailContactsSource:
    def __init__(self, account, refresh_token: str):
        from flask import current_app
        r = _http().post('https://oauth2.googleapis.com/token', data={
            'client_id': current_app.config.get('GOOGLE_CLIENT_ID'),
            'client_secret': current_app.config.get('GOOGLE_CLIENT_SECRET'),
            'refresh_token': refresh_token, 'grant_type': 'refresh_token'}, timeout=15)
        data = r.json() if r is not None else {}
        if not data.get('access_token'):
            raise ReauthNeeded('Google access expired or was revoked. Reconnect Gmail.')
        self.token = data['access_token']

    def close(self):
        pass

    def _get(self, url, params):
        r = _http().get(url, params=params, headers={'Authorization': 'Bearer ' + self.token}, timeout=20)
        if getattr(r, 'status_code', 200) == 410:
            return None                                     # sync token expired: full resync
        if getattr(r, 'status_code', 200) in (401, 403):
            raise ReauthNeeded('Google access expired or was revoked. Reconnect Gmail.')
        return r.json()

    def next_batch(self, cursor: dict) -> tuple:
        cursor = dict(cursor or {})
        phase = cursor.get('phase') or 'other'
        if phase == 'idle':
            phase = cursor['phase'] = 'other'               # ongoing sync: re-walk with sync tokens
        other = phase == 'other'
        url = ('https://people.googleapis.com/v1/otherContacts' if other
               else 'https://people.googleapis.com/v1/people/me/connections')
        params = {'pageSize': 1000, 'requestSyncToken': 'true'}
        if other:
            params['readMask'] = 'names,emailAddresses,phoneNumbers'
        else:
            params['personFields'] = 'names,emailAddresses,phoneNumbers,organizations,addresses,urls'
        token_key = 'sync_' + phase
        if cursor.get('page'):
            params['pageToken'] = cursor['page']
        elif cursor.get(token_key):
            params['syncToken'] = cursor[token_key]
        data = self._get(url, params)
        if data is None:
            cursor.pop(token_key, None)
            cursor.pop('page', None)
            return [], cursor, False
        people = data.get('otherContacts' if other else 'connections') or []
        obs = [self._person(p, address_book=not other) for p in people]
        if data.get('nextPageToken'):
            cursor['page'] = data['nextPageToken']
            return [o for o in obs if o], cursor, False
        cursor.pop('page', None)
        if data.get('nextSyncToken'):
            cursor[token_key] = data['nextSyncToken']
        cursor['phase'] = 'connections' if other else 'idle'
        return [o for o in obs if o], cursor, not other

    @staticmethod
    def _person(p: dict, address_book: bool) -> dict | None:
        emails = [e.get('value', '').strip().lower() for e in p.get('emailAddresses') or [] if e.get('value')]
        if not emails or (p.get('metadata') or {}).get('deleted'):
            return None
        names = (p.get('names') or [{}])[0]
        org = (p.get('organizations') or [{}])[0]
        addr = (p.get('addresses') or [{}])[0]
        phones = [ph.get('value') for ph in p.get('phoneNumbers') or [] if ph.get('value')]
        urls = [u.get('value') for u in p.get('urls') or [] if 'linkedin.com/in/' in (u.get('value') or '')]
        return {'kind': 'person', 'email': emails[0], 'name': names.get('displayName') or '',
                'first_name': names.get('givenName'), 'last_name': names.get('familyName'),
                'company': org.get('name'), 'job_title': org.get('title'),
                'department': org.get('department'), 'city': addr.get('city'),
                'region': addr.get('region'), 'country': addr.get('country'),
                'phones': phones, 'linkedin_url': urls[0] if urls else None,
                'message_id': p.get('resourceName'), 'address_book': address_book,
                'interacted': not address_book}


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

def _source_for(account):
    from app.models.mail_crm import AccountExclusion
    from app.services import mail_feature
    from app.services.token_crypto import decrypt_token
    secret = decrypt_token(account.token_enc) if account.token_enc else None
    if not secret:
        raise ReauthNeeded('Credentials are missing. Reconnect this account.')
    if account.provider == 'imap':
        folders = {e.value for e in AccountExclusion.query.filter_by(account_id=account.id, kind='folder').all()}
        return ImapSource(account, secret, mail_feature.setting('mail_batch_messages'), folders)
    if account.provider == 'gmail':
        return GmailContactsSource(account, secret)
    raise SyncError('Unsupported provider.')


def run_batch(account) -> dict:
    """One batch for one account. Returns the SyncRun as a dict."""
    from app.extensions import db
    from app.models.mail_crm import EmailAccount, SyncRun
    from app.services import mail_feature
    run = SyncRun(account_id=account.id)
    db.session.add(run)
    db.session.flush()
    if not mail_feature.is_enabled(account.user_id, account.provider):
        run.error, run.finished_at = 'Feature off', datetime.utcnow()
        db.session.commit()
        return run.to_dict()
    source = None
    try:
        source = _source_for(account)
        obs, cursor, initial_done = source.next_batch(account.cursor)
        _process(account, run, obs, source)
        if cursor.get('phase') == 'inbox' and not account.initial_sync_done:
            # Optimal stopping: quit the backfill once new real contacts per
            # 1,000 messages fall below the admin threshold.
            cursor['scanned'] = cursor.get('scanned', 0) + run.messages_scanned
            cursor['found'] = cursor.get('found', 0) + run.imported + run.queued
            if cursor['scanned'] >= mail_feature.setting('mail_backfill_min_scanned') and                     cursor['found'] * 1000 < mail_feature.setting('mail_backfill_min_yield') * cursor['scanned']:
                cursor['stop_inbox'] = True
        account.cursor = cursor
        if initial_done:
            account.initial_sync_done = True
        account.last_sync_at, account.last_error = datetime.utcnow(), None
    except ReauthNeeded as exc:
        db.session.rollback()
        account = EmailAccount.query.get(account.id)
        run = SyncRun(account_id=account.id, error=str(exc)[:300])
        db.session.add(run)
        account.status, account.last_error = EmailAccount.STATUS_REAUTH, str(exc)[:300]
        _notify_reauth(account)
    except Exception as exc:
        logger.error('mail sync failed account=%s: %s', account.id, exc, exc_info=True)
        db.session.rollback()
        account = EmailAccount.query.get(account.id)
        run = SyncRun(account_id=account.id, error=str(exc)[:300])
        db.session.add(run)
        account.last_error = 'Sync failed; it will retry: %s' % str(exc)[:200]
    finally:
        if source is not None:
            source.close()
    run.finished_at = datetime.utcnow()
    db.session.commit()
    return run.to_dict()


def _notify_reauth(account) -> None:
    try:
        from app.services.notification_service import send_notification
        send_notification(user_id=account.user_id, notification_type='email_account_reauth',
                          title='Reconnect %s' % account.address,
                          body='Email import paused this account because its sign-in stopped working.',
                          cta_url='/settings/email-import', cta_label='Reconnect')
    except Exception:
        pass


def run_due(budget_seconds: float | None = None) -> dict:
    """Cron entry point: batches for active accounts, oldest-synced first,
    until the time budget runs out."""
    from app.models.mail_crm import EmailAccount
    from app.services import mail_feature
    if mail_feature.mode(mail_feature.FEATURE) == 'off':
        return {'batches': 0}
    budget = budget_seconds if budget_seconds is not None else mail_feature.setting('mail_sync_seconds')
    deadline = time.monotonic() + budget
    accounts = EmailAccount.query.filter_by(status=EmailAccount.STATUS_ACTIVE).order_by(
        EmailAccount.last_sync_at.is_(None).desc(), EmailAccount.last_sync_at.asc()).all()
    ids = [a.id for a in accounts]
    n = 0
    for acc_id in ids:
        if time.monotonic() >= deadline:
            break
        acc = EmailAccount.query.get(acc_id)
        if acc is None or acc.status != EmailAccount.STATUS_ACTIVE:
            continue
        if acc.initial_sync_done and acc.last_sync_at and \
                datetime.utcnow() - acc.last_sync_at < timedelta(minutes=15):
            continue                                       # ongoing sync every 15 minutes
        run_batch(acc)
        n += 1
    return {'batches': n}


def _process(account, run, obs: list, source) -> None:
    """Fold a batch of observations into sender facts, then score and decide."""
    from app.extensions import db
    from app.models.mail_crm import AccountExclusion, EmailAccount, ReviewItem, SenderSignal, email_hash
    from app.services import mail_contacts, mail_extract, mail_feature, mail_scoring

    S = mail_feature.setting
    L = mail_feature.setting_list
    patterns, marketing = L('mail_automated_patterns'), L('mail_marketing_domains')
    freemail, prefixes = L('mail_freemail_domains'), L('mail_role_prefixes')
    excluded = {e.value for e in AccountExclusion.query.filter_by(account_id=account.id, kind='domain').all()}
    own = {a.address for a in EmailAccount.query.filter_by(user_id=account.user_id).all()} | {account.address}
    user_domains = {a.rsplit('@', 1)[-1] for a in own if '@' in a}
    ai_budget = S('mail_claude_per_sync')

    touched = {}       # email -> {'name', 'fields', 'sig': lines|None, 'message_id', 'delta': {...}}

    def entry(email, name=''):
        e = touched.setdefault(email, {'name': '', 'delta': {}, 'person': None, 'sig_ref': None,
                                       'message_id': None})
        if name and not e['name']:
            e['name'] = name
        return e

    def skip_address(email):
        d = email.rsplit('@', 1)[-1] if '@' in email else ''
        return (not email or email in own or d in excluded or any(d.endswith('.' + x) for x in excluded))

    for o in obs:
        run.messages_scanned += 1
        if o['kind'] == 'sent':
            for key, people in (('sent', o.get('to') or []), ('cc', o.get('cc') or [])):
                for name, addr in people:
                    if skip_address(addr):
                        continue
                    e = entry(addr, name)
                    e['delta'][key] = e['delta'].get(key, 0) + 1
        elif o['kind'] == 'inbound':
            addr = o['email']
            if skip_address(addr):
                continue
            e = entry(addr, o['name'])
            d = e['delta']
            d['recv'] = d.get('recv', 0) + 1
            d['maxrcpt'] = max(d.get('maxrcpt', 0), o['recipients'])
            d['list'] = d.get('list') or o['list_headers']
            d['auto'] = d.get('auto') or o['automated_headers']
            d['authfail'] = d.get('authfail') or o['auth_failed']
            if e['sig_ref'] is None:                         # newest message from this sender
                e['sig_ref'] = (o.get('folder'), o['uid'])
                e['message_id'] = o['message_id']
        elif o['kind'] == 'person':
            if skip_address(o['email']):
                continue
            e = entry(o['email'], o['name'])
            e['person'] = o
            e['message_id'] = o['message_id']
            e['delta']['book'] = e['delta'].get('book') or o['address_book']
            e['delta']['interacted'] = e['delta'].get('interacted') or o['interacted']

    for addr, e in touched.items():
        h = email_hash(addr)
        sig = SenderSignal.query.filter_by(account_id=account.id, email_hash=h).first()
        if sig is None:
            sig = SenderSignal(account_id=account.id, email_hash=h, decision=SenderSignal.DECISION_PENDING)
            db.session.add(sig)
        facts = sig.facts
        for k, v in e['delta'].items():
            if isinstance(v, bool):
                facts[k] = bool(facts.get(k)) or v
            elif k == 'maxrcpt':
                facts[k] = max(facts.get(k, 0), v)
            else:
                facts[k] = facts.get(k, 0) + v

        sf = mail_scoring.SenderFacts(
            email=addr, display_name=e['name'], automated_headers=bool(facts.get('auto')),
            list_headers=bool(facts.get('list')), user_sent_to=facts.get('sent', 0) > 0,
            user_cc=facts.get('cc', 0) > 0, received_count=facts.get('recv', 0),
            max_recipients=facts.get('maxrcpt', 0), in_address_book=bool(facts.get('book')),
            provider_interacted=bool(facts.get('interacted')), auth_failed=bool(facts.get('authfail')),
            signature_person=bool(facts.get('sig_person')))
        pre = mail_scoring.score_sender(sf, automated_patterns=patterns, marketing_domains=marketing,
                                        role_prefixes=prefixes, import_at=S('mail_score_import'),
                                        review_at=S('mail_score_review'))

        # Signature: newest message only, only for senders that could be imported,
        # and Claude only when the signature changed (caching lens).
        fields = _header_fields(addr, e['name'], freemail)
        if e['person']:
            fields.update(_person_fields(e['person']))
        # Only a message newer than the one already read: the backfill walks
        # newest-first, so older mail never re-reads a signature.
        if (pre.decision != 'skip' or pre.score >= S('mail_score_review') - 15) and e['sig_ref'] \
                and account.access_level == 'mail_read' and e['sig_ref'][1] > facts.get('sig_uid', 0):
            folder, uid = e['sig_ref']
            facts['sig_uid'] = uid
            try:
                lines = mail_extract.signature_window(source.text_for(folder, uid), S('mail_signature_lines'))
            except Exception as exc:
                logger.debug('signature fetch failed: %s', exc)
                lines = []
            if lines and mail_extract.looks_like_signature(lines):
                rules = mail_extract.rule_fields(lines)
                if rules.get('phones'):
                    p = rules['phones'][0]
                    fields['phone'] = (p['e164'], 'signature', mail_extract.CONF['signature_rule'])
                if rules.get('linkedin_url'):
                    fields['linkedin_url'] = (rules['linkedin_url'], 'signature', mail_extract.CONF['signature_rule'])
                sh = mail_extract.signature_hash(lines)
                if sh != sig.signature_hash and ai_budget > 0:
                    ai_budget -= 1
                    ai = mail_extract.ai_fields(lines, addr, client=AI_CLIENT)
                    sig.signature_hash = sh
                    c = mail_extract.CONF['signature_ai']
                    for src_key, field in (('first_name', 'first_name'), ('last_name', 'last_name'),
                                           ('job_title', 'job_title'), ('company', 'company_name'),
                                           ('city', 'city'), ('region', 'state_region'), ('country', 'country')):
                        if ai.get(src_key):
                            fields[field] = (ai[src_key], 'signature', c)
                    if ai.get('first_name') or ai.get('job_title'):
                        facts['sig_person'] = True
                del lines                                    # the signature text is discarded here
        _add_inferences(fields, addr, user_domains, freemail)

        sf.signature_person = bool(facts.get('sig_person'))
        v = mail_scoring.score_sender(sf, automated_patterns=patterns, marketing_domains=marketing,
                                      role_prefixes=prefixes, import_at=S('mail_score_import'),
                                      review_at=S('mail_score_review'))
        run.senders_evaluated += 1
        sig.facts, sig.score, sig.tier = facts, v.score, v.tier
        sig.reasons_json, sig.evaluated_at = _json(v.reasons), datetime.utcnow()
        decided_before = sig.decision
        if decided_before == SenderSignal.DECISION_REVIEW:
            _merge_review_fields(account, h, fields)
        if decided_before == SenderSignal.DECISION_IMPORT or v.decision == 'import':
            c = mail_contacts.upsert_sender(account.user_id, account, {
                'email': addr, 'fields': fields, 'tier': v.tier, 'message_id': e['message_id'],
                'sent_count': facts.get('sent', 0) + facts.get('cc', 0),
                'received_count': facts.get('recv', 0)}, prefixes)
            if c is not None:
                sig.decision = SenderSignal.DECISION_IMPORT
                if decided_before != SenderSignal.DECISION_IMPORT:
                    run.imported += 1
                _resolve_review(account, h, c.id)
            else:
                sig.decision = SenderSignal.DECISION_SKIP
                run.skipped += 1
        elif v.decision == 'review':
            if decided_before == SenderSignal.DECISION_SKIP and _user_rejected(account, h):
                run.skipped += 1                             # the user already said no
                continue
            sig.decision = SenderSignal.DECISION_REVIEW
            if _queue_review(account, h, addr, e['name'], fields, v, e['message_id'], facts):
                run.queued += 1
        else:
            sig.decision = SenderSignal.DECISION_SKIP
            run.skipped += 1
    db.session.flush()


def _json(v):
    import json
    return json.dumps(v)


def _header_fields(addr, display, freemail) -> dict:
    from app.services import mail_extract
    first, last, src, conf = mail_extract.split_name(display, addr)
    fields = {}
    if first:
        fields['first_name'] = (first, src, conf)
    if last:
        fields['last_name'] = (last, src, conf)
    company = mail_extract.company_from_domain(addr.rsplit('@', 1)[-1], freemail)
    if company:
        fields['company_name'] = (company, 'header', mail_extract.CONF['domain'])
    return fields


def _person_fields(p: dict) -> dict:
    from app.services import mail_extract
    c = mail_extract.CONF['address_book']
    out = {}
    for key, field in (('first_name', 'first_name'), ('last_name', 'last_name'), ('company', 'company_name'),
                       ('job_title', 'job_title'), ('department', 'department'), ('city', 'city'),
                       ('region', 'state_region'), ('country', 'country'), ('linkedin_url', 'linkedin_url')):
        if p.get(key):
            out[field] = (p[key], 'address_book', c)
    for raw in p.get('phones') or []:
        e164 = mail_extract.to_e164(raw)
        if e164:
            out['phone'] = (e164, 'address_book', c)
            break
    return out


def _add_inferences(fields: dict, addr: str, user_domains: set, freemail: list) -> None:
    from app.services import mail_extract
    c = mail_extract.CONF['inferred']
    title = (fields.get('job_title') or (None,))[0]
    if title:
        if 'department' not in fields:
            dept = mail_extract.infer_department(title)
            if dept:
                fields['department'] = (dept, 'inferred', c)
        exp = mail_extract.infer_expertise(title)
        if exp:
            fields['expertise'] = (exp, 'inferred', c)
    role = mail_extract.infer_role(title or '', addr.rsplit('@', 1)[-1], user_domains, freemail)
    if role:
        fields['role_to_user'] = (role, 'inferred', c)


def _queue_review(account, h, addr, name, fields, verdict, message_id, facts) -> bool:
    import json
    from app.extensions import db
    from app.models.mail_crm import ReviewItem
    item = ReviewItem.query.filter_by(account_id=account.id, email_hash=h,
                                      status=ReviewItem.STATUS_PENDING).first()
    payload = {'email': addr, 'name': name, 'score': verdict.score, 'reasons': verdict.reasons,
               'tier': verdict.tier, 'message_id': message_id, 'received_count': facts.get('recv', 0),
               'fields': {k: v[0] for k, v in fields.items()},
               'fields_full': {k: list(v) for k, v in fields.items()}}
    if item is None:
        db.session.add(ReviewItem(user_id=account.user_id, account_id=account.id, email_hash=h,
                                  kind=ReviewItem.KIND_MID_SCORE, payload_json=json.dumps(payload)))
        return True
    item.payload_json = json.dumps(payload)
    return False


def _merge_review_fields(account, h, fields: dict) -> None:
    """A sender waiting in review already had its signature read; keep those
    values (higher confidence wins per field) instead of reading it again."""
    from app.models.mail_crm import ReviewItem
    item = ReviewItem.query.filter_by(account_id=account.id, email_hash=h,
                                      status=ReviewItem.STATUS_PENDING).first()
    if item is None:
        return
    for k, v in (item.payload.get('fields_full') or {}).items():
        v = tuple(v)
        if k not in fields or v[2] > fields[k][2]:
            fields[k] = v


def _resolve_review(account, h, contact_id) -> None:
    from app.models.mail_crm import ReviewItem
    for item in ReviewItem.query.filter_by(account_id=account.id, email_hash=h,
                                           status=ReviewItem.STATUS_PENDING).all():
        item.status, item.contact_id, item.decided_at = ReviewItem.STATUS_APPROVED, contact_id, datetime.utcnow()


def _user_rejected(account, h) -> bool:
    from app.models.mail_crm import ReviewItem
    return ReviewItem.query.filter_by(account_id=account.id, email_hash=h,
                                      status=ReviewItem.STATUS_REJECTED).first() is not None
