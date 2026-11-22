# Founder Track — Customer Communication (FND-001 addition, PRD decision of Nov 21, 2026)

Source: `docs/SIM-PRD-FND-001 SimiSI Founder Track.md`, sections "Customer communication",
"Compliance guardrails" (Founder identity, Commercial email), "Agent observability" (Connectors,
Alerts), success metrics rows for Goal 2/6/7, and the new risk row on shared-domain complaints.

## 1. What is new versus what is built

| PRD item | Today | This addition |
| --- | --- | --- |
| Customer email | The 7 customer agents make drafts; founders copy them and send them themselves | SimiSI sends after approval, in the founder's name, with replies going to the founder |
| Contact book | None per venture (investor sends use the owner's CRM `contacts`) | One contact book per venture, shared by co-founders, with states and per-agent access |
| Do-not-contact | Global `EmailSuppression` + per-user suppression | Venture do-not-contact list that blocks every agent; opt-outs, bounces and complaints feed it |
| Send flow | Approve, then copy | Pick recipients → preview per recipient → approve at the agent's level → send now or schedule |
| Approval rules per agent | One level per action type | The PRD table: e.g. Pilot Offer and Proof-of-Concept Plan always approve-each; Outbound Pipeline first contact approve-each, one recipient at a time; Pricing Test prices locked |
| Complaint auto-pause | Per campaign (`send_limits.review_campaign`) | Per venture: customer sending pauses when the venture passes its complaint threshold |
| Observability | Investor sends only | Connectors view adds bounces, complaints and opt-outs; Startup pages and metrics rows |
| Metrics | — | Customer reply rate, opt-out and complaint rate, startup page investor reach |

Investor outreach is unchanged. It keeps the approved deviation in FOUNDER_PLAN.md (sent through
SimiSI in the founder's name).

## 2. Integration rule

This is an addition. Nothing existing changes behaviour:
- Existing `contacts`, `email_log`, `email_suppression` and the wealth agents are untouched.
- The new behaviour sits behind a new setting, `fnd_customer_email` (off | on, default **off**). While
  it is off, customer drafts stay copy-only, exactly as they are today.
- Sends reuse `outreach_email_service.send_outreach_email` (postal address, footer, unsubscribe,
  global suppression, EmailLog) through its existing opt-in arguments (`reply_to`, `unsubscribe_url`).
- The SendGrid webhook gets one additive hook: if an EmailLog row belongs to a venture email, update
  that venture's contact and do-not-contact list. Existing branches are unchanged.

## 3. Data (sql/080_founder_customer_email.sql)

The PRD's table names collide with existing tables (`contacts`, `email_suppression`), so the new
tables take the venture prefix, as `venture_plan_step` already does.

| Table | Holds |
| --- | --- |
| `venture_contact` | venture_id, email, name, company, role, segment, source (manual / csv / gmail / outlook / agent), state (proposed / approved / contacted / replied / opted_out / bounced), proposed_by_agent, approved_by, timestamps |
| `venture_contact_access` | venture_contact_id × agent_key: which approved contacts each agent may email |
| `venture_email_message` | One row per recipient per send: outreach_action_id, contact_id, subject/body as sent, status (scheduled / sent / skipped / bounced / complained / replied), scheduled_for, sent_at, email_log_id, approved_level, approver |
| `venture_suppression` | venture_id, email, reason (opt_out / bounce / complaint / manual), source event; blocks every agent in that venture |

Per the PRD, every customer email also stays logged as a `venture_outreach_action`. That row is the
approval; `venture_email_message` holds the per-recipient content and delivery status.

## 4. Behaviour

**Contact book** at `/founder/v/<id>/contacts`, visible to every co-founder:
- Add contacts by hand, by CSV upload, or by import from the founder's connected Gmail or IMAP
  mailbox (reusing MAIL-001's `mail_contacts`). The page states that purchased lists are not
  accepted, and a checkbox on CSV upload confirms it.
- Agent suggestions are created as **Proposed**. Customer agents may list "suggested contacts" in
  their output; a founder approves or rejects each one.
- Per-agent access: a grid of approved contacts × the 7 customer agents, with tick boxes.
- An opt-out, bounce or complaint moves the contact to the venture do-not-contact list, which blocks
  every agent.

**Send flow** on each customer draft (the `_drafts.html` card gains a "Send through SimiSI" panel
when `fnd_customer_email` is on):
1. Pick recipients from that agent's allowed, approved, not-suppressed contacts.
2. Preview each email as the recipient sees it: merge fields, From "<Founder> via SimiSI",
   Reply-To, unsubscribe link and mailing address.
3. Approve at the agent's level:
   - **approve-each** — every recipient is approved one by one.
   - **approve-the-campaign** — one approval covers the selected recipients.
4. Send now, or schedule a time. The existing `cron_evaluations.py` worker (or a new
   `cron_customer_email.py`, run every minute) sends scheduled rows.
5. Every send is logged (EmailLog + `venture_email_message`, plus AuditLog and the founder activity
   stream) and shows in `/admin/observability`.

If sending is unavailable (setting off, SendGrid missing, venture paused), the draft stays copy-only
with the reason shown.

**Per-agent approval rules** live in `config/founder_agents.json` as a new `customer_send` block per
agent, so the code has one source:

| Agent | First contact | Follow-ups | Can be raised |
| --- | --- | --- | --- |
| Pain-Point Evidence | approve each | campaign | no higher than shown |
| Customer Interview | campaign (requires a scheduling link in the body) | campaign | no |
| Proof-of-Concept Plan | approve each, one named decision-maker | approve each | never |
| Prototype Feedback | campaign | campaign | no |
| Pilot Offer | approve each, the PoC customer only | approve each | never (states a price) |
| Pricing Test | campaign, price versions locked by the founder before sending; the send refuses a body whose prices differ from the locked set | campaign | no |
| Outbound Pipeline | approve each, **one recipient per send**, never raised | campaign, only to prospects who replied | first contact never |

"Follow-up" means a contact with at least one earlier sent message from this venture. The agent
cannot change prices or recipients after approval; any edit sends the draft back for approval
(bound by a content hash, as `send_pipeline` already does).

**Sending identity**
- From name: "<Founder name> via SimiSI". Reply-To: the founder's own email.
- From address: an admin setting, `fnd_customer_from_address` (default `MAIL_DEFAULT_SENDER`),
  which must be on a domain verified in SendGrid. Per-venture subdomains and founders' own verified
  domains are listed in §6; they are not built here.
- Every email carries the unsubscribe link and the founder's mailing address from Settings. The link
  is the existing founder link: it feeds `venture_suppression` and the owner's own opt-out list,
  not the global list. A spam complaint still goes global, as it does for every SimiSI send. A commercial email is refused
  until that address is set; the existing `no_postal_address` path covers this.
- The general action cap per agent applies (`fnd_action_cap_per_day`, now counted per agent for
  customer sends). Outbound Pipeline has no separate daily cap.

**Replies.** Reply-To is the founder, so SimiSI does not see replies through Inbound Parse. They are
matched two ways:
1. If the founder has connected a mailbox (MAIL-001), `mail_sync` marks a contact **Replied** when a
   message arrives from that address after the send, and links it to the send's plan step.
2. Otherwise a one-click "Mark replied" on the contact or the message.

**Complaint auto-pause.** A new alert metric, `customer_complaint_rate_pct` (default 0.3% over
30 days, minimum 20 sends before it applies), sits beside `customer_bounce_rate_pct` (default 5%).
Passing the complaint threshold sets `founder_venture.customer_sending_paused`, which is shown to
the founder and on the admin Venture trace. An admin resumes it.

## 5. Admin and observability

- Admin → Founder Track settings:
  - `fnd_customer_email` (off/on);
  - `fnd_customer_from_address`;
  - the complaint and bounce thresholds;
  - `fnd_csv_max_rows` (default 500).
- `/admin/observability` Connectors view: email sent / bounced / complained / opted out per venture,
  plus mailbox authorisation failures (existing).
- Startup pages view: invited links opened and paid unlocks per published page (the PRD's "startup
  page investor reach").
- Metrics, from existing data plus the new tables:
  - customer reply rate by agent;
  - opt-out and complaint rate per customer email;
  - startup page investor reach.
- Privacy: contact names and message bodies stay masked in admin views unless the founders grant
  support access (existing `mask()`).
- Founder data protection: deleting a venture deletes its contact book, messages and suppression
  rows. Audit rows keep metadata only.

## 6. Not in this addition (listed for Phase 4)

- Per-venture sending subdomains, and founders verifying their own domain (SendGrid domain
  authentication API plus DNS steps).
- Outlook import. MAIL-001 has Gmail contacts and IMAP; Outlook would need a new OAuth app.
- Automatic reply detection without a connected mailbox. The alternative, a second Reply-To at the
  SimiSI inbound address, makes the reply look odd to the customer.
- Customer email for concierge ventures before the founder accepts. Sending requires a signed-in
  founder.

## 7. Tests (house style, root `test_founder_customer_email.py`)

- Setting off: nothing changes; drafts stay copy-only.
- Contact states and transitions; CSV import (limits, duplicates, refuses a row on the venture or
  global suppression lists); Proposed → Approved only by a founder.
- Per-agent access: a contact not ticked for the agent cannot be picked.
- Approval rules for each of the 7 agents. In particular:
  - Outbound Pipeline refuses two recipients on a first contact and refuses raising;
  - Pilot Offer is refused to anyone but the PoC customer;
  - Pricing Test refuses changed prices.
- An edit after approval needs re-approval.
- The send carries From "<Name> via SimiSI", Reply-To founder, the unsubscribe link and the postal
  address; it is refused without a postal address.
- Webhooks: bounce or spamreport for a venture email update the contact and `venture_suppression`;
  passing the complaint rate pauses the venture.
- Scheduled sends go out from the worker; a paused venture or an opted-out contact is skipped.
- Reply via `mail_sync` and "Mark replied".
- Observability rows and metrics.
- Venture deletion removes contact book rows.
- Regression: all existing founder test files plus the full suite pass unchanged.

## 8. Deploy

- Run `sql/080_founder_customer_email.sql`.
- Make sure the From address domain is authenticated in SendGrid (SPF/DKIM/DMARC).
- Add the cron line for scheduled sends, if a separate worker is used.
- Switch `fnd_customer_email` on in Admin → Founder Track when ready.
