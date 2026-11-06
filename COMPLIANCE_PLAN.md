# Compliance risk reduction: Phase 1 inventory and plan

**Branch:** `fix/compliance-risks`, cut from `feat/hero-mascot-liftoff` so deploys keep stacking.

**Status:** read-only inventory. No code has been changed.

This is product and copy engineering, not legal sign-off. Every user-facing wording proposed here is a **draft for counsel**. All of it is collected side by side in `COUNSEL_REVIEW.md`, and none of it is marked final.

---

## 0. Decisions needed before editing

| # | Decision | Why it matters | My proposal |
|---|---|---|---|
| D1 | **Hard rule 6 is not true by default today.** The orchestrator sends cold email and consulting outreach, plus their scheduled follow-ups, whenever the simulation's `email` channel approval is on. That is the default: `Layer6Config.channel_approvals` has `'email': True` (`app/models/layer6.py:56`, `app/blueprints/layer6/routes.py:245`). Only the P5 "Send to contacts" path asks for a per-batch confirmation. | The task says "never send without approval; do not weaken". The rule has to be *made* true. | **(a) Default `email` (and `email_funnels`) to off for new simulations**, and treat the per-simulation channel switch as the user's standing approval. The UI wording becomes "Send outreach automatically on this channel", with an explicit opt-in. Existing simulations keep their current setting. **(b)** A stricter alternative: route every orchestrator send through the P5 confirm-batch gate, so there is no standing approval at all. |
| D2 | **Primary keys.** The rule asks for `generate_pk()`, 9 characters, **uppercase**. The repo only has `utils.id_gen.generate_id()`, which produces mixed-case IDs (e.g. `L3JxpP2ms`) and is used by every table. | Consistency against the rule as written. | Add `generate_pk()` (uppercase A–Z0–9, 9 characters) to `utils/id_gen.py`. Use it for the new tables only: `legal_texts`, `outcome_checkins`, `compliance_events`, `send_caps_ledger` (if needed) and `mailbox_warmup`. |
| D3 | **The "Index Fund & ETF Recommendations" agent** (`fund_recommendations`). Its spec says "Curate 8–12 recommended funds… tickers". | It is personalised product selection by design, so guardrails alone can't make it compliant. | **Reframe it as "Fund Types Explained"**: index fund and ETF categories, expense ratios, account placement concepts and questions for an adviser, with no named funds. The `action_type` stays the same. *Alternative:* disable it. |
| D4 | **The "Dollar-Cost Averaging Schedule" agent** (`dca_schedule`). It asks for a contribution amount and a brokerage, writes a "brokerage-specific setup guide", and lists `alpaca` as an integration. | Specific amounts and platform instructions read as personalised direction. | Reframe it as "Regular Contribution Concepts": how dollar-cost averaging works, with an illustrative schedule using the user's own example number labelled as an example. No platform setup steps. Remove `alpaca` from its integrations. |
| D5 | **The "Automated Residual" layer name** (L4), used in 20+ places. | It promises residual income. | Keep the internal key `L4`. The user-facing name becomes **"Recurring income"**, pending counsel (see `COUNSEL_REVIEW.md` §1). |
| D6 | **The public share-ROI card** says "Captured $X from a $Y simulation", built from the user's own *recorded* income. | It is a results claim the user publishes about SimiSI. | Keep the card, add the typical-results disclosure from the legal-text source, label the figure "Self-reported, not verified", and require an explicit "Share" opt-in (it is token-shared today). |
| D7 | **The sender postal address.** The current footer uses SimiSI's `BUSINESS_ADDRESS = 'Santa Clara, CA'`, which is a city, not a postal address (`outreach_campaign_service.py:26`). | CAN-SPAM wants the sender's valid physical postal address. | (i) A per-user postal address, collected in Settings, that **blocks campaign and outreach sends until it is set**. (ii) SimiSI's own address becomes an **Admin setting**, which blocks admin drip and broadcast sends until it is set. |

---

## 1. Workstream A: earnings claims

### A1. Copy audit
I searched templates, JS, Python strings (emails, prompts), `config/*.json` and `sql/` seeds, case-insensitively. Three groups of hits are **not user-facing** and are left alone:
- code comments and identifiers ("guarantee each agent node…", `{passive:true}` scroll listeners);
- human-SME "advisory" recommendations, which are not investment-related;
- `bio_quality` "advisory, never blocks" comments.

User-facing hits:

| # | Where | Text | Type | Proposed (draft, counsel) |
|---|---|---|---|---|
| 1 | `app/services/site_context.py:28` (renders on the home page layer stack) | L4 name "Automated Residual Income", tag "Systems that run while you sleep" | residual, while you sleep | "Recurring income" / "Income that keeps working after the initial build" |
| 2 | `app/blueprints/simulations/routes.py:26` | L3 subtitle "Earn While You Sleep" | while you sleep | "Built once, sold repeatedly" |
| 3 | `app/templates/simulations/detail.html:250` | layer subtitles: "Earn While You Sleep", "Money That Makes Money" | while you sleep / implied returns | "Built once, sold repeatedly"; "Planning what to do with what you earn" |
| 4 | `app/models/simulation.py:118-119` (stored as `layer_name` and rendered) | "Automated Residual Systems — Running Without You"; "Wealth Deployment — Money Working for You" | residual / implied returns | "Recurring Income — After the Initial Build"; "Wealth Planning — Education and Planning" |
| 5 | `app/services/claude.py:222-223` (prompt layer definitions; the wording reaches generated text) | the same names as #4, plus "index funds/ETFs, real estate equity, angel investing, dividend portfolios" | residual / products | Same names as #4. L5 description: "general education about saving, allocation concepts and questions to bring to a licensed professional" |
| 6 | `app/services/outreach_campaign_service.py:107` (SimiSI drip 3 subject, seeded into `outreach_templates`) | "How SimiSI builds income while you sleep" | while you sleep | "How the deeper layers work, and what they take to build" |
| 7 | same file, drip 3 body (~lines 111–124) | "income that compounds on its own", "revenue streams that run without your daily involvement", "so your income builds lasting wealth" | passive framing | See COUNSEL_REVIEW §2 |
| 8 | same file, drip 2 body | "Most users see their first opportunities within the first few cycles." | unsupported results claim | Remove, or replace with "Results depend on your field, effort and market." |
| 9 | `app/services/simi_service.py:42` (Simi suggested question chip) | "What's my path to passive income?" | passive income | "Which layers fit my situation?" |
| 10 | `app/templates/corporate/invite.html:48` | "…from active consulting to passive assets" | passive | "…from consulting to longer-term planning" |
| 11 | `app/templates/settings/index.html:2232-2240` (voice-training script the user reads aloud) | "generated 46,000 dollars in confirmed income…", "within two weeks… first client… three income streams running on autopilot", "books discovery calls" | money claims, autopilot | A neutral script with no income or results claims (COUNSEL_REVIEW §2) |
| 12 | `app/services/fintech.py:30-41` (static allocation guidance) | 60/20/10/10% allocations; "Passive real estate exposure"; named platforms Fidelity, Vanguard, Schwab, M1 | personalised allocation, products | **Remove.** The function has no callers (dead code) |
| 13 | `app/templates/landing.html:122` and `app/services/site_context.py:92` | "Advisory only. These agents plan…"; "advisory only; SimiSI never places trades" | "advisory" (B1) | "Education and planning only. These agents explain options; they never trade or move money." |
| 14 | `app/templates/execution/autonomy.html:34` | "Layer 5 agents only generate advisory documents" | advisory | "…only generate education and planning documents" |
| 15 | `app/templates/public/simi_help.html:293` | "Financial and legal guidance is advisory" | advisory | "Financial and legal topics are education, not advice" |
| 16 | `app/services/simi_service.py:343` (Simi system prompt) | disclaimer: "This is advisory only." | advisory, and a disclaimer pasted into a prompt | Becomes the referral line from the legal-text source |
| 17 | L4 "Automated residual" in other places: `home.js:5,146`, `marketing/transition_package.html:84`, `admin/calibration.html:177`, `simulations/_wealth_world.html:324`, `base.html:226` (feedback modal), `admin/feedback.html:115`, `models/feedback.py:9`, `models/observability.py:77`, `world_service.py:208`, `artifacts/routes.py:499`, `layer6/routes.py:1412`, `orchestrator_diagram.js:578`, `guide_service.py:34,418` | "Automated Residual" | residual (flag) | User-facing copy becomes "Recurring income" per D5. Internal keys and stored feedback categories stay |
| 18 | Samples `samples/simulacrum_usecase_maya.html` and `…_marcus.html` | "$73,400 income expansion", "$168,200 in new income", "$112k → $280k year 1" | case-study results | Label "Illustrative case study: a composite example, not a real customer's results" (A5). There is no such label today (0 hits) |
| 19 | Home page hero chart (`landing.html`, `home.js`) | single values per layer ("$12.4k a month"), and a tooltip "Total a month" | single headline numbers (A4) | It is already captioned "A sample simulation… Illustrative". Change the end labels and tooltip to ranges, or keep the values and add "Illustrative estimate" next to the figures |
| 20 | `config/agents.json:2003` (L3 sales-page agent output_spec) | "FAQ and guarantee" | guarantee (in *user's* product copy) | "FAQ and refund policy". The agent shouldn't push the user toward offering guarantees it can't define |

No hits for "effortless", "automated wealth", "get rich", "replace your salary", "quit your job" or "financial freedom".

**Also found:** `app/services/email_service.py:194` `send_invoice_email(..., amount_cents=69500)` and `app/tasks/email.py:20,32` (`amount_cents=1000`) carry hardcoded price defaults. Hard rule 3 says no hardcoded prices, so these become required arguments.

### A3. Banned-phrase test
- **Phrase list:** `config/compliance_phrases.json`, with `banned` (fails the build) and `review` (warns, e.g. "residual").
- **Test:** `test_compliance_phrases.py` renders every public and app page through the test client (the list from my site audit), plus:
  - every email body built by `email_service` and `outreach_campaign_service` (rendered with dummy tokens);
  - every agent system prompt, built by `agent_briefing` for every agent in `agents.json`.

  It fails on any banned phrase. A seeded phrase proves it can fail.

### A4. Where projected income is rendered today

| Surface | File:line | Today | Change |
|---|---|---|---|
| Simulation detail, stream badge | `templates/simulations/detail.html:294-295` | `$low–$high/mo` | Estimate component |
| Live generation stream | `static/js/sse.js:96` | `$low–$high/mo` | JS twin of the component |
| Bio page income table and totals | `templates/public/bio_page.html:2705-2744` | ranges, plus a summed total range | JS twin. The total says "Estimate" |
| Partner advisor view | `templates/partners/advisor_view.html:47-48` | range | Component |
| Export / print | `templates/simulations/export.html:32` | range | Component (print variant) |
| Calibration cards and summary | `templates/simulations/_calibration_cards.html` (JS) | range, with source and confidence tier | Already ranges with a source. Add the "Estimate, not a promise" label and the L3/L4 note through the component |
| Wealth Engine World | `world_service.py` / `_wealth_world.html` | stream ranges | Component (JS twin) |
| Home hero chart, sample runs | `landing.html`, `home.js` | illustrative values | See A1 #19 |
| GCC income tab, share-ROI, corporate cohort | `layer6.html:1276,1530,1563`; `share_roi.html:9,48,56`; `corporate/dashboard.html:70` | **recorded actual** income (not projections) | Not an estimate. Label it "Self-reported" (D6). Share-ROI gets the typical-results disclosure |
| Agent prompts | `agent_briefing.py`, `calibration_service.py` | prompts that produce income figures | Add a prompt rule: "present every income figure as a low–high range with its basis; never a single number" |

**The component:** `partials/_estimate.html`, a macro `estimate(low, high, unit='mo', basis=None, source=None, layer=None)`. It renders:
- the range;
- the label "Estimate, not a promise";
- a "How we estimated this" disclosure (assumptions and source, one click away);
- for L3 and L4, the base-rate note.

It has a JS twin, `static/js/estimate.js`, with the same markup, for the pages that build ranges client-side. All text comes from the legal-text source. The guard test greps the simulation templates and JS for `est_monthly_low`/`$…/mo` patterns outside the component and fails on any.

### A5. Testimonials and case studies
- **Home page voices:** the testimonials come from `UserFeedback` with status `approved` (real, approved). Add the typical-results disclosure under any quote that mentions money, clients or results (a keyword check on the quote).
- **Bio page testimonials:** these are written by the owner (`settings` bio editor, `bp-testimonial-item`), so they are the user's own marketing. Show the same disclosure when a quote mentions money or results, using the user-facing variant.
- **Illustrative labels:** the Maya and Marcus samples, the home page's sample runs, and the Playbook case studies on the home page get the "Illustrative" label from the legal-text source.
- **No unapproved reviews:** the voices block already renders only approved feedback. Add a test that seeds a pending review and asserts it never renders.

### A6. Outcome check-ins
- **New table:** `outcome_checkins` (`generate_pk`) with `user_id`, `simulation_id`, `checkin_day` (30/90/180), `income_gained_range` (bands such as `0`, `<1k`, `1–5k`…), `first_client`, `job_landed`, `notes` and `created_at`. Unique on (`simulation_id`, `checkin_day`).
- **Prompt:** a dashboard card when a completed simulation reaches day 30, 90 or 180 without a check-in. It can be dismissed, and "remind me later" snoozes it.
- **Admin:** aggregate counts only.
- **No public use** until counsel approves the presentation. It's a code comment and a test asserts no public template reads the table.
- **Migration:** `sql/064_compliance.sql` (all new tables in one file).

---

## 2. Workstream B: investment advice (Layer 5)

### Layer 5 agents and where their prompts come from
**Agents** (from `config/agents.json`, all `disclaimer: true`):
- `income_allocation`
- `projections`
- `fund_recommendations` (D3)
- `investment_policy`
- `real_estate_strategy`
- `entity_structure`
- `tax_optimization`
- `dca_schedule` (D4)
- `insurance_review`
- `estate_planning`

**Legacy aliases in `app/services/claude.py:800-940`:**
- `portfolio_analysis`
- `compound_growth`
- `investment_policy_statement`
- `insurance_gap_analysis`
- `investment_thesis`

**What the prompt is built from:**
1. `config/agents.json`: `description`, `description_template`, `output_spec`, `external_facts`. For example, `fund_recommendations` says "tickers and expense ratios" and "Curate 8–12 recommended funds".
2. `app/services/claude.py:222-223`: the layer definitions (A1 #5).
3. `app/services/agent_briefing.py:372-376`: the disclaimer rule. **It is pasted as text in code**, which breaks hard rule 2, so it moves to the legal-text source.
4. `app/services/claude.py:800-940`: the alias forms.

### B1. "Advisory" becomes "education and planning"
The user-facing hits are A1 #13–16. Internal identifiers stay unchanged (`badge-advisory` CSS and the SME "advisory" channel).

### B2. Guardrails in every Layer 5 prompt
One function, `l5_rules()`, is added to the system prompt for every agent with `layer == 5` (and the aliases) in `agent_briefing.agent_rules`. Its text comes from the legal-text key `l5_agent_rules`. It requires:
- no named securities, funds, tickers, crypto assets or products;
- no instruction to buy, sell or allocate a specific amount (scenarios framed as "a common approach is…" or "in a scenario where…");
- no tax-filing or legal advice (explain concepts and list questions for a professional);
- the output ends with the referral line from the legal-text source (fiduciary adviser, CPA or attorney, by agent).

The output specs for `fund_recommendations` and `dca_schedule` are rewritten per D3 and D4.

### B3. Output filter
**Reuse the existing guardrail engine** (`app/services/guardrail_service.py`, OBS-001). It already evaluates `output_filter` and `content_policy` rules post-agent, and a `block` holds the artifact. New code:
- **`compliance_filter.check_l5(text)`.** It flags tickers (`$[A-Z]{1,5}` and a ticker list in `config/compliance_phrases.json`, matched as whole words near fund or ETF context), fund family and product names (Vanguard, Fidelity, SPDR, iShares, Schwab, ARK, …), crypto assets (BTC, ETH, …), and imperative trade language ("buy", "sell", "move $X into", "put N% in", "rebalance into <named fund>").
- **The hook.** It goes in `claude.execute_agent_action` for Layer 5 actions, before the artifact is saved:
  1. On a hit, regenerate once with a stricter prompt (`l5_rules_strict`).
  2. On a second hit, save the safe fallback message (legal-text `l5_fallback`) instead.
  3. In both cases, write a `compliance_events` row (`generate_pk`): `user_id`, `agent`, `rule`, `excerpt` (300 characters, contacts masked) and `created_at`.
- **Admin:** a "Compliance events" list on `/admin/observability` (read-only).

### B4. Brokerage
- **What's there.**
  - Alpaca connect only calls `GET /v2/account` (`app/blueprints/integrations/routes.py:1601-1617`).
  - `alpaca_order` is in the tool registry with execution disabled (`agent_plans.py:48`, "never in v1"). `plan_executor.EXECUTABLE_TOOLS` covers only Stripe links and Cal.com.
  - **No code path places an order.**
- **But the UI says otherwise.**
  - Settings shows an **"Enable automated trading"** toggle with consent text: "I understand that SimiSI will place trades in my brokerage account" (`settings/index.html:1697-1707, 4228-4251`).
  - It also has a "DCA amount" field.
  - The toggle posts to `/api/integrations/alpaca/fintech-toggle` (`integrations/routes.py:1676-1707`).
  - `fintech.py` holds the dead allocation code.
- **Change.**
  - Remove the toggle, the consent text, the DCA config and the endpoint.
  - Make Alpaca strictly read-only ("compare your plan with your account balances") or remove it entirely; I propose keeping read-only.
  - Remove `alpaca_order` from the tool registry and the `alpaca`→`alpaca_order` map (`agent_execution.py:76`).
  - Delete `fintech.py` and its admin toggle (`fintech_toggle`).
- **Test.** `test_no_brokerage_orders.py`:
  - asserts no source file references `/v2/orders`, `submit_order` or `orders`, and that `alpaca_order` isn't registered;
  - patches `requests` during a full Layer 5 agent run plus the Alpaca connect route, and asserts that no URL containing `/orders` is requested.

### B5. Admin kill switch for Layer 5
- **Same pattern as Email-to-CRM:** a `FeatureFlag` (`feature_flags` table) with key `layer5_agents`, mode `on` or `off`, fails closed, managed by a small service like `mail_feature`.
- **When it's off:**
  - Layer 5 agents aren't dispatched by the orchestrator, can't be run from the UI, and the setup selector shows them as unavailable;
  - existing artifacts stay viewable, with a "temporarily unavailable" banner.
- **Admin card:** on `/admin/observability` (or Platform), with a reason field and an audit-log entry.

---

## 3. Workstream C: outreach rules

### Send paths (third-party, commercial)

| Path | Code | Opt-out link and headers | Suppression checked | DNC | Postal address | Caps |
|---|---|---|---|---|---|---|
| Core sender (every agent email) | `outreach_email_service.send_outreach_email` | **only when `unsubscribe=True`** | global `EmailSuppression` ✓; per-user `UserSuppression` **✗** | ✓ | SimiSI city only | ✗ (recipient ledger only) |
| P5 confirmed batch ("Send to contacts") | `send_pipeline.send_batch` (`:268-272`) | ✓ (`unsubscribe=True`) | ✓ (pre-check in `artifact_actions.send_refusal`) | ✓ | city only | ✗ |
| Scheduled follow-ups and steps | `action_step_service.execute_step` (`:378`, `:444`) | **✗** | via the core sender | ✓ | ✗ | ✗ |
| Consulting outreach (orchestrator) | `consulting_outreach_service._try_apollo_send` (`:474`), sending through SimiSI's SendGrid | **✗** | ✓ plus a pre-check at `:446-460` | ✓ | ✗ | ✗ |
| Sponsorship outreach | `sponsorship_outreach_service.py` (~`:298`) | to verify | to verify | to verify | ✗ | ✗ |
| Artifact send from a simulation | `simulations/routes.py:988` `send_agent_artifact` | to verify | to verify | | | |
| Partner referral invitation | `email_service.send_referral_invitation_email`, `partners/routes.py:464` | **✗** | ✗ | n/a | ✗ | ✗ |
| SimiSI drip and broadcasts (to SimiSI's own users) | `outreach_campaign_service.send_now` (`:622`) | ✓ (its own unsubscribe) | ✓ | | SimiSI city only | — |
| ConvertKit broadcasts | step type `convertkit_broadcast` | ConvertKit's own (the user's account) | n/a | | the user's ConvertKit | the user's ConvertKit |

**Transactional and relationship email** (verification, password, invoices, collaborator invites, bio-privacy approvals, transition invites) is out of scope for the commercial-email rules. I'll leave it as is and list it in the plan for counsel's confirmation.

**Already enforced:**
- **Do Not Contact** at send time.
- **Global suppression:** bounces, drops and spam reports go to `EmailSuppression` through `/sendgrid` events (`integrations/routes.py:2021-2105`). The unsubscribe link (`pages/routes.py:980`) writes it immediately.
- **Recipient ledger:** one run per person.
- **Replies:** inbound parse sets `EmailLog.replied_at`, and `no_reply` follow-ups check it (`action_step_service.py:313-331`).
- **Email-to-CRM switch:** turning it off marks contacts DNC and cancels their scheduled steps (`mail_feature.py`).
- **Sandbox routing.**

**Gaps and the change for each:**
- **C1, opt-out and the honest From line.**
  - `send_outreach_email` becomes `unsubscribe=True` by default for every agent and commercial send (an explicit `transactional=True` is needed to skip it).
  - The footer adds "Reply STOP to opt out". Inbound parse treats a reply that is just "stop", "unsubscribe" or "remove" as an opt-out: suppression plus DNC.
  - The From line stays honest: "<User Name> <simi@simulacrumai.io>", with the footer "Sent by <name> via SimiSI".
  - A subject check rejects "RE:" or "FWD:" on a first-touch email (deceptive subjects).
- **C1, postal address (D7).**
  - `users.postal_address` (encrypted) is set in Settings, then Notifications or Sending.
  - Every commercial send path checks it: the core sender refuses with `reason='no_postal_address'`, the batch confirm shows "Add your mailing address in Settings to send", and the orchestrator raises a GCC action item once.
  - The footer uses the user's address.
  - SimiSI's own address moves to the Admin setting `outreach_business_address`.
- **C2, one suppression check.** `suppression_service.is_suppressed(user_id, email)` combines global `EmailSuppression`, per-user `UserSuppression`, contact DNC and the Email-to-CRM `ContactDncFlag`. It is called by the core sender, the batch pre-check, the consulting pre-check, CSV, Apollo and mail imports (mark DNC on import), and partner referrals. A test mutates each source and asserts the send is skipped.
- **C3, caps, warm-up and auto-pause.** Admin settings with defaults:

  | Setting | Default |
  |---|---|
  | `send_cap_per_user_day` | 50 |
  | `send_cap_per_campaign_day` | 25 |
  | `warmup_schedule` (sends per day for the first 14 days) | `10,15,20,25,30,40,50` |
  | `pause_bounce_rate_pct` | 5 |
  | `pause_complaint_rate_pct` | 0.3 |
  | `pause_min_sample` | 20 |

  - **Counting:** from `EmailLog` (per user, per `action_id`, per UTC day).
  - **Warm-up:** keyed on the user's first outreach send, or `UserIntegration.warmup_started_at` for an own-SendGrid sender.
  - **Auto-pause:** a campaign (agent action) whose 7-day bounce or complaint rate crosses a threshold is paused. Its pending steps are cancelled, and a GCC action item plus a notification tell the user why, with the numbers.
  - **Over the cap:** a step is deferred to the next day rather than dropped.
- **C4, import source.**
  - The CSV import (`contacts/routes.py:384`) and the admin CRM import require a declared source: "My own contacts / clients", "Event or meetup attendees who agreed", "Referrals", "Purchased or rented list" (**blocked**) or "Scraped from the web" (**blocked**).
  - The declared source is stored in `source_notes`, and `source` stays `csv_import`.
  - Apollo and mail imports keep their `source` (already set).
- **C5, follow-ups.**
  - Reply: enforced in code ✓.
  - Bounce, opt-out and complaint: they hold indirectly through suppression at send time. Add an **explicit** check in `evaluate_condition` for every follow-up step (any condition type: `no_reply`, `no_purchase`, `no_signature`, `no_booking`). It skips the step if the contact has replied, bounced, opted out or been marked DNC since the step was created.
  - `no_purchase` and `no_signature` follow-ups currently keep going after a reply, which is a gap.
- **C6, LinkedIn.**
  - **Found:** an approve-then-publish path. `/api/integrations/linkedin/post` queues `SocialPostQueue`, and the approve route calls `linkedin.post_ugc` (`integrations/routes.py:1303-1420`, `services/linkedin.py:65`).
  - The OAuth scope requests `w_member_social` (`linkedin.py:13`).
  - The `linkedin_post` tool is disabled ("attorney review"). The action-step type `linkedin_post` is a no-op log (`action_step_service.py:392`).
  - No connecting, liking or scraping code was found; `crawl_profile` only reads the user's own OIDC userinfo.
  - **Change:** remove the publish call (the approve route marks the item "Ready to post" and shows **Copy text** plus **Open LinkedIn**). Drop `w_member_social` from the default scope. Remove the `linkedin_post` step type, and remove the tool from the registry. The `layer6` planner's `channel_approvals.linkedin` stays a draft-only channel.
- **C7, audit log.** Every commercial send (sent or skipped) writes `AuditLog.log('outreach_send', user_id, resource_id=email_log_id or contact_id, metadata={...})`. The metadata holds:
  - `path`
  - `unsub_version` (a constant bumped when the footer or link format changes, e.g. `unsub-v2`)
  - `suppression` (`clear` or the reason)
  - `cap_check`
  - `postal_address_set`

  `EmailLog` keeps the delivery record.

### Reused, not rebuilt
- **Email-to-CRM switch:** `mail_feature.py` with `FeatureFlag`, `UserFeatureOverride` and `ContactDncFlag`. This is the pattern for B5, and its DNC flags feed C2.
- **Audit log:** `app/models/audit_log.py` (`AuditLog.log(...)`, used by Admin today).
- **Guardrail engine:** `guardrail_service.py`, for B3.
- **Recipient ledger and global suppression:** unchanged, both called through the new `suppression_service`.

---

## 4. One source for legal text (hard rule 2)
- **New table:** `legal_texts` (`generate_pk`) with `key`, `body`, `status` (`draft` / `counsel_approved`), `version`, `updated_by` and `updated_at`. Defaults are seeded from `app/services/legal_text.py`. Every default is `status='draft'`. **Nothing in code sets `counsel_approved`.** Only an admin can, from Admin → Legal text, after counsel signs off.
- **Access:** `legal_text.get(key)` for Python (prompts, emails, the filter fallback), and a Jinja global `legal('key')` for templates.
- **Admin page:** `/admin/legal-text`. It lists every key, its draft and approved status, and an edit history in the audit log.
- **Keys** (texts in COUNSEL_REVIEW):

  | Key | Use |
  |---|---|
  | `estimate_label` | the "Estimate, not a promise" label |
  | `estimate_basis_intro` | intro to the "How we estimated this" disclosure |
  | `base_rate_l3_l4` | the base-rate note for layers 3 and 4 |
  | `typical_results` | the disclosure under results testimonials |
  | `illustrative_label` | label for illustrative case studies |
  | `self_reported_label` | label for user-recorded income |
  | `l5_banner` | the Layer 5 framing |
  | `l5_referral_adviser` | referral line: fiduciary adviser |
  | `l5_referral_cpa` | referral line: CPA |
  | `l5_referral_attorney` | referral line: attorney |
  | `l5_agent_rules` | Layer 5 prompt rules |
  | `l5_agent_rules_strict` | stricter rules for the regeneration |
  | `l5_fallback` | the safe fallback message |
  | `email_footer` | the commercial email footer |
  | `email_reply_optout` | the reply-to-opt-out line |
  | `postal_address_required` | the "add your mailing address" message |
  | `import_source_blocked` | the purchased-list block message |
  | `campaign_paused` | the auto-pause message |
  | `linkedin_manual` | the copy-and-post-by-hand message |
  | `share_roi_disclosure` | the disclosure on the share-ROI card |

---

## 5. Ordered edits (after approval). One commit each; tests after each.

| # | What | Files | Risk | Verify |
|---|---|---|---|---|
| 1 | `generate_pk()`; legal-text table, service, Jinja global and admin page; migration `064_compliance.sql` (all new tables) | `utils/id_gen.py`, `models/legal_text.py`, `services/legal_text.py`, admin template and routes, `sql/064` | Low | Unit tests; render the admin page |
| 2 | Banned-phrase config and test (passes once step 3 is done; a seeded phrase fails it) | `config/compliance_phrases.json`, `test_compliance_phrases.py` | Low | Run with and without the seeded phrase |
| 3 | A1 copy replacements (drafts), layer display names (D5), voice script, drip templates (plus a re-seed of the defaults only where they are unedited), `fintech.py` removal | per the A1 table | Medium (many templates) | Phrase test green; full site render audit |
| 4 | Estimate component and JS twin; replace every income render; guard test | `partials/_estimate.html`, `static/js/estimate.js`, the A4 files | Medium | Guard test; 390 px render; no console errors |
| 5 | Testimonial disclosure, Illustrative labels, share-ROI (D6) | `landing.html`, `bio_page.html`, samples, `share_roi.html` | Low | Render tests |
| 6 | Outcome check-ins: model, dashboard prompt and API | `models/outcome.py`, `dashboard/index.html`, pages route | Low | Day-30 prompt test; no public reads |
| 7 | Layer 5 prompt rules (B2), agent spec reframes (D3, D4), disclaimer moved to the legal-text source | `agent_briefing.py`, `agents.json`, `claude.py` | Medium | Prompt snapshot tests; "which stock should I buy" test with a fake client |
| 8 | Layer 5 output filter and `compliance_events` (B3) | `services/compliance_filter.py`, `claude.execute_agent_action`, model | Medium | Seeded ticker caught → regenerate → fallback and event |
| 9 | Brokerage clean-up (B4) | settings template, integrations routes, `agent_plans`, `agent_execution`, `fintech.py` | Low | `test_no_brokerage_orders.py` |
| 10 | Layer 5 kill switch (B5) | `services/l5_feature.py`, orchestrator dispatch, run routes, setup selector, admin card | Medium | Switch off → no dispatch or run; artifacts still viewable |
| 11 | Single suppression check (C2) across every path | `services/suppression_service.py`, core sender, batch, consulting, imports, referrals | Medium | Each source suppresses a send |
| 12 | Opt-out by default, reply-STOP, postal address (C1, D7) | core sender, inbound parse, settings, user model, admin setting | Medium | No address → blocked; footer and headers present |
| 13 | Caps, warm-up, auto-pause (C3) | `services/send_limits.py`, core sender, step executor, admin settings | Medium | Cap defers; thresholds pause and notify |
| 14 | Import source declaration (C4) | contacts import, admin CRM import, UI | Low | Purchased list → 400 |
| 15 | Follow-up stop conditions (C5) | `action_step_service.evaluate_condition` | Low | Reply, bounce or opt-out → skipped for every condition type |
| 16 | LinkedIn: remove the publish path and scope (C6) | integrations routes, `linkedin.py`, `action_step_service`, `agent_plans` | Low | No `post_ugc` reachable; copy UI |
| 17 | Audit logging per send (C7) | core sender, batch | Low | Log rows with `unsub_version` and the suppression result |
| 18 | Hardcoded price defaults removed (rule 3) | `email_service.py`, `tasks/email.py` | Low | Invoice email takes the charged amount |
| 19 | The D1 approval default | `models/layer6.py`, `layer6/routes.py`, GCC settings copy | **High (behaviour change)** | New simulations: nothing sends until opted in |
| 20 | Full suite, site render and contrast audit at 390 and 1280 px, zip | — | — | — |

### Where the build differs from this plan
- **Admin page.** The Layer 5 switch, commercial-email limits, compliance events and outcome aggregates live on a new **Admin → Compliance** page (`/admin/compliance`), not in the observability console. Legal text is edited at **Admin → Legal Text**.
- **Layer 5 simulation narrative.** Besides agent outputs (step 8), the simulation's own Layer 5 layer is filtered too. It is regenerated once with the strict rules; if it still names a ticker or product, the offending sentences are **redacted** (not replaced wholesale with the fallback), so the rest of the planning narrative survives. Each catch is a `compliance_events` row.
- **Caps defer, they don't drop.** Over a daily or warm-up cap the core sender returns `status: 'deferred'`; the step runner reschedules the step for 01:00 UTC the next day and counts it as held. A paused campaign returns `skipped`.
- **Old drip defaults.** Unedited copies of the previous drip templates are recognised by hash and served as the new defaults; edited ones are left alone.
- **API path.** Outcome check-ins post to `/api/outcome-checkins` (`/api/outcomes` was already taken by calibration).
- **Suppression labels.** The single check returns `global` / `member` / `do_not_contact` / `dnc_flag`; `global` shows as "Unsubscribed, bounced or complained".
- **Follow-up stops.** A step whose contact no longer exists is skipped ("Contact no longer exists") rather than sent.
- **LinkedIn (step 16).** Removed: `post_ugc`, the `w_member_social` scope, the `linkedin_post` step type, tool and integration mapping (sql/064 deletes the registry row), and the GCC "LinkedIn messaging" toggle. Approving a queued post now marks it ready and shows **Copy text** and **Open LinkedIn**. LinkedIn's own share-offsite buttons (bio editor, onboarding) stay; the member posts from LinkedIn's dialog.
- **Audit (step 17).** One `outreach_send` AuditLog row per attempt on the agent/step sender and on SimiSI's own drip/broadcast sender; admin test sends are not included.
- **Prices (step 18).** `stripe_service.create_payment_intent` also had a 69500 default; all three call paths now need the real amount.
- **D1 (step 19).** New configs default to `review_all` with email and email funnels off, and AI-suggested settings can no longer pre-tick them. sql/064 writes the old defaults onto existing configs that had none stored, so live simulations keep sending as before.

**Testing:** all through the Flask test client with fakes for SendGrid, Anthropic and Alpaca, and Playwright on pages rendered by the test client. No local server.

**Deploy (later):**
- run `sql/064_compliance.sql`;
- set the SimiSI postal address in Admin → Compliance (SimiSI's own drips and broadcasts hold until it is set);
- the Layer 5 switch defaults to on; check it in Admin → Compliance;
- LinkedIn: the app no longer requests `w_member_social`; members reconnecting get the identity scope only;
- users must add a postal address before campaigns send again. That is a visible behaviour change; GCC shows an action item.
