# PRD-SIM-MAIL-001: Email-to-CRM, phase 1

Source: `docs/PRD-SIM-MAIL-001 Email-to-CRM Contact Extraction.docx`.
Branch: `feat/mail-crm`, cut from `feat/bio-quick-wins` so deploys keep stacking.

## Owner decisions (2026-11-01)
1. **Scope:** phase 1 plus the admin switch. This covers connecting accounts, spam filtering and scoring, extraction of the 12 fields with their sources, a basic review queue for mid-score senders, and the global, per-user and per-provider switch that marks contacts Do Not Contact when the feature is turned off. Enrichment is not included.
2. **Providers:** IMAP (app password) and Gmail step 1 (People API, contacts only). Microsoft 365 is not built, and its switch defaults to off.
3. **Subject lines:** never read. The role is inferred from the job title and the domain only, and is labelled Inferred.

## How it's built
| Piece | Where |
|---|---|
| Tables (additive; `contacts` extended, not replaced) | `app/models/mail_crm.py`, `sql/060_mail_crm.sql` |
| Switch, settings, DNC ledger | `app/services/mail_feature.py` |
| Real-correspondent score | `app/services/mail_scoring.py` (pure) |
| Names, signature window, phones, LinkedIn, Claude, inferences | `app/services/mail_extract.py` |
| Contact upsert, field sources, locking, readiness, review decisions | `app/services/mail_contacts.py` |
| Connections, IMAP and Gmail adapters, resumable batch engine | `app/services/mail_sync.py` |
| Routes and pages | `app/blueprints/mail/`, `templates/mail/settings.html` (`/settings/email-import`), `templates/mail/admin.html` (`/admin/email-crm`) |
| Cron | `cron_mail.py`, every minute, with a lock file and a time budget (`mail_sync_seconds`) |
| Tests | `test_mail_crm.py` (fake IMAP, Google and Claude; no network) |

## Rules as implemented
- **Read the minimum.**
  - IMAP reads headers for every message, plus the body of the newest message per sender. The body is cut to the signature window and discarded in memory.
  - Spam and junk folders are never opened.
  - Gmail step 1 calls only the People API; no Gmail message endpoint is called.
  - A test dumps every table to confirm that no body or signature text is stored.
- **Skipped senders** live only as a hash in `sender_signals`. Their running facts are counts and flags only, with no names or addresses.
- **Sources.** Every field records its source, confidence, account and message id, and when it was seen. Values the user edits, or typed before import existed, are locked. Department, expertise and role are labelled Inferred.
- **Claude** runs once per sender, and again only when the signature's trailing lines change. Calls per batch are capped by `mail_claude_per_sync`.
- **Scheduling.** The Sent folder is read first, so Warm contacts appear early. After that, the newest inbox mail is read in batches.
- **Optimal stopping.** The backfill stops reading further back once new contacts fall below `mail_backfill_min_yield` per 1,000 messages, after `mail_backfill_min_scanned` messages.
- **Campaign-ready** means:
  - a person, not a role address;
  - a valid address whose domain has an MX record;
  - not suppressed and not DNC;
  - a first name and a company.
- **The switch.**
  - It fails closed, and a global off wins over any per-user setting.
  - Restricting it pauses or revokes the affected connections, flags their contacts DNC (`contact_dnc_flags`, one row per reason), cancels scheduled steps and expires unsent drafts.
  - Every change records a reason, the admin and the counts (`feature_changes` plus the audit log).
  - Turning it back on lifts nothing. An admin may lift only one change's flags, and an unsubscribe, the user's own DNC or a suppression is never lifted.
  - The user can't lift a DNC the switch set.
- **Final gate.** `send_outreach_email` now refuses any Do Not Contact contact at send time. Before this, it didn't check the flag.

## Deviations and gaps
- **Turning the switch off:** users get the in-app notice; any email copy depends on their notification settings.
- **Scheduled sends:** cancelled when their `condition_ref` names the contact. Any others are stopped by the send-time DNC gate.
- **Dedup:** exact email match only. Merge suggestions, multiple emails and phones per contact, and per-user learning are phase 2.
- **Industry and company size:** never filled in phase 1, because they come only from enrichment (phase 4).
- **Staff access grants and account-deletion purge:** not built (PRD "Privacy"). Admin consoles that can open users' contacts are unchanged.

## Deploy
1. Run `sql/060_mail_crm.sql`.
2. Add the cron line in `cron_mail.py`.
3. For Gmail, add `https://simisi.io/mail/connect/gmail/callback` as an authorized redirect URI on the existing Google OAuth client, and enable the People API. Until Google verifies the sensitive scopes, the consent screen shows "unverified" and use is capped at 100 users.
4. The feature ships **allowlisted users only**. Allow beta users at `/admin/email-crm`.
5. Update the privacy policy (what inbox data is read, why, and who it's shared with). This is legal copy, so it hasn't been written here.
