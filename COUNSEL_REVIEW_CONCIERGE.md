# Counsel review — Founder Concierge Onboarding (DRAFT wording)

Every legal, consent, claims and policy string the concierge feature will show or send. All of it is
**draft for counsel**. In the build these live in `app/services/legal_text.py` (editable in
Admin → Legal Text), so counsel's changes need no code release. `{placeholders}` are filled at runtime.

**Final build (steps 1–12):** each string below carries its `legal_text` key in brackets. Templates call
`legal('key')`, Python calls `legal_text.get('key')`; no copy of the wording exists elsewhere (a test fails
if one appears). Editing a text in Admin → Legal text puts it back to draft until counsel approves it.

## 1. Consent and confidentiality (admin create form)
- **C1 Consent checkbox (required):** [`concierge_consent`] "{consent_given_by} authorized SimiSI to receive these documents
  and to evaluate them on behalf of {primary_name} for the purpose of preparing a Founder Track
  evaluation. Recorded by {admin_name} on {date}."
- **C2 Consent source field label:** [`concierge_consent_source_label`] "Who gave consent (the founder, or the referrer acting for them)"
- **C3 NDA flag label:** [`concierge_nda_label`] "An NDA covers these documents (reference kept in notes)"
- **C4 Confidentiality statement (admin area banner):** [`concierge_confidentiality`] "Founder documents are confidential. They are
  stored encrypted, visible only to admins with concierge permission and to the venture's primary
  founder, and are never public or used in analytics."

## 2. Evaluation and claims
- **E1 Required disclaimer (preview, full evaluation, emails that mention results):** [`concierge_not_advice`]
  "Not investment advice and not a promise of funding."
- **E2 Evaluation basis:** [`concierge_eval_basis`] "This evaluation was prepared by SimiSI from the documents provided. Scores
  describe what those documents show today. Fields marked 'Needs founder input' could not be found in
  them."
- **E3 Lower-confidence note when documents are missing:** [`concierge_missing_docs`] "Some documents were not provided, so parts
  of this evaluation rest on less evidence."
- **E4 AI and human review:** [`concierge_ai_review`] "The evaluation was drafted with AI and reviewed by a SimiSI team member."

## 3. Founder preview
- **P1 What accepting unlocks:** [`concierge_unlocks`] "Accepting gives you the full evaluation, your recommended agent team,
  your step-by-step execution plan, your documents and a private startup page draft, in your own
  account."
- **P2 Ownership terms on acceptance:** [`concierge_ownership_terms`] "When your payment is confirmed, this venture, its documents,
  the final evaluation, the agent team, the plan and the startup page draft become yours. SimiSI keeps
  read-only access for support; it edits only with your permission."
- **P3 Days left:** [`concierge_days_left`] "This evaluation is available until {expires_on} ({days_left} days left). If it is
  not accepted by then, the documents and the evaluation are permanently deleted."
- **P4 Decline confirmation:** [`concierge_decline_confirm`] "Decline this evaluation? It will be permanently deleted with your
  documents on {deletion_date}. You can still accept it until then."
- **P5 Revision request note:** [`concierge_revision_note`] "Ask SimiSI for a revision. The {window_days}-day window keeps running
  unless SimiSI extends it."

## 4. Fee, payment and refunds
- **F1 Fee line:** [`concierge_fee_line`] "Consultation fee: {fee} (one time). This replaces the Founder Track fee for this
  venture; you will not be charged both."
- **F2 Refund policy (preview and receipt):** [`concierge_refund_policy`] "Refundable within {refund_days} days of payment. A
  refund {refund_effect}." — built with D5 (approved): the shipped draft reads "Refundable within
  {refund_days} days of payment. A refund locks the venture's paid features again; it stays in your account."
  `{refund_days}` is the Pricing setting `fnd_refund_window_days`.
- **F3 Receipt email footer:** [`concierge_receipt_footer`] "Payment received for the SimiSI consultation fee. This is not an
  investment and does not give anyone a stake in your company."

## 5. Emails
- **M1 Share/invite email (subject):** [`concierge_invite_subject` + `concierge_invite_body`] "Your SimiSI Founder Track evaluation for {venture_name} is ready"
  Body: "{primary_name}, SimiSI has prepared a Founder Track evaluation of {venture_name} from the
  documents {consent_given_by} shared with us. Preview it and decide whether to accept it: {link}.
  The link works only for {primary_email} and expires on {expires_on}." + E1.
- **M2 Reminder (day 7 / day 21):** [`concierge_reminder`] "Your evaluation of {venture_name} is waiting. {days_left} days left
  before it and your documents are deleted." + link + E1.
- **M3 Final reminder (3 days before):** [`concierge_final_reminder`] "Last reminder: on {expires_on} your evaluation of
  {venture_name} and the documents behind it will be permanently deleted unless you accept it."
- **M4 Acceptance confirmation and receipt:** [`concierge_accepted_email`] "{venture_name} is now in your SimiSI account. Start with
  the fields marked 'Needs founder input'." + receipt + F2 + F3.
- **M5 Deletion notice (optional, to the founder):** [`concierge_deleted_email`] "Your evaluation of {venture_name} was not accepted
  and has been permanently deleted, together with the documents shared with us, on {date}."
- **M6 Email-mismatch page:** [`concierge_email_mismatch`] "This invitation was sent to another email address. Sign in with that
  address, or ask SimiSI to update it."

## 6. Data retention and privacy policy
- **R1 Deletion statement (privacy policy):** [`concierge_privacy_deletion`] "If a concierge evaluation is declined or not accepted
  within {window_days} days of being shared, SimiSI permanently deletes the uploaded documents and the
  evaluation. A minimal record (dates, status and fee) is kept."
- **R2 Backups:** [`concierge_backups`] "Deleted data can remain in encrypted server backups for up to {backup_days} days,
  after which the backups are overwritten." — `{backup_days}` to be confirmed with the hosting provider
  (GoDaddy cPanel backup retention); counsel to confirm the disclosure.
- **R3 Admin access after transfer:** [`concierge_admin_access`] "After you accept, SimiSI staff can view your venture read-only
  to support you. They can change it only if you turn on support access."

## 6a. Where each string appears (final build)
| Code | Key | Shown / sent |
|---|---|---|
| C1–C3 | `concierge_consent`, `concierge_consent_source_label`, `concierge_nda_label` | /admin/ventures/new (C1 is also stored in the creation audit row, redacted at deletion) |
| C4 | `concierge_confidentiality` | every /admin/ventures page |
| E1 | `concierge_not_advice` | admin evaluation, founder preview, M1/M2/M3 emails |
| E2–E4 | `concierge_eval_basis`, `concierge_missing_docs`, `concierge_ai_review` | admin evaluation |
| P1, P2, P3, P5 | `concierge_unlocks`, `concierge_ownership_terms`, `concierge_days_left`, `concierge_revision_note` | founder preview (and its JSON) |
| P4 | `concierge_decline_confirm` | decline confirmation dialog and the declined banner |
| F1, F2 | `concierge_fee_line`, `concierge_refund_policy` | founder preview; F2 also in the M4 receipt |
| F3 | `concierge_receipt_footer` | M4 receipt |
| M1 | `concierge_invite_subject`, `concierge_invite_body` | share / re-send email |
| M2, M3 | `concierge_reminder`, `concierge_final_reminder` | daily job reminders |
| M4 | `concierge_accepted_email` | receipt after payment |
| M5 | `concierge_deleted_email` | deletion notice from the daily job |
| M6 | `concierge_email_mismatch` | invitation opened by another or an unverified account |
| R1 | `concierge_privacy_deletion` | founder preview; for the privacy policy |
| R2 | `concierge_backups` | NOT shown anywhere yet: `{backup_days}` must be confirmed with the host first |
| R3 | `concierge_admin_access` | founder preview and the workspace "Confirm your details" card |

## 6b. How deletion and backups work (for the privacy policy)
- At the end of the window (`fnd_cc_window_days`, default 30, plus one admin extension of
  `fnd_cc_extend_days`), the daily job (`cron_concierge.py` + the in-process scheduler) permanently deletes a
  declined or expired case: the encrypted files (the venture's whole private folder), the extracted text, the
  evaluation runs and findings, the evaluation cache, run metadata, team, plan, page, members, the admin's
  edit history, the revision messages and the venture row. The case keeps only: its id, the venture id as a
  reference, its state, dates, fee and amount, admin ids, and "documents deleted on <date>". Names, emails,
  notes, pre-fill, consent source and reasons are nulled; the consent text quoted in the creation audit row
  is redacted.
- Files and extracted text are encrypted at rest with `ENCRYPTION_KEY` (Fernet). Backups therefore hold
  only ciphertext for documents; database backups hold the case rows until they age out.
- **Open:** the GoDaddy cPanel backup retention period (`{backup_days}` in R2, decision D11) must be
  confirmed with the host before R2 is published.

## 7. Open questions for counsel
1. Is recording consent from a **referrer** (not the founder) sufficient to receive and evaluate the
   founder's documents? Any additional terms the referrer must accept?
2. Ownership transfer wording (P2): do the documents and AI-generated evaluation need an explicit
   licence/assignment clause?
3. Refund effect (F2) once ownership has transferred.
4. Whether the consultation fee needs separate terms of service from the Founder Track fee.
5. Backup retention disclosure (R2) and whether deletion within backups must be guaranteed sooner.
