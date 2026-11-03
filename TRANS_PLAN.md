# PRD-SIM-TRANS-001: Employer Transition Package, phase 1

Source: `docs/PRD-SIM-TRANS-001 Employer Transition Package.docx`.
Branch: `feat/transition`, cut from `feat/mail-crm` so deploys keep stacking.

## Owner decisions (2026-11-02)
1. **Scope:** phase 1, design partners. SimiSI staff set up employers, cohorts and seats in Admin.
2. **Data model:** build on Organizations. An employer is a `corporate_accounts` row with `org_type = 'transition'`.
3. **Coaches:** SMEs (`simi_smes`) with `is_coach` and coach fields.
4. **Privacy:** strict, aggregate-only reporting for transition employers. Other organization types keep today's dashboard.

## Already existed (reused)
- **Organizations:** admin, co-branding, Stripe invoices (`org_invoices`) and contract dates.
- **SME logins:** login and vetting.
- **Seat funding:** `try_redeem_credit` is the single funding hook for simulation start and fork.
- **Email import:** Email-to-CRM, which gets the employer-domain block.
- **Send pipeline:** EXEC P5, which gets the confidentiality acknowledgement.

## Built
| Piece | Where |
|---|---|
| Tables: cohorts, seats, coaching sessions, encrypted notes, outcomes; coach columns on `simi_smes` | `app/models/transition.py`, `sql/061_transition.sql` |
| Service: tiers and quotes, seats, invites, activation, entitlements, report, coaching | `app/services/transition_service.py` |
| Staff admin | `/admin/transition` (Admin → Growth → Transition) |
| Employer report (aggregate) and seat-status CSV | `/transition/employer/<org_id>`; org admin or site admin |
| Employee invite (privacy summary and consent) and package page | `/transition/join/<token>`, `/transition`; dashboard banner |
| Coach console | `/coach` (an SME login with `is_coach`) |
| Invites, reminders and expiry | `cron_generate.py` calls `run_maintenance()`, at most hourly; no new cron line |
| Tests | `test_transition.py` (65 checks) |

## Rules as implemented
- **Prices and tiers are admin settings** (`transition_*`). A tier with no price is refused in the quote; nothing is hardcoded.
- **Seats** move unassigned → invited → activated → expired, and an unused seat can be revoked.
  - An activated seat can't be revoked.
  - Entitlements are copied at activation, so later tier edits don't change what was given: access months, the first simulation plus re-runs, and coaching sessions.
- **Personal emails** are encrypted. Invites go out on the cohort's invite date, with reminders on days 3, 7 and 14.
- **Activation** requires the employee to tick that they've read the privacy summary. Outcome sharing is off unless the employee opts in. The token is single-use.
- **Simulations:** the seat funds them first, inside `try_redeem_credit`. After expiry, the normal price applies.
- **Expiry:** the account and bio page stay with the employee, and the co-branding link is cleared.
- **The employer report** shows seat-status counts and the activation rate. Engagement and outcomes appear only when at least `min_report_size` employees (default 5) have activated, and smaller cells show as "<5".
  - Outcomes are counted only while the employee's consent switch is on. Turning it off withdraws everything they reported.
  - The report never shows simulations, income, contacts, outreach, bio drafts or coaching notes.
  - The general org dashboard and roster CSV redirect transition employers to it.
- **The employer's domains** are blocked in Email-to-CRM (IMAP and Gmail).
- **Confidentiality reminder:** a one-time acknowledgement before the first bio publish or send-to-contacts batch (HTTP 428, then retry with the ack). It appears in the bio page, the editor, the onboarding wizard and the send modal.
- **Coaching:**
  - Executive seats are matched to senior coaches. Other matches go by specialty overlap, then caseload, with 10% of matches going to the least-used coach. An employee can change coach once.
  - Held sessions use a credit. The first no-show is forgiven (admin grace setting), and cancelled sessions never count.
  - Employees rate held sessions. A coach averaging below 4.0 over 3 or more ratings is paused for review.
  - Notes are encrypted and readable only by the employee and the coach; no staff or employer route reads them.
  - A coach sees the simulation summary and bio only while the employee grants access.

## Deviations
- **Fewer tables than the PRD's model:** `seat_invites` and `coaching_credits` live as columns on `transition_seats`, and `employers` / `employer_admins` / `employer_contracts` reuse `corporate_accounts`.
- **One HR login per employer** (`admin_user_id`). Several HR admins with owner/manager/viewer roles is phase 2.
- **Agent action credits** per tier are not enforced; who pays when they run out is an open question in the PRD.
- **Workshops** are shown as included per tier, but attendance isn't tracked.
- **Coach payouts:** `pay_cents` is recorded per held session; payouts are phase 3.
- **Cohort research caching and the Bayesian outcome update** (PRD lenses) are not built.
- **Invoicing:** staff issue invoices through the existing org invoices; there's no per-cohort Stripe automation yet (phase 2).

## Found along the way (fixed 2026-11-02)
`POST /api/corporate/invite/<token>/accept` (ORG-001) took `user_id` from the request body without login. It now requires a signed-in user and always links that user; a body `user_id` is ignored and logged. `test_org_invite_security.py` covers it.

## Deploy
1. Run `sql/061_transition.sql`.
2. No new cron line is needed; `cron_generate.py` already runs every minute.
3. Set the tier prices at `/admin/transition` (Tiers, pricing and settings) before quoting.
4. Mark coaches under Coaches. They need an SME login and a Cal.com booking link.
5. **Legal review before the first design partner:** the consent screen, employer reporting, and the severance and tax wording.
