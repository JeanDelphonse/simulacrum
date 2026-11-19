# Counsel review — Founder Concierge Onboarding (DRAFT wording)

Every legal, consent, claims and policy string the concierge feature will show or send. All of it is
**draft for counsel**. In the build these live in `app/services/legal_text.py` (editable in
Admin → Legal Text), so counsel's changes need no code release. `{placeholders}` are filled at runtime.

## 1. Consent and confidentiality (admin create form)
- **C1 Consent checkbox (required):** "{consent_given_by} authorized SimiSI to receive these documents
  and to evaluate them on behalf of {primary_name} for the purpose of preparing a Founder Track
  evaluation. Recorded by {admin_name} on {date}."
- **C2 Consent source field label:** "Who gave consent (the founder, or the referrer acting for them)"
- **C3 NDA flag label:** "An NDA covers these documents (reference kept in notes)"
- **C4 Confidentiality statement (admin area banner):** "Founder documents are confidential. They are
  stored encrypted, visible only to admins with concierge permission and to the venture's primary
  founder, and are never public or used in analytics."

## 2. Evaluation and claims
- **E1 Required disclaimer (preview, full evaluation, emails that mention results):**
  "Not investment advice and not a promise of funding."
- **E2 Evaluation basis:** "This evaluation was prepared by SimiSI from the documents provided. Scores
  describe what those documents show today. Fields marked 'Needs founder input' could not be found in
  them."
- **E3 Lower-confidence note when documents are missing:** "Some documents were not provided, so parts
  of this evaluation rest on less evidence."
- **E4 AI and human review:** "The evaluation was drafted with AI and reviewed by a SimiSI team member."

## 3. Founder preview
- **P1 What accepting unlocks:** "Accepting gives you the full evaluation, your recommended agent team,
  your step-by-step execution plan, your documents and a private startup page draft, in your own
  account."
- **P2 Ownership terms on acceptance:** "When your payment is confirmed, this venture, its documents,
  the final evaluation, the agent team, the plan and the startup page draft become yours. SimiSI keeps
  read-only access for support; it edits only with your permission."
- **P3 Days left:** "This evaluation is available until {expires_on} ({days_left} days left). If it is
  not accepted by then, the documents and the evaluation are permanently deleted."
- **P4 Decline confirmation:** "Decline this evaluation? It will be permanently deleted with your
  documents on {deletion_date}. You can still accept it until then."
- **P5 Revision request note:** "Ask SimiSI for a revision. The {window_days}-day window keeps running
  unless SimiSI extends it."

## 4. Fee, payment and refunds
- **F1 Fee line:** "Consultation fee: {fee} (one time). This replaces the Founder Track fee for this
  venture; you will not be charged both."
- **F2 Refund policy (preview and receipt):** "Refundable within {refund_days} days of payment. A
  refund {refund_effect}." — `refund_effect` depends on decision D5 (e.g. "locks the venture's paid
  features again; it stays in your account").
- **F3 Receipt email footer:** "Payment received for the SimiSI consultation fee. This is not an
  investment and does not give anyone a stake in your company."

## 5. Emails
- **M1 Share/invite email (subject):** "Your SimiSI Founder Track evaluation for {venture_name} is ready"
  Body: "{primary_name}, SimiSI has prepared a Founder Track evaluation of {venture_name} from the
  documents {consent_given_by} shared with us. Preview it and decide whether to accept it: {link}.
  The link works only for {primary_email} and expires on {expires_on}." + E1.
- **M2 Reminder (day 7 / day 21):** "Your evaluation of {venture_name} is waiting. {days_left} days left
  before it and your documents are deleted." + link + E1.
- **M3 Final reminder (3 days before):** "Last reminder: on {expires_on} your evaluation of
  {venture_name} and the documents behind it will be permanently deleted unless you accept it."
- **M4 Acceptance confirmation and receipt:** "{venture_name} is now in your SimiSI account. Start with
  the fields marked 'Needs founder input'." + receipt + F2 + F3.
- **M5 Deletion notice (optional, to the founder):** "Your evaluation of {venture_name} was not accepted
  and has been permanently deleted, together with the documents shared with us, on {date}."
- **M6 Email-mismatch page:** "This invitation was sent to another email address. Sign in with that
  address, or ask SimiSI to update it."

## 6. Data retention and privacy policy
- **R1 Deletion statement (privacy policy):** "If a concierge evaluation is declined or not accepted
  within {window_days} days of being shared, SimiSI permanently deletes the uploaded documents and the
  evaluation. A minimal record (dates, status and fee) is kept."
- **R2 Backups:** "Deleted data can remain in encrypted server backups for up to {backup_days} days,
  after which the backups are overwritten." — `{backup_days}` to be confirmed with the hosting provider
  (GoDaddy cPanel backup retention); counsel to confirm the disclosure.
- **R3 Admin access after transfer:** "After you accept, SimiSI staff can view your venture read-only
  to support you. They can change it only if you turn on support access."

## 7. Open questions for counsel
1. Is recording consent from a **referrer** (not the founder) sufficient to receive and evaluate the
   founder's documents? Any additional terms the referrer must accept?
2. Ownership transfer wording (P2): do the documents and AI-generated evaluation need an explicit
   licence/assignment clause?
3. Refund effect (F2) once ownership has transferred.
4. Whether the consultation fee needs separate terms of service from the Founder Track fee.
5. Backup retention disclosure (R2) and whether deletion within backups must be guaranteed sooner.
