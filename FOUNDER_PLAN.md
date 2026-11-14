# Founder Track (SIM-PRD-FND-001): build spec

The source PRD is `docs/SIM-PRD-FND-001 SimiSI Founder Track.docx`; a text copy is at `.ds_tmp/fnd/prd.md`. The reuse map behind this spec is `.ds_tmp/fnd/synthesis.md`, with per-area reports in `.ds_tmp/fnd/report_*.md`.

This file is the single spec every work package follows. Where it differs from the PRD, this file wins, and the reason is given.

## 0. Owner decisions (2026-11-13)

| Topic | Decision |
|---|---|
| Scope | Phases 1–3. Phase 3 (investor connection) ships behind the `founder_investor` switch. The switch refuses to turn on until an admin records the counsel sign-off date (`fnd_counsel_signoff_at`). |
| Orchestration | A separate founder dispatcher reuses the Layer 6 *algorithms* (`layer6._count_unblocked`, `_ensure_layer_diversity`, `_beta_mean`/`_compute_posterior`), the observability hook and guardrails. It **never** calls `run_orchestrator_cycle`, creates `Simulation`/`Layer6Config` rows, or adds to `config/agents.json`. |
| Founder plan UI | A new workspace at `/founder/v/<venture_id>` (Growth Command Center look, its own SSE stream). The existing GCC is untouched. |
| Fee model | One-time Stripe Checkout. `fnd_fee_mode` is stored with the value `one_time`; `recurring` is rejected until it is built. |
| Free preview | Free: the evidence rung, a score band per rubric dimension, and counts of findings by type and severity. Locked: finding text, citations, the team builder, the plan and the agents. The cut-off is an admin setting `fnd_preview_level`: `rung`, `scores` (default) or `top3` (scores plus the 3 most severe findings in full). |
| Uploads | PDF and DOCX only, reusing `resume_parser`. The global `MAX_CONTENT_LENGTH` rises from 5 MB to 20 MB (owner accepted; this affects every upload). |
| Startup page design | **Light, close to the member bio pages**, built on the design system (`system.css`, Archivo, navy/gold tokens). It must still read as an investor page: memo-style header with stage and date, a key-metric strip, the evidence ladder as a 5-rung visual, investor section order, and a "Request an introduction" panel. Its own stylesheet `static/css/startup.css` is scoped under `.st-root`. |
| Investor data | An admin-curated list (`investor_profile`, CSV import at `/admin/founder`) plus investors the founder adds. No third-party vendor. |
| Intro sending (Phase 3) | Sent through SimiSI's existing compliant send path (SendGrid). The founder's name is the display name, `reply_to` is the founder's own address, and the email is labelled as sent on the founder's behalf by SimiSI. This deviates from the PRD's "from the founder's own account", which the owner approved. Every send is approved under the autonomy matrix. |
| Default prices | Founder Track 49900 cents, add-on (existing SimiSI users) 29900 cents, investor access 4900 cents per startup page, refund window 14 days. All are admin settings. |
| Regression gate | Existing tests keep passing. The only existing tests edited are `test_delete_user_sql.py` (new tables classified), `test_admin_subnav.py` (one new page) and `test_footer.py` (if the startup page uses the public footer). These are additive test maintenance. |

## 1. Hard rules (every work package)

1. **No change to existing behaviour.** Don't edit existing routes, templates, tables, the 49 wealth agents (`config/agents.json`, `agent_registry`, `config/agent_execution.json`, `utils/model_router._ROUTING`), simulation pricing, `guide_service` (`TURN_TOOL`, `validate_turn`, `_system_prompt`, `LAYER_*`), the existing GCC stream, `send_limits.DEFAULTS`, `legal_text.email_footer`, `profile._RESERVED` or `GOOGLE_SCOPES`.
   - An unavoidable edit to shared code must be **additive and opt-in**, with the default keeping today's behaviour. List every such edit in your report.
2. **New tables** use `utils.id_gen.generate_pk()` (9-character uppercase alphanumeric primary keys). Column names must avoid `session_id`, `run_id` and `step_id`; use `plan_step_id` and `obs_run_id`.
3. **Python 3.9 in production.** Every new `.py` file starts with `from __future__ import annotations`. No `match`, no 3.10+ APIs.
4. **No hardcoded prices, limits or legal text.**
   - Prices and limits come from `founder_config` (admin settings with defaults).
   - Disclaimers come from `legal_text` keys, rendered with `legal('key')` or `legal_text.get(key)`.
5. **Founder control.**
   - Nothing reaches a customer or an investor without approval at the founders' level.
   - Agents never move money, negotiate or accept terms.
   - Agents give no legal, tax or investment advice.
   - Agents never project investor returns.
6. **Honest evaluation.**
   - Every finding cites its source passage and states its confidence.
   - Reports carry the `founder_report_disclaimer` legal text ("not a funding prediction").
   - Copy must pass `test_compliance_phrases.py`: no "guaranteed", "passive income" and so on.
7. **Gmail Limited Use.** No contact or mail data that came from an email import reaches an LLM (`source='email_import'` or a `ContactAccountLink` row). Warm-intro matching over those contacts is deterministic code only.
8. **Never start a local server.** Tests use the Flask test client in house style: root-level `test_founder_*.py` scripts with `check()`, in-memory SQLite, a faked Anthropic client (`gs.AI_CLIENT` style, or `claude._client` patching), SendGrid faked by patching `sendgrid.SendGridAPIClient`, and Stripe faked. The network is blocked (patch `requests.get`/`requests.post` to raise).
9. **Feature flag.** With `founder_track` off (the default), every founder route returns 404 and nothing in the existing UI changes. The test user path in existing suites must be identical.

## 2. Switches and settings

`app/services/founder_feature.py` copies `guide_service.mode()/is_enabled()/set_mode()/set_user_access()`, using `FeatureFlag` and `UserFeatureOverride`:
- `founder_track`: modes `off`/`allowlist`/`on`, **default `off`**. Admins bypass it unless the mode is `off`.
- `founder_pitch`: the spoken pitch. Default `on`; it only matters when `founder_track` is enabled.
- `founder_investor`: Phase 3. Default `off`. `set_mode` refuses `on`/`allowlist` unless `founder_config.get('fnd_counsel_signoff_at')` is set (raises `FounderError`).
- Public `/startup/<slug>` pages render only when `mode('founder_track') != 'off'`; otherwise 404.

`app/services/founder_config.py` holds `DEFAULTS`, typed `get(key)`, `set(key, value, admin_id)` with validation, and `all_settings()`. Values live in `PlatformSetting`, with keys prefixed `fnd_`. Keys:
- Rubric: `fnd_rubric_weights` (JSON for the nine dimensions, default equal weights).
- Readiness gates: `fnd_gate_rung_applications` (2), `fnd_gate_rung_intros` (4).
- Next steps: `fnd_next_alternates` (3).
- Preview: `fnd_preview_level` (`scores`).
- Prices: `fnd_price_cents` (49900), `fnd_addon_price_cents` (29900), `fnd_investor_access_cents` (4900), `fnd_refund_window_days` (14), `fnd_fee_mode` (`one_time`).
- Agent caps: `fnd_action_cap_per_day` (10), `fnd_budget_cap_cents` (0).
- Autonomy: `fnd_autonomy_matrix` (JSON per action type: default level and maximum the founder may raise to, from PRD §Investor connection) and `fnd_first_contact_cap` (3).
- Targets: `fnd_targets` (JSON per PRD success metric: `eval_completion`, `finding_accuracy`, `paid_poc_rate`, `step_completion_per_week`, `intro_acceptance`, `application_submission`, `alignment_agreement`).
- Alerts: `fnd_alert_error_rate_pct` (20), `fnd_alert_cost_per_venture_cents` (2000), `fnd_alert_stuck_days` (14).
- Retention: `fnd_retention_days` (365).
- Counsel: `fnd_counsel_signoff_at` (empty).

## 3. Data model

New file `app/models/founder.py`, imported in `app/models/__init__.py`. Migrations `sql/069`–`sql/074`, MySQL InnoDB utf8mb4 in house style. All primary keys are `CHAR(9)` from `generate_pk`. Unless noted, each table has `created_at DATETIME NOT NULL`.

**069_founder_core**
- `founder_venture`
  - Identity: `id`, `owner_user_id` (UNIQUE: one owned venture per account), `name`, `one_liner`.
  - Lifecycle: `stage` (idea/pre-seed/seed/series-a), `status` (active/paused/archived), `rung` INT default 1, `track` (standalone/addon).
  - Payment: `paid_at`, `payment_intent_id`, `amount_cents`.
  - Controls: `paused_at`, `support_access_until`.
  - Team: `checkin_cadence_days` INT null.
  - Timestamps: `updated_at`.
- `venture_member`: `id`, `venture_id`, `user_id` null, `invite_email`, `invite_token` (UNIQUE null), `role` (owner/cofounder), `title`, `status` (invited/active/left), `joined_at`.
- `venture_artifact`: `id`, `venture_id`, `kind` (startup_plan/exec_summary/ops_plan/pitch_deck/financial_model/pitch_transcript), `version` INT, `is_current` BOOL, `filename`, `file_path`, `text_content` LONGTEXT, `char_count`, `uploaded_by`.
- `founder_run_meta`: `id`, `obs_run_id` (UNIQUE, points to `agent_runs.id`), `venture_id`, `plan_step_id` null, `agent_key`, `trigger` (dispatch/founder/schedule/rerun), `autonomy_level` null, `approved_by` null, `prompt_version`, `cost_cents`.

**070_founder_eval**
- `evaluation_run`: `id`, `venture_id`, `artifact_versions` JSON, `status` (running/done/failed), `scores` JSON (dimension → 0–100), `overall_score`, `rung_at_run`, `obs_run_id`, `cost_cents`, `started_at`, `finished_at`.
- `evaluation_finding`:
  - Identity: `id`, `venture_id`, `evaluation_run_id`, `artifact_id` null, `dimension` (problem/customer/market/solution/gtm/business_model/operations/team/fundraising), `layer` 1–5.
  - Classification: `ftype` (gap/contradiction/assumption/strength), `severity` (low/medium/high), `title`, `detail`.
  - Confidence: `alpha` FLOAT, `beta` FLOAT, `confidence` FLOAT.
  - Citation: `citation_text`, `citation_ref` (artifact kind and version).
  - Linking: `recommended_agent` null, `superseded_at` null.
- `finding_rating`: `id`, `finding_id`, `user_id`, `fair` BOOL, `comment`.
- `founder_eval_cache`: `id`, `artifact_id`, `dimension`, `payload` JSON (UNIQUE `(artifact_id, dimension)`).

**071_founder_plan**
- `agent_blueprint`: `id`, `venture_id`, `agent_key`, `layer`, `status` (recommended/accepted/declined), `finding_id` null, `params` JSON (defaults).
- `agent_param_override`: `id`, `venture_id`, `blueprint_id` null, `layer` null (a layer-wide override), `param_key`, `previous_value`, `new_value`, `user_id`.
- `venture_plan_step`:
  - Identity: `id`, `venture_id`, `layer`, `kind` (agent/manual/alignment/proof/legal), `agent_key` null, `title`, `description`.
  - State: `state` (locked/ready/in_progress/awaiting_approval/done/skipped).
  - Ordering: `prereq_keys` JSON, `effort` INT 1–5, `sort_order`.
  - Ownership: `owner_member_id` null.
  - Completion: `output_id` null, `evidence_note`, `skip_reason`, `completed_at`, `updated_at`.
- `venture_step_event`: `id`, `venture_id`, `plan_step_id`, `from_state`, `to_state`, `actor_user_id` null, `actor_kind` (founder/agent/system), `note`.
- `venture_agent_output`: `id`, `venture_id`, `plan_step_id`, `agent_key`, `content` LONGTEXT, `version` INT, `obs_run_id`.
- `proof_plan`: `id`, `venture_id`, `customer_name`, `pain`, `baseline`, `smallest_build`, `success_criteria` JSON, `duration`, `provides` JSON, `on_success`, `status` (draft/agreed/complete), `agreed_at`.
- `proof_result`: `id`, `venture_id`, `proof_plan_id`, `results` JSON, `measured` BOOL, `benefits_text`, `quote_text`, `quote_permission` BOOL, `paid_at` null.

**072_founder_team**
- `alignment_response`: `id`, `venture_id`, `member_id`, `question_key`, `answer` TEXT, `updated_at`. UNIQUE `(member_id, question_key)`.
- `decision_right`: `id`, `venture_id`, `decision_type` (publish_page/investor_contact/customer_contact/spend/hiring/product/legal), `layer` null, `holder_member_id`.

**073_startup_page**
- `startup_page`:
  - Identity: `id`, `venture_id` UNIQUE, `slug` UNIQUE, `status` (draft/held/published/unpublished).
  - Header: `name`, `one_liner`, `logo_path`, `stage`.
  - Story: `problem`, `customer`, `solution`, `demo_url`, `market`, `market_method`, `business_model`.
  - Traction: `traction` JSON (metrics the founders choose).
  - The ask: `ask_round`, `ask_amount`, `use_of_funds`.
  - Claims: `founder_stated` JSON (which fields lack evidence).
  - Review: `name_check` (pending/clear/held).
  - Publishing: `published_at`, `publish_approved_by`, `updated_at`.
- `startup_slug_history`: `id`, `startup_page_id`, `old_slug` UNIQUE.
- `startup_page_visit`: `id`, `startup_page_id`, `visitor_hash`, `investor_link_id` null, `view` (public/investor), `referrer`.

**074_founder_investor** (Phase 3)
- `compliance_profile`: `id`, `venture_id` UNIQUE, `exemption` (506b/506c/undecided), `recorded_by`, `recorded_at`, `mailbox_consent_at` null, `contacts_consent_at` null.
- `investor_link`: `id`, `venture_id`, `startup_page_id`, `label`, `token` UNIQUE, `investor_email` null, `created_by`, `revoked_at`, `opened_at`, `open_count`.
- `investor_access`: `id`, `startup_page_id`, `email`, `kind` (invited/paid/requested), `investor_link_id` null, `stripe_session_id`, `payment_intent_id`, `amount_cents`, `status` (pending/active/requested/denied/refunded), `approved_by` null, `opted_in_updates` BOOL, `refunded_at`.
- `investor_update`: `id`, `venture_id`, `subject`, `body`, `status` (draft/approved/sent), `approved_by`, `sent_at`, `recipient_count`.
- `investor_profile` (admin-curated directory): `id`, `name`, `firm`, `kind` (investor/accelerator/grant), `stages` JSON, `check_min`, `check_max`, `sectors` JSON, `geographies` JSON, `apply_url`, `accepts_applications` BOOL, `auto_submit_allowed` BOOL (default 0), `notes`, `source` (admin/founder), `added_by`.
- `investor_target`: `id`, `venture_id`, `investor_profile_id` null, `name`, `firm`, `kind`, `fit_reasons` JSON, `fit_score`, `status` (candidate/calibration/pursuing/passed), `position`.
- `intro_path`: `id`, `venture_id`, `investor_target_id`, `connector_contact_id`, `strength`, `evidence` JSON, `status`.
- `venture_outreach_action`:
  - Identity: `id`, `venture_id`, `plan_step_id` null, `action_type` (research/intro_request/followup/first_contact/financial_message/customer_message/application).
  - Autonomy: `level_required`, `level_applied`.
  - State: `status` (drafted/awaiting_approval/approved/sent/blocked), `approved_by`, `block_reason`, `sent_at`.
  - Content: `target_ref`, `subject`, `body`.
- `founder_alert_rule`: `id`, `metric`, `threshold`, `enabled`.

**Account deletion:** `sql/tools/gen_delete_user.py`
- Add a `d_ventures` set: ventures the user owns.
- Map `venture_id` and `startup_page_id` to that set in `PARENT_COLS`.
- Treat `owner_user_id` and `user_id` as user columns; treat `joined_user_id`-style actor columns as null-on-delete.
- `investor_profile` is GLOBAL. `investor_access` is keyed by `startup_page_id`.
- Regenerate `sql/tools/delete_user.sql`, then keep `test_delete_user_sql.py` green.

## 4. Founder agent catalog

New file `config/founder_agents.json` with 27 agents. Every `action_type` is prefixed `fnd_`. Each entry has the same fields as `config/agents.json`, plus `track: "founder"`, `layer` 1–5 (founder layers), `effort` 1–5, `prerequisites` (fnd keys), `external` (none/customer/investor), `action_type_class` (the autonomy-matrix row) and `default_params`. The 27 agents by layer:

| Layer | Agents |
|---|---|
| **L1 Validate** | `fnd_problem_statement`, `fnd_pain_evidence`, `fnd_customer_interview`, `fnd_market_sizing`, `fnd_competitor_scan` |
| **L2 Build** | `fnd_poc_plan`, `fnd_mvp_scope`, `fnd_tech_stack`, `fnd_roadmap`, `fnd_prototype_feedback` |
| **L3 Sell** | `fnd_benefits_case`, `fnd_pilot_offer`, `fnd_positioning`, `fnd_landing_page`, `fnd_pricing_test`, `fnd_outbound_pipeline` |
| **L4 Operate** | `fnd_incorporation_checklist`, `fnd_runway_budget`, `fnd_hiring_plan`, `fnd_metrics_reporting` |
| **L5 Capitalize** | `fnd_investor_readiness`, `fnd_startup_page`, `fnd_investor_matching`, `fnd_warm_intro`, `fnd_application_form`, `fnd_data_room`, `fnd_followup_crm` |

`app/services/founder_registry.py` provides `all_agents()`, `get(key)`, `by_layer(n)`, `LAYER_NAMES` (Validate/Build/Sell/Operate/Capitalize), `DIMENSION_AGENTS` (rubric dimension → candidate agents) and `PARAM_KEYS` (autonomy_level, scope, budget_cap, action_cap, stopping_rule, voice, success_metric).

## 5. Services: interfaces between work packages

Every service is a module in `app/services/`. Signatures are fixed so packages can call each other.

- **`founder_feature`**: `mode(key='founder_track')`, `is_enabled(user, key='founder_track')`, `set_mode(key, mode, admin_id)`, `set_user_access(user_ids, state, admin_id, key='founder_track')`, `FounderError`.
- **`founder_config`**: `get(key)`, `set(key, value, admin_id)`, `DEFAULTS`, `pricing() -> dict`.
- **`founder_ventures`** (foundation):
  - Lookup and access: `venture_for_owner(user_id)`, `ventures_for_user(user_id)` (owner or active member), `get_member(venture, user_id)`, `require_member(venture_id, user)` (raises 404 or 403), `is_paid(venture) -> bool`.
  - Creation: `create_venture(user, name, one_liner, stage) -> FounderVenture`. It creates the owner member and the default decision rights (owner holds all).
  - Audit: `log_event(venture_id, kind, note, user_id=None)` writes `AuditLog`.
- **`founder_intake_service`**: `save_upload(venture, file_storage, kind, user) -> VentureArtifact` (versioned) and `add_transcript(venture, text, user) -> VentureArtifact`.
- **`founder_pitch_service`**: `start(venture, user)`, `turn(venture, user, text) -> {say, restatement?, confirmed?}`. On confirmation it calls `founder_intake_service.add_transcript`.
- **`founder_evaluation_service`**:
  - Running: `run(venture, user=None, artifact_ids=None) -> EvaluationRun` (sync core, plus `run_async`).
  - Reading: `latest_run(venture)`, `findings(venture, layer=None)`, `report_view(venture, user) -> dict` (applies the preview cut-off unless paid).
  - Feedback: `rate(finding_id, user, fair, comment)`.
  - Rung: `current_rung(venture)`, `set_rung(venture, rung, reason, user_id)`.
- **`founder_team_service`**: `build(venture) -> [AgentBlueprint]` (finding → agent with defaults), `override(venture, user, param_key, new_value, blueprint_id=None, layer=None)`, `accept(venture, user, layer)`.
- **`founder_orchestrator`**:
  - Plan: `build_plan(venture)` (creates steps from accepted blueprints plus alignment and proof steps), `refresh(venture)` (recomputes locked/ready), `next_steps(venture) -> {primary, alternates}`.
  - Transitions: `transition(step, to_state, user=None, actor_kind, note)`, `complete(step, user, evidence_note=None, output_id=None)`, `skip(step, user, reason)`.
  - Ranking: by downstream steps unblocked, ties to shortest effort, with layer diversity early.
  - Events: emits SSE events through `founder_stream.publish(venture_id, event, data)`.
- **`founder_agent_service`**: `execute(step, user=None, trigger='founder') -> VentureAgentOutput`.
  - It wraps the work in an observability run (`observability_service`, layer `F1`..`F5`), writes `founder_run_meta`, calls Claude through `claude._client()` with the founder system prompt and briefing, and runs `founder_compliance.check(text, layer)`.
  - Customer- or investor-facing results become `venture_outreach_action` drafts with status `awaiting_approval`. It never sends anything.
- **`founder_compliance`**: `check(text, layer, exemption=None) -> [issues]` (negotiation or structuring language, return projections, general solicitation under 506(b) or undecided, banned phrases) and `ensure_disclaimer`.
- **`founder_autonomy`** (Phase 3 send gate, also used in Phase 2 for drafts):
  - Levels: `level_for(venture, action_type)`, `can_raise(action_type, level)`.
  - Approvals and sends: `require_approval(action)`, `approve(action, user)` (routes to the decision-right holder), `send(action, user)` (Phase 3, behind `founder_investor`, exemption-gated).
- **`founder_stream`**: `publish(venture_id, event, data)` and an SSE generator for `/api/founder/v/<id>/stream`. Uses the 5-second polling pattern of `layer6/routes.py` over `venture_step_event`, with no in-memory bus needed.
- **`startup_page_service`**:
  - Page lifecycle: `ensure_page(venture)` (drafts from the evaluation), `slugify`, `set_slug` (reserved words, uniqueness, history), `request_publish`, `publish(page, user)` (needs the `publish_page` decision right), `unpublish`.
  - Rendering: `public_context(page)`, `investor_context(page, access)`.
  - Tracking: `record_visit`.
- **`startup_access_service`** (Phase 3):
  - Investor links: `issue_link`, `resolve_link(token)`.
  - Sign-in by email: `send_signin(email, page)`, `verify_signin(token)` (stored in the session key `_investors`, with no `login_user`).
  - Access: `has_access(page, email)`, `request_access`, `approve_access`.
  - Payment: `create_checkout(page, email)`, `fulfil(session)`, `refund_if_unpublished(page)`.
- **`founder_onepager`**: `render_pdf(page, investor: bool) -> bytes`, using `fitz.Story` like `artifact_actions.export_pdf`.
- **`founder_investors`** (Phase 3): `match(venture)` (37% calibration), `import_csv(text, admin)`, `warm_paths(venture)` (deterministic over `Contact`/`ContactAccountLink` with consent), `draft_intro(...)`, `draft_application(...)`, `draft_update(venture)`, `send_update(update, user)`. Member-scoped unsubscribe goes through `UserSuppression`.

## 6. Routes

- Blueprint `founder` (package `app/blueprints/founder/`, one module per area, all registered from `__init__.py`). Every route checks `founder_feature.is_enabled(current_user)` and returns 404 if it isn't.
  - Pages: `/founder` (intro and start), `/founder/v/<id>` (workspace: next step, plan and activity), `/founder/v/<id>/intake`, `/report`, `/team`, `/plan`, `/alignment`, `/proof`, `/page` (startup page editor), `/investors` (Phase 3), `/join/<token>` (co-founder invite).
  - APIs: `/api/founder/...`.
- Blueprint `startup`:
  - `/startup/<slug>`: public view. Old slugs redirect with 301.
  - `/startup/<slug>/i/<token>`: private investor link (Phase 3).
  - `/startup/<slug>/unlock`: investor checkout (Phase 3).
  - `/startup/<slug>/signin/<token>`: investor sign-in by email.
  - `/startup/<slug>/request`: intro or data-room request.
  - `/startup/<slug>/onepager.pdf`.
- Admin: `/admin/founder` with tabs Overview, Rubric, Readiness, Autonomy, Caps, Targets, Pricing, Preview, Investors (directory import), Name checks, Counsel. JSON endpoints live under `/api/admin/founder/...`.
- Observability: a "Founder Track" tab on `/admin/observability` with Fleet, Venture trace, Autonomy audit, Eval quality, Compliance, Connectors and Startup pages.

## 7. UI

- Founder screens extend `base.html` and use `system.css` tokens and classes (`.wrap`, `.btn`, `.btn.primary`, cards).
- The workspace takes the GCC visual language: a focus bar showing the one next step, layer lanes and an activity list.
- Each founder page stays one clear task.
- The member nav "My work" menu gets a gated "Founder Track" item (`site_nav` opt-in gate `founder`, hidden when the switch is off).
- The startup page is light, as described in section 0, under `.st-root` in `static/css/startup.css`. It uses `partials/_favicon.html` and `partials/_logo.html` (a "Made with SimiSI" mark in the footer).

## 8. Work packages

These are implemented in waves. Each package owns its listed files exclusively. It may *read* anything, and may *call* any interface in section 5. If a package needs a change to a file it doesn't own, it says so in its report instead of making the change.

- **WP0 Foundation** (wave 0): `founder_feature`, `founder_config`, `founder_ventures`, `founder_registry`, `config/founder_agents.json`, the models in `app/models/founder.py`, migrations `sql/069`–`074`, `app/models/__init__.py`, account deletion (generator, regenerated `delete_user.sql`, `test_delete_user_sql.py`), and the empty blueprints `founder` and `startup` registered in `app/__init__.py` (plus `_public_prefixes` for `/startup/` and `/founder/join/`). It also raises `config.py` `MAX_CONTENT_LENGTH` to 20 MB, adds `legal_text` keys (`founder_report_disclaimer`, `founder_not_advice`, `startup_claims_note`, `investor_update_footer`), adds `founder_stream`, and writes `test_founder_foundation.py`.
- **WP1 Intake and pitch** (wave 1): `founder_intake_service`, `founder_pitch_service`, `blueprints/founder/intake.py`, `templates/founder/intake.html`, `static/js/founder_pitch.js` and `test_founder_intake.py`.
- **WP2 Evaluation and report** (wave 1): `founder_evaluation_service`, `blueprints/founder/report.py`, `templates/founder/report.html` and `test_founder_eval.py`.
- **WP3 Admin, pricing and payment** (wave 1):
  - Admin: `blueprints/founder/admin.py` (the `/admin/founder` page and APIs), `templates/admin/founder.html`, plus one tuple in `partials/_admin_subnav.html` (Growth group) with the matching `test_admin_subnav.py` page entry.
  - Payment: `stripe_service.create_founder_checkout` (added), a new `elif` branch for products `founder_track`/`founder_addon` in `payments/routes.py`, `blueprints/founder/billing.py` and `test_founder_admin_billing.py`.
- **WP4 Team, plan, agents and workspace** (wave 2): `founder_team_service`, `founder_orchestrator`, `founder_agent_service`, `founder_compliance`, `founder_autonomy` (levels and drafts), `blueprints/founder/workspace.py`, `templates/founder/{workspace,team,plan}.html`, `static/js/founder_workspace.js` and `test_founder_plan.py`.
- **WP5 Proof path and alignment** (wave 2): `founder_proof_service`, `founder_alignment_service`, `blueprints/founder/team_proof.py`, `templates/founder/{alignment,proof,join}.html` and `test_founder_alignment_proof.py`.
- **WP6 Startup page (public)** (wave 2): `startup_page_service`, `blueprints/startup/routes.py` (public view, request and redirects), `blueprints/founder/page_editor.py`, `templates/startup/page.html`, `templates/founder/page_editor.html`, `static/css/startup.css`, and the sitemap and robots additions in `pages/routes.py` (additive). Also `test_footer.py` only if needed, and `test_startup_page.py`.
- **WP7 Observability and navigation** (wave 2): additive edits to `observability_service.layer_code` (F codes) and its filters, a Founder Track tab in `blueprints/observability` and `templates/admin/observability.html`, `founder_alerts` (alert rules and an evaluator), the pause and re-run controls, the gated `site_nav` item, and `test_founder_obs_nav.py`.
- **WP8 Investor connection** (wave 3, behind `founder_investor`):
  - Startup side: `startup_access_service`, `founder_onepager`, the investor routes in a new `blueprints/startup/investor.py`, `stripe_service.create_investor_access_checkout` (added), and a new `investor_access` branch in `payments/routes.py`.
  - Founder side: `founder_investors`, the exemption gate and sends in `founder_autonomy` (extending WP4's module through new functions only), `blueprints/founder/investors.py`, `templates/founder/investors.html` and the investor-view partials.
  - Tests: `test_founder_investor.py`.

## 9. Deploy notes (accumulate)

- Run `sql/069` to `sql/074` in order.
- The `founder_track` switch is off by default; turn it on in `/admin/founder` (allowlist first).
- `founder_investor` stays off until counsel signs off.
- The upload cap is now 20 MB. Side effects checked: the public careers form (`/api/careers/apply`) and resume upload (`/api/resumes/upload`) relied on the old global 5 MB cap, so both now check 5 MB themselves (`careers_service.MAX_RESUME_BYTES`, 413 with the existing message) and behave as before. Every other upload route now accepts up to 20 MB.
- Account deletion (`delete_user.sql`): a co-founder's decision rights in someone else's venture pass to that venture's owner, their plan-step ownership is cleared, and investors a founder added to the directory (`source='founder'`) are deleted; admin-curated investors stay.
