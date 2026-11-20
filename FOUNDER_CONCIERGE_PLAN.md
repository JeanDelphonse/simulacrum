# Founder Concierge Onboarding — Phase 1 inventory and plan

Source: `docs/CLAUDE_CODE_PROMPT_Founder_Concierge_Onboarding.md`. Branch `feat/founder-concierge`
(cut from `feat/founder-track`, which holds the whole Founder Track and is not yet on master).
Phase 1 is read-only: nothing below has been built. **Section 9 lists the decisions I need from you.**

---

## 1. What exists today (inventory)

### 1.1 Models (`app/models/founder.py`, sql/069–078)
| Model | Role for concierge |
|---|---|
| `FounderVenture` | The venture. `owner_user_id` is **NOT NULL** (one owner, several ventures per owner since sql/078, cap `MAX_VENTURES_PER_OWNER=5`). `status` ('active'…), `stage` (idea / pre-seed / seed / series-a), `rung`, `track` ('standalone' / 'addon'), `paid_at`, `payment_intent_id`, `amount_cents`, `support_access_until`. |
| `VentureMember` | Membership: `role` (owner / cofounder = account permission), `team_role` (sql/077), `title`, `status` (invited / active / left), invite token. |
| `DecisionRight` | Seven decision types, all held by the owner at creation. |
| `VentureArtifact` | Uploaded/transcribed documents, versioned per kind: startup_plan, exec_summary, ops_plan, pitch_deck, financial_model, pitch_transcript. Holds `file_path` **and the extracted `text_content` in plain text**. |
| `EvaluationRun`, `EvaluationFinding`, `FounderEvalCache` | Evaluation runs, cited findings, per-(artifact, dimension) cached answers. |
| `VenturePlanStep`, `VentureStepEvent`, `VentureAgentOutput`, `VentureOutreachAction` | Plan, events, agent outputs, drafts. |
| `StartupPage` (+ slug history, visits) | Created as `draft` by `startup_page_service.ensure_page()`; published only by the publish-decision holder. |
| `FounderRunMeta` | Founder context for an observability run (cost, prompt version). |

### 1.2 Self-serve intake fields
`/founder/start` → `founder_ventures.create_venture(user, name, one_liner, stage)`. That is the whole
intake form: **name, one-liner, stage**. Then documents (`/founder/v/<id>/intake`: five upload kinds,
PDF/DOCX, cap `founder_intake_service.max_upload_bytes()` = 20 MB) and an optional spoken pitch.
"Alignment" is the co-founder alignment check (`founder_alignment_service`, answered per founder;
it cannot be inferred from documents). Team roles/titles live on `VentureMember`.

### 1.3 Evaluation agents
`founder_evaluation_service.run / run_async(venture)`: for every current artifact × nine rubric
dimensions, one Claude call (`_evaluate_one`), citations verified word-for-word, cached per
artifact; missing kinds become "not provided" gap findings; scores, overall, rung estimate. Runs in a
background thread; logged in observability via `obs.run_context` (layer F1) with tokens
(`record_usage`) and `cost_cents` on the run and `FounderRunMeta`. Retries transient API errors;
releases the DB connection during calls (fix of 2026-11-15). Live updates go through
`founder_stream` (SSE, `/api/founder/v/<id>/stream`).
The recommended team and parameters come from `founder_team_service` (findings → recommended agents);
the execution plan from `founder_orchestrator.build_plan(venture)`. Self-serve builds these after payment.

### 1.4 How ventures attach to accounts
Ownership = `owner_user_id` + an `owner` `VentureMember` row. Every founder route resolves the venture
through `founder_ventures.require_member()` (404 unknown, 403 non-member) and `founder_required`
(the feature switch). There is no "pending / unowned" venture concept today.

### 1.5 Startup page
`startup_page_service.ensure_page(venture)` creates a `draft` page from evaluated evidence; it is
private until the publish-decision holder publishes and the name check clears. Concierge reuses this
unchanged (draft on acceptance = private, as required).

### 1.6 Pricing, Stripe checkout and webhook
- Founder prices are `founder_config` settings (`fnd_price_cents`, `fnd_addon_price_cents`,
  `fnd_investor_access_cents`, `fnd_refund_window_days`, `fnd_fee_mode`), edited in
  **/admin/founder → Pricing**. No price literals in code.
- `stripe_service.create_founder_checkout(venture, user, product)` builds an inline `price_data`
  Checkout Session (no Stripe price ids are used anywhere today).
- Webhook `payments/routes.py` `checkout.session.completed` has `elif` branches per product
  (`founder_track`, `founder_addon`, `investor_access`); `charge.refunded` revokes founder access
  (`billing.revoke_founder_payment`). Fulfilment is idempotent (`billing.fulfil_founder_session`).
- **Discounts:** `SimulationDiscount` (global sale) and `UserDiscount` (per-user comp vouchers) exist
  but apply to the **simulation** price only. There is no coupon mechanism for founder products.
- Refunds: `stripe_service.issue_refund(payment_intent_id, reason)`.

### 1.7 Email and scheduling
- `email_service._send` (SendGrid) from `MAIL_DEFAULT_SENDER` = **simi@simisi.io** (config default,
  env-overridable), display name SimiSI. `FOUNDER_EMAIL` = simi@simisi.io for admin notifications.
  **Answer to the brief's question: the support/sender address is simi@simisi.io.**
- `app/scheduler.py` (APScheduler, in-process) already runs founder jobs (alerts). Cron scripts exist
  (`cron_mail.py`, `cron_generate.py`, `cron_keepalive.py`). On cPanel/Passenger an idle process can be
  stopped, which stops APScheduler with it; time-critical jobs need a cron entry.

### 1.8 File storage and encryption
- Founder files: `founder_private/` (outside the public `UPLOAD_FOLDER`, excluded from git and the
  deploy zip), per-venture folder, validated by extension + content sniffing (`%PDF-`, DOCX zip with
  `word/document.xml`, unpack-size cap). **Not encrypted.** Extracted text stored **in plain text** in
  `venture_artifact.text_content`.
- Encryption helper exists: `token_crypto.encrypt_token / decrypt_token` (Fernet, `ENCRYPTION_KEY`),
  used for OAuth tokens and the pitch draft file. If `ENCRYPTION_KEY` is unset it silently returns
  plain text.
- **No malware scanning** anywhere in the app. Only PDF and DOCX parsers are installed (PyMuPDF,
  python-docx); PPTX/XLSX would need new dependencies.

### 1.9 Admin roles and permissions
A single boolean `users.is_admin`. Role flags `is_partner`, SME via its own table. Feature access per
user exists through `FeatureFlag` + `UserFeatureOverride` (`founder_feature`, modes off / allowlist /
on). **No per-admin permission system.**

### 1.10 Observability
`/admin/observability` → Founder Track tab: fleet, venture trace (masked unless the founder granted
support access; each view audited), autonomy audit, evaluation quality, compliance, alerts. Evaluation
runs already record model, tokens and cost. `/admin/founder?tab=ventures` lists ventures with owners.

---

## 2. Decisions the brief asks me to confirm in Phase 1
- **Sender:** simi@simisi.io (`MAIL_DEFAULT_SENDER`). Confirmed in code; still an env setting.
- **Coupons:** no founder coupon mechanism exists → **out of scope**; the per-venture fee override
  (with reason) covers one-off pricing.

---

## 3. Design

### 3.1 The venture before acceptance
A concierge venture is a normal `FounderVenture` with `track='concierge'`, `status='concierge'` and
**no owner yet** (`owner_user_id` NULL) — so it reuses the evaluation agents, plan, team and startup
page code unchanged. Because nothing resolves to a NULL owner, it is invisible to every founder route
(`require_member` finds no membership) and to `/founder`, dashboards, the directory and the sitemap.
On acceptance: set `owner_user_id`, add the `owner` member and default decision rights (the same code
path as `create_venture`), set `status='active'`, `paid_at`, `track='concierge'`.
→ requires `ALTER TABLE founder_venture MODIFY owner_user_id VARCHAR(9) NULL` (see §6, D1).

### 3.2 New table `concierge_case` (one per concierge venture) — sql/079
`id` (generate_pk), `venture_id` (unique), `state`, `primary_name`, `primary_email`,
`cofounder_emails` (JSON), `source`, `referrer`, `notes`, `consent_given_by`, `consent_at`,
`consent_version`, `nda` (bool), `fee_override_cents`, `fee_override_reason`, `fee_override_by`,
`confirmed_at`, `confirmed_by`, `shared_at`, `expires_at`, `extended_at`, `extended_by`,
`extend_reason`, `viewed_at`, `accepted_at`, `accepted_user_id`, `declined_at`, `decline_reason`,
`expired_at`, `documents_deleted_at`, `invite_token_hash`, `invite_version`, `invite_sent_at`,
`reminders_sent` (JSON), `payment_intent_id`, `amount_cents`, `refunded_at`, `prefill` (JSON:
`{field: {value, status: ai|admin|needs_input, citation}}`), `created_by`, `created_at`, `updated_at`.
After deletion only the minimal record survives (id, venture id, state, dates, fee,
`documents_deleted_at`); every content column is nulled.

### 3.3 New table `concierge_edit` — the AI-vs-admin record
`id`, `case_id`, `entity` ('prefill' | 'finding'), `entity_id`, `field`, `ai_value`, `before`,
`after`, `by_user_id`, `created_at`. Every admin edit to a pre-filled field or a finding writes one
row; the AI's original value is kept. Deleted with the case content at expiry.

### 3.4 New table `concierge_message` — revision requests and admin replies
`id`, `case_id`, `direction` (founder | admin), `body`, `by_user_id`, `created_at`.

### 3.5 Column `venture_artifact.encrypted` (TINYINT, default 0)
Concierge uploads are stored encrypted on disk (Fernet, `ENCRYPTION_KEY`) **and** their extracted
text is stored encrypted in `text_content`. A one-line decrypt in the evaluation's artifact read
(`if a.encrypted: decrypt`) — self-serve artifacts (`encrypted=0`) are untouched. Uploads are refused
if `ENCRYPTION_KEY` is not configured (no silent plain-text fallback). See D2.

### 3.6 State machine (one module, `concierge_service.transition()`)
```
draft → documents_uploaded → evaluating → ready_for_review → confirmed → shared → viewed →
  accepted_paid → active
  revision_requested → ready_for_review
  declined → deleted        expired → deleted
```
Every transition writes `AuditLog` (`concierge_<to_state>`, actor, venture, from, to, timestamp).
Re-run after new documents: `ready_for_review|confirmed → evaluating`. Re-share after a revision
issues a new token (old one invalid). Extension once: `expires_at += fnd_cc_extend_days`, reason
required, audited.

### 3.7 Settings (founder_config keys, edited in /admin/founder; safe defaults)
| Key | Default | Where |
|---|---|---|
| `fnd_concierge_fee_cents` | (you set it; no default price in code — 0 = not offered) | Pricing tab |
| `fnd_concierge_active` | false | Pricing tab |
| `fnd_concierge_currency` | usd | Pricing tab |
| `fnd_concierge_stripe_price_id` | '' (optional; inline price used when empty, as today) | Pricing tab |
| `fnd_cc_window_days` | 30 | new Concierge tab |
| `fnd_cc_reminder_days` | [7, 21, -3] (negative = days before expiry) | Concierge tab |
| `fnd_cc_extend_days` | 14 | Concierge tab |
| `fnd_cc_allowed_types` | ['pdf', 'docx'] | Concierge tab |
| `fnd_cc_max_upload_mb` | 20 | Concierge tab |
| `fnd_cc_refund_window_days` | reuse `fnd_refund_window_days` unless you want a separate one | Pricing |

### 3.8 Admin area `/admin/ventures` (new page, Founder Track admin group)
Queue with filters by state: venture, primary founder email, state, days since shared, days left, fee
(override shown), paid. Create form (primary name + email required, co-founder emails, source/referrer,
notes, **required consent checkbox** with who/when, NDA flag). Venture page: upload slots for the five
kinds (any subset), run/re-run evaluation with live SSE progress, pre-filled fields and findings
editable with version history, Confirm, Share, Extend, Correct email & re-send, Refund, and the
messages thread. After acceptance the page becomes **read-only** unless the founder granted support
access (reuses `support_access_until`).
Permission: a new `founder_feature` key **`founder_concierge`** used as a per-admin allowlist
(`UserFeatureOverride`), checked together with `is_admin` (D4).

### 3.9 Pre-fill
A new extraction step (one Claude call per run, logged in observability with tokens and cost) reads
the evaluated documents and proposes: venture name, one-liner (who it helps and how), stage, team
names/roles found in the documents. Anything not found is `needs_input`, never guessed. Alignment
answers are always `needs_input` (they belong to each founder). Findings, rung, recommended team and
plan come from the existing services (`founder_team_service`, `founder_orchestrator.build_plan`), run
against the concierge venture so the founder receives exactly what self-serve produces.

### 3.10 Share, invite and the founder preview
- "Share with founder" (state `confirmed` only) emails the primary founder a link
  `/founder/concierge/<token>`; the token is random, stored hashed, single-purpose, expires with the
  window, and is invalidated when the email is corrected or the case re-shared.
- The link requires sign-in. No account → sign-up with the email pre-filled. The venture is shown only
  when `current_user.email` equals `primary_email` **and** `email_verified` is true; a different or
  unverified email gets "This invitation was sent to another address" (no data).
- Preview (`/founder/concierge/<case_id>`, primary founder only): name, one-liner, overall score, the
  top three gaps (titles only), the count of "Needs founder input" fields, what accepting unlocks, the
  fee (override or Pricing), days left, and "Not investment advice and not a promise of funding."
  Its JSON endpoint returns only those fields (tested at the API, not just the template).
- Actions: **Accept and pay** (Stripe Checkout, product `concierge_fee`; acceptance happens only in
  the webhook), **Request a revision** (message → admin; window keeps running), **Decline** (optional
  reason → `declined`, deletion scheduled at window end — or immediately? see D9).
- Pending concierge invitations also appear as a card on `/founder` and the dashboard for the invited,
  verified email.

### 3.11 On acceptance (webhook `checkout.session.completed`, product `concierge_fee`)
Idempotent fulfilment (same pattern as `fulfil_founder_session`): verify amount = fee at share time,
transfer ownership (§3.1), set `paid_at`, `amount_cents`, `payment_intent_id`, state
`accepted_paid → active`, build plan/team if not built, `ensure_page` (draft, private), audit
"ownership transferred", email receipt to founder, notify admin. The venture now behaves exactly like
a paid self-serve venture; a "Confirm your details" card on the workspace lists pre-filled fields,
"Needs founder input" first. Co-founder emails on file pre-fill the invite form; nothing is sent
automatically (D8). `checkout_page` refuses the Founder Track fee for `track='concierge'`.

### 3.12 Expiry, decline and deletion
A job (APScheduler **and** a `cron_concierge.py` entry, D10) runs daily: reminders at the configured
days (each recorded in `reminders_sent`, never twice), notifies admin on expiry, and at window end for
`declined`/`expired` cases permanently deletes: the files (encrypted blobs), `venture_artifact`,
`evaluation_run`/`evaluation_finding`/`founder_eval_cache`, plan steps/events/outputs/drafts,
startup page (none should exist pre-acceptance), `concierge_edit`, `concierge_message`, the venture
row's content, then nulls the case's content columns and sets `documents_deleted_at`. A test asserts
the venture folder is empty and no content rows remain.

### 3.13 Observability and cost
Evaluation runs already log model, tokens and cost; the extraction call is logged the same way. The
concierge venture page shows total API cost of its runs next to the fee.

---

## 4. Ordered edits (each a small commit, tests after each)

| # | Change | Files | Risk | Verification |
|---|---|---|---|---|
| 1 | Migration sql/079: `concierge_case`, `concierge_edit`, `concierge_message`; `founder_venture.owner_user_id` NULL; `venture_artifact.encrypted` | sql/079, models/founder.py | Medium (alters an existing column) | model/sql check; full founder suite; delete_user SQL test |
| 2 | Settings keys + admin Pricing fields + Concierge tab | founder_config.py, admin/founder.py, admin/founder.html | Low | config tests; no price literal grep |
| 3 | `concierge_service`: state machine, audit, permission check | new service | Low | unit tests for every transition |
| 4 | Encrypted storage for concierge uploads + decrypt hook in evaluation read | founder_intake_service.py (additive branch), founder_evaluation_service.py (1-line hook) | Medium | self-serve intake/eval tests unchanged; encrypted-at-rest test |
| 5 | `/admin/ventures`: queue, create, upload, consent | new blueprint module + templates | Low | admin tests, 390 px screenshot |
| 6 | Evaluation run + SSE + pre-fill extraction + edit versions + confirm | concierge_service, admin templates | Medium | tests with faked Claude; observability rows |
| 7 | Share, token, invite email, reminders, admin notifications | concierge_service, email | Medium | token tests (wrong email, unverified, re-send invalidates) |
| 8 | Founder preview page + JSON + decline + revision | founder blueprint module + template | Medium | preview API never returns full content; 403 for co-founder/others |
| 9 | Stripe `concierge_fee` checkout + webhook branch + refund | stripe_service.py, payments/routes.py (new `elif`) | Medium | webhook-only acceptance; failed/abandoned payment leaves state |
| 10 | Acceptance transfer + confirm-your-details card + checkout guard | concierge_service, workspace template, billing.py guard | Medium | self-serve flow tests unchanged; never charged premium |
| 11 | Deletion/expiry job + cron script | scheduler.py, cron_concierge.py | Medium | storage empty, minimal record kept |
| 12 | Admin Ventures tab / observability handle owner NULL; counsel file final | admin.py, founder_obs_service.py | Low | full suite, screenshots |

---

## 5. Additive-only conflicts (need your OK)
1. **`founder_venture.owner_user_id` becomes nullable** (schema change to an existing table). Self-serve
   always sets it, so behavior is unchanged; admin lists must show "unassigned" for concierge ventures.
2. **Evaluation decrypt hook**: one guarded line in `founder_evaluation_service` artifact reading.
3. **`checkout_page` guard** refuses the Founder Track fee for concierge ventures (new condition only).
4. **Webhook**: a new `elif product == 'concierge_fee'` branch and a refund branch (pattern already used).
5. **Owner-cap** (`MAX_VENTURES_PER_OWNER`): accepting a concierge venture adds an owned venture.

## 6. Out of scope (unless you say otherwise)
- Discount codes for founder products (no mechanism exists; the per-venture override covers it).
- PPTX/XLSX uploads (no parser installed); PDF/DOCX only by default — the allowed types setting can
  only list types we can read.
- Antivirus scanning (no scanner on the host; see D3).

---

## 9. Decisions needed before Phase 2

| # | Question | My recommendation |
|---|---|---|
| D1 | Represent the pre-acceptance venture as a `FounderVenture` with NULL owner (reuses everything) vs separate tables copied on acceptance | **NULL owner** |
| D2 | Encrypt concierge files and their extracted text with `ENCRYPTION_KEY`; refuse uploads if the key is missing. Is `ENCRYPTION_KEY` set in cPanel? | **Yes**, and confirm the key exists in production |
| D3 | Malware scanning: shared hosting has no scanner; sending confidential documents to an external scanning API would breach confidentiality. Do strict structural checks instead (PDF/DOCX only, reject macro-enabled, encrypted or embedded-file documents, never serve originals inline) and call it "content checks", or require ClamAV on the server? | **Structural checks** now; ClamAV if the host supports it |
| D4 | Concierge permission as a per-admin allowlist via the existing feature-override mechanism (no new roles table) | **Yes** |
| D5 | Refund after acceptance: venture stays the founder's but locks again (like self-serve), or is it removed? | **Locks again, founder keeps it** (counsel to confirm) |
| D6 | Owner cap: do concierge ventures count toward `MAX_VENTURES_PER_OWNER`? | **Exempt** (admin-led) |
| D7 | Decline: delete at window end (as written) or immediately on decline? | **At window end** (as written; lets a founder change their mind) |
| D8 | Co-founders after acceptance: pre-fill the invite form only (founder sends) vs auto-invite | **Pre-fill only** |
| D9 | Founder preview URL inside the account: `/founder/concierge/<id>` plus a "Pending review" card on `/founder` and the dashboard | **Yes** |
| D10 | Scheduled deletion/reminders: APScheduler plus a daily cron line (`cron_concierge.py`) because Passenger can stop the in-process scheduler | **Both**, cron is the guarantee |
| D11 | Backups: cPanel backup retention period on GoDaddy (needed for the privacy statement) | **You confirm with the host** (counsel item) |
| D12 | Consultation fee amount and currency | **You set it** in Admin → Pricing (not stored in code) |

Deploy notes (when built): run `sql/079_founder_concierge.sql`; set `ENCRYPTION_KEY`; add the
`cron_concierge.py` daily cron line; grant the `founder_concierge` permission to the admins who onboard.

---

## 10. Build status (steps 1–12 built) and deploy notes

Built on `feat/founder-concierge`; `test_founder_concierge.py` covers every step and the brief's
QUALITY CHECKS. Changes beyond the plan, made while building steps 7–11:
- **Transitions:** `declined → viewed` (the founder may change their mind until the window ends, as P4
  says) and `ready_for_review | confirmed → expired` (a case sent back for a revision keeps its running
  window and can expire while back in review).
- **Share refuses** while the consultation fee is inactive or under the card minimum, and while the
  Founder Track switch is `off` (an accepted venture could not be opened). On acceptance the founder is
  allowed on the `founder_track` allowlist when it is in allowlist mode.
- **Plan after acceptance:** `founder_orchestrator.build_plan` needs accepted agents, so (as on self-serve)
  the founder accepts the recommended team on the Team page and the plan follows; the team and the private
  startup page draft are created at acceptance.
- **Reminders** link the preview (sign-in protected), not the token (only its hash is stored). If several
  reminder days were missed, only the latest due one is sent; the rest are recorded as skipped.

Deploy:
1. Run `sql/079_founder_concierge.sql` (no other migration; steps 7–12 use its columns).
2. Set `ENCRYPTION_KEY` in the cPanel environment **and** in `~/simisi/.env` (cron reads the .env).
   Without it concierge uploads and reads are refused.
3. Add the daily cron line (same interpreter as `cron_mail.py`):
   `30 5 * * * /home/dburriyy6pdz/virtualenv/simisi/3.9/bin/python /home/dburriyy6pdz/simisi/cron_concierge.py >> /home/dburriyy6pdz/simisi/cron.log 2>&1`
   (`python cron_concierge.py` daily; the in-process APScheduler job `concierge-daily` runs the same
   idempotent function at 05:20 UTC.)
4. Grant the concierge permission: Admin → Founder Track → Overview → `founder_concierge`, allowlist the
   admins who onboard (mode stays "allowlist").
5. Set the fee: Admin → Founder Track → Pricing → Consultation fee (amount, currency, optional Stripe price
   id) and turn it **active**. Founder Track must not be `off`.
6. Stripe: the existing webhook endpoint already receives `checkout.session.completed` and
   `charge.refunded`; no new event types.
7. `FOUNDER_EMAIL` (admin notices) and `MAIL_DEFAULT_SENDER` (sender, simi@simisi.io) as today.
8. Counsel: every string is a draft in Admin → Legal text (`concierge_*`); R2 needs the host's backup
   retention before it is published (COUNSEL_REVIEW_CONCIERGE.md §6b).

### Evaluation worker (added after launch)
Evaluations (concierge and self-serve) are queued and run by `cron_evaluations.py` when
Admin → Founder Track → Concierge → "Run evaluations with" is "Cron worker" (`fnd_eval_runner = cron`,
the default). cPanel/Passenger can stop the website's background threads mid-run; cron is not stopped.
- Cron line, every minute (use the app folder the site runs from):
  `* * * * * /home/dburriyy6pdz/virtualenv/public_html/simisi/3.9/bin/python /home/dburriyy6pdz/public_html/simisi/cron_evaluations.py >> /home/dburriyy6pdz/public_html/simisi/cron.log 2>&1`
- The worker reads the app folder's `.env`: `ENCRYPTION_KEY` and the Claude API key must be there.
- One worker at a time (lock file); an idle minute costs one query. A run still queued after
  `fnd_eval_queue_warn_minutes` (default 3) shows "Run here instead" on the concierge case page.
- Until the cron line exists, set "Run evaluations with" to "Website background thread".
