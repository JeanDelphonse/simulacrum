# EXEC_PLAN — Agent Execution Layer (Phase 1: inventory & plan)

Branch: `feat/agent-execution` (off `fix/unique-recipients`). Read-only so far; no feature code.
Spec: `docs/CLAUDE_CODE_PROMPT_Agent_Execution.md`.

**Bottom line.** Much of the substrate already exists: versioned artifacts, a tool-call
guardrail boundary, rate limits, an escalations queue, channel approvals, scheduled steps and
a per-agent planning step. The execution layer should be built *on* these, not beside them.
Three current behaviours conflict with the spec's non-negotiable rules and need your call
before P1 starts (§3). Decisions needed are collected in §8.

---

## 1. Inventory — what exists today

### 1.1 Where artifacts are generated and stored
| Concern | Today | Where |
|---|---|---|
| Artifact object | `agent_actions` row: `artifact` text, `status`, `action_type`, `simulation_id`, `archived_artifact` | `app/models/agent_action.py` |
| Versions | `artifact_versions`: `version_number`, `content`, `is_current`, `edited_by` (agent / user / co-pilot), `parent_version_id`, `draft_content`, `edit_summary`, `change_summary`, `public_url` | `app/models/artifact.py` |
| Edit / draft / restore | `POST /api/artifacts/<id>/save`, `/draft`, `GET /versions`, `POST /versions/<n>/restore` | `app/blueprints/artifact_view/routes.py` |
| Re-run / archive | `POST /api/artifacts/<id>/rerun`, `/archive`, `/unarchive` | same |
| Bundles / export | `artifact_bundles` + zip compile task (cover page, sorted artifacts) | `app/models/artifact.py`, `app/tasks/bundle.py` |
| Dependencies / staleness | `artifact_dependencies`, seeded from the agents.json DAG | `app/services/prefill_engine.py` |
| Multi-step units | `action_steps` (per-contact `email_send` / `email_followup` / `convertkit_broadcast` / `linkedin_post` / `drip_release`), conditions `no_reply` / `no_signature` / `no_purchase` / `no_booking` | `app/models/action_step.py`, `app/services/action_step_service.py` |
| Structured artifacts | Cold Email / Consulting Outreach artifacts are JSON (`prospects[]`, `sequence[]`); everything else is markdown | `app/services/claude.py` |

### 1.2 Agent runner / orchestrator entry points
- **Single governed entry:** `claude.execute_agent_action()`. It opens the observability run, checks dispatch, runs the body and evaluates the output.
- **Body:** `_execute_agent_action_body()`: briefing, then prospect research, then **planning step** (`agent_briefing.plan_run`, Haiku, plan recorded on the trace), then generation.
- **User-triggered runs:** `tasks/agent.execute_agent_action_task`. The simulations route runs it in a thread; the artifact page's Re-run calls `.delay()` (Celery), see §3.4.
- **Orchestrator:** `layer6.run_orchestrator_cycle()`. It scores agents, applies `_check_autonomy_bounds()` (channel approvals), runs agents one at a time in a background thread (`_execute_action_sync`), then runs the post-completion hooks: send hooks, `create_steps_from_artifact`, and `wire_service.deploy_to_integration`.
- **Scheduled steps:** `process_due_steps()` at cycle start, gated by channel approval.

### 1.3 "View artifact" surfaces (where an action bar must render)
- Artifact page `/simulations/<sim>/artifacts/<id>` (`templates/simulations/artifact_view.html`). It has edit, preview, versions and re-run, plus Consulting Outreach per-prospect send / skip / edit.
- Journey tab "View artifact →" (`layer6.html`, `jrn3` rows) and the cycle activity list (`cyc-agent-link`).
- Layer detail (`layer_detail.html`), and the Agent Network / World drawer (`agent_network.js`, `_agent_network.html`, `_wealth_world.html`).
- Admin observability artifact quarantine/release (`admin/observability.html`).

### 1.4 CRM / contacts
- `contacts`: pipeline `prospect / active / client / closed_lost`, `outreach_count`, `last_contacted_at`, `do_not_contact`, `is_archived`, `source`, `source_action_id`. Also `contact_activities`.
- `email_logs` (every send; opens, replies, bounces), `email_suppressions` (unsubscribe / bounce / complaint).
- Recipient ledger `agent_recipients`: each person once per agent per simulation. Placeholder-contact guard. Admin outreach reset.

### 1.5 Integrations — client present vs actually wired
| Integration | Client | Wired today | Notes |
|---|---|---|---|
| internal_email | SendGrid via `outreach_email_service.send_outreach_email` | **Sends** (hooks + steps) | From `MAIL_DEFAULT_SENDER` = SimiSI domain (§3.1) |
| Apollo | `apollo_client.py` | Research (search + enrich), contact upsert, sequences API present | Fixed 2026-10-29 (uncommitted on `fix/unique-recipients`). Sequences would send from the **user's** mailbox. |
| ConvertKit | `convertkit_service.py` | Subscriber tagging, sequence enrol (wire) | `convertkit_broadcast` step is a **logged no-op** |
| Cal.com | `cal_service.py` (OAuth) | Booking URL injection, webhooks | Deploy = no-op |
| Kajabi | none | Deploy raises `NotImplementedError` | Stub |
| Stripe | `stripe_connect_service.py` | **Creates products + payment links** on the user's connected account | Reversible (link only) |
| PandaDoc | `pandadoc_service.py` | **Creates + sends documents for signature** to a CRM contact | Contractual |
| LinkedIn | `linkedin.py` (OAuth) | Deploy raises: "attorney review required" | Blocked by design |
| Alpaca | registry entry + execution webhook only | **No order / trade code exists** | Plan-only already true |

Registry: `config/integrations.json` and `integration_registry.py` (tiers brokered / oauth / advanced). Activation state: `integration_activation.py` (CONNECT-001).

### 1.6 Governance, guardrails, escalations
- **Observability** (`observability_service.py`, PRD-SIM-OBS-001): runs, events, tool spans, artifacts, quarantine. `tool_span(..., side_effecting=True)` calls `guardrail_service.check_tool_call()` **at the tool-call boundary**. Capture fails open; enforcement fails closed (already the documented posture).
- **Guardrail rule types:** `tool_allowlist`, `param_bound`, `output_filter`, `content_policy`, `rate_limit`, `model_constraint`. Scopes: global / layer / agent.
- **Autonomy today:** `Layer6Config.trust_level` presets (`full_auto` / `balanced` / `review_all`) and `channel_approvals` (`email`, `email_funnels`, `linkedin`, `calendar`, `content_publishing`). Enforced at dispatch (`_check_autonomy_bounds`), at step execution and at wire deploy.
- **Escalations:** `Layer6ActionQueue.STATUS_ESCALATED`, `utils/action_items.create_action_item` (SSE + escalation email), notifications. The Journey tab shows escalated entries.
- **Audit:** `audit_logs` (`AuditLog.log`).

---

## 2. Spec ↔ existing-model mapping (proposal: extend, don't duplicate)

| Spec table | Proposal | Why |
|---|---|---|
| `artifacts` | **Reuse `agent_actions`**; add `shape` and `state` columns | Every surface, FK and the ledger keys off `agent_actions.id`. A parallel table means dual writes. |
| `artifact_versions` | **Reuse**. `origin` = existing `edited_by` (add value `agent_refine`); `body_ref` = inline `content` | Already versioned with parent / draft / restore |
| `artifact_units` | **New**. For sequences, link to `action_steps` (`unit.step_id`) rather than replace them | Steps already drive scheduling and sends |
| `artifact_actions` | New | Verb log per artifact / unit |
| `tool_registry` | New, seeded from §5 | — |
| `agent_plans`, `plan_steps` | New. P3 promotes the existing planning-step output into these | Plans are produced today but only kept on the trace |
| `agent_autonomy_config` | New. **Absorbs** `trust_level` / `channel_approvals` (migration maps presets onto the ladder) | Two autonomy systems would disagree |
| `execution_runs` | New, with `obs_run_id` linking to observability `agent_runs` | Avoid a second trace store |
| `action_approvals` | New. Each pending approval also creates an action item, so it appears in the existing Escalations queue | — |

PKs: the spec says `generate_pk()`; the repo's 9-char generator is `utils.id_gen.generate_id()`. I'll use that.
Migrations continue at `sql/056_…`. Every new module gets `from __future__ import annotations` (production runs Python 3.9).

---

## 3. Conflicts with the spec's non-negotiable rules (decide before P1)

1. **Sending identity.** All agent emails go through SimiSI's SendGrid account, from `MAIL_DEFAULT_SENDER` (the SimiSI domain) with the user's name as display name. The spec says real sends must use the **user's own** integration and identity. Apollo has no direct-send API; its **sequences** send from the user's linked mailbox. ConvertKit broadcasts send from the user's ConvertKit sender. Gmail/Microsoft "send as" would be a new OAuth integration.
2. **Auto-send.** With the `email` channel approved (the default is **on**), the orchestrator sends Cold Email Step 1 and scheduled follow-ups **without a per-send human confirmation**. The hard-gate rule says sending to real people *always* needs explicit confirmation, at every autonomy level.
3. **Re-run silently replaces edits.** A re-run creates a new version and makes it current even when the current version was user-edited. Old versions survive, so revert is possible, but the switch is silent.
4. **Artifact-page Re-run depends on Celery.** It calls `.delay()`, and production runs no Celery worker (every other path uses threads), so the button may never actually run. The fix is small, but it is a behaviour change.

"Preserve behavior; this is additive" is in tension with 1 and 2. §8 Q1–Q2 ask how to resolve that.

---

## 4. Rule preview — `config/agent_execution.json` (computed read-only, not written yet)

Precedence where several classes apply (the spec doesn't define one): **money_move > send_blast > contractual > public_post > money_link > reversible > none** (most dangerous wins).

Two inputs the rules reference don't exist in `agents.json`:
- **`multi_step`**: proposed source is the agents with scheduled-step configs (`AGENT_STEP_CONFIG`).
- **`artifact_type`**: proposed source is label/description keywords (email, outreach, campaign, reactivation, win-back, sequence).

| L | Agent | shape | class | integrations |
|---|---|---|---|---|
| 1 | rate_card | config | money_link | stripe |
| 1 | linkedin_optimization | page | public_post | linkedin |
| 1 | booking_page | page | reversible | cal_com |
| 1 | cold_email_campaign | sequence | send_blast | apollo, internal_email |
| 1 | consulting_outreach | sequence | send_blast | apollo, internal_email |
| 1 | role_search | document | send_blast ⚠ | apollo |
| 1 | consulting_proposal | sequence | contractual | pandadoc |
| 1 | sow_template | document | contractual | pandadoc, stripe |
| 1 | agreement_template | document | contractual | pandadoc |
| 1 | referral_network | document | send_blast | apollo, internal_email |
| 1 | negotiation_script | document | none | — |
| 2 | speaking_proposals | sequence | send_blast | apollo, internal_email |
| 2 | speaker_fee_rider | config | contractual | pandadoc |
| 2 | group_coaching | document | reversible ⚠ | cal_com, convertkit, kajabi, stripe |
| 2 | workshop_curriculum | document | reversible | cal_com, kajabi, stripe |
| 2 | corporate_training | sequence | send_blast | apollo, internal_email |
| 2 | waitlist_landing_page | page | send_blast ⚠ | convertkit, kajabi |
| 2 | alumni_reactivation | sequence | send_blast | internal_email |
| 2 | roi_calculator | config | none | — |
| 3 | course_curriculum | document | reversible | kajabi |
| 3 | pricing_research | config | none | — |
| 3 | sales_page | page | reversible | kajabi |
| 3 | ebook_outline | document | money_link | stripe |
| 3 | ab_test_plan | config | none | — |
| 3 | membership | page | reversible | kajabi, stripe |
| 3 | launch_sequence | sequence | send_blast | convertkit |
| 3 | affiliate_program | document | send_blast | apollo |
| 3 | testimonials | document | send_blast | internal_email |
| 3 | lapsed_buyer | sequence | send_blast | internal_email |
| 4 | seo_content_calendar | schedule | public_post | linkedin |
| 4 | lead_magnet_funnel | page | send_blast | convertkit |
| 4 | newsletter | schedule | reversible ⚠ | convertkit |
| 4 | saas_product_spec | document | none | — |
| 4 | ip_licensing | document | contractual | pandadoc |
| 4 | affiliate_partnerships | document | send_blast | apollo, internal_email |
| 4 | video_podcast | schedule | none | — |
| 4 | community | document | none | — |
| 4 | programmatic_ads | document | none | — |
| 4 | client_winback | sequence | send_blast | internal_email |
| 5 | income_allocation … estate_planning (9 agents) | config / document | none → **generate-only** | — |
| 5 | dca_schedule | schedule | money_move → **plan-only, execution disabled** | alpaca |

⚠ Where the literal rules give a questionable result:
- **role_search → send_blast.** It sources hiring contacts but sends nothing; the class comes from `produces_contacts`. *Proposal:* `none` (send_to_contacts still offered as a drafted, hard-gated action).
- **newsletter, group_coaching → reversible.** Both use ConvertKit to email a list, but no "send" keyword matches. *Proposal:* `send_blast`, because they email people.
- **waitlist_landing_page → send_blast.** It publishes a page plus opt-in confirmation emails to people who signed up themselves. *Proposal:* keep `send_blast` (conservative).
- **`agents.json` integrations ≠ the wire deploy map** (e.g. the wire map gives consulting_outreach PandaDoc and Cal.com). The spec says derive from `agents.json`. *Proposal:* follow the spec, and have a test flag the drift.

---

## 5. Phased build

Each phase: small commits, the full test suite green after each, a combined deploy zip as today. No local server; you verify on simisi.io.

### P1 — Artifact object, versioning, action bar (safe actions only, no side effects)
- **Model:** `agent_actions.shape` / `state` columns (sql/056). Versions: an `agent_refine` origin; **the original agent version is immutable** (save/restore never mutate version 1; an API guard rejects writes to it).
- **Re-run policy (§3.3):** if the current version is user-edited, the re-run lands as a non-current version marked "agent update available"; the user picks it or keeps theirs. Fix the Re-run dispatch to use the thread runner (§3.4).
- **Actions:** edit (manual), **AI refine** (Haiku/Sonnet rewrite of the selection or the whole artifact, producing a new `agent_refine` version, with a diff preview), preview, **export** (Markdown / PDF / DOCX, reusing the bundle cover code), copy, **duplicate** (new action row; the original is untouched).
- **UI:** an `artifact_action_bar` Jinja macro plus a JS module, rendered on the artifact page, the Journey rows, the cycle activity list and the Agent Network drawer. Only safe verbs in P1.
- **Files:** `app/services/artifact_actions.py`, `app/blueprints/artifact_view/routes.py` (new verbs), `templates/partials/_artifact_action_bar.html`, `static/js/artifact_action_bar.js`.
- **Risks:** artifact_view.html is large and has two render modes (Consulting Outreach JSON vs markdown); PDF export needs a library that runs on GoDaddy Python 3.9 (prefer a pure-Python one; confirm availability).
- **Tests:** `test_artifact_versions.py` (immutability, re-run never steals current, revert, duplicate) and `test_artifact_actions.py` (each verb, ownership checks, no outbound calls; HTTP is mocked to assert zero sends).

### P2 — Derived execution config, contact picker, send-as-draft
- `scripts/build_agent_execution.py` generates `config/agent_execution.json` from §4's rules (deterministic; a test asserts the file equals the generator's output, so it can't be hand-edited).
- `agent_execution.py` loader; `supported_actions`, `suggested_tools` and `methods` per agent.
- **Contact picker:** choose CRM contacts for an artifact, filtered by the ledger, suppression, do-not-contact and placeholders.
- **Send-to-contacts = DRAFT only:** it builds per-recipient previews (merge fields resolved) and stores them as `artifact_units`. **Nothing is sent.**
- **Tests:** generator determinism, every class/shape matches the §4 table, the picker excludes protected contacts, and the draft path makes zero network calls.

### P3 — Plans and the tool registry (L1)
- `tool_registry` seeded from integrations and primitives (`ai_refine`, `web_research`, `export_pdf`, `send_email`, `create_payment_link`, `publish_page`, `send_for_signature`, …), each with class, blast radius, cost and reversibility.
- Promote the existing planning step: the plan becomes `agent_plans` / `plan_steps` (tool + method + inputs + expected result + reversibility + blast + cost), rendered as an inspectable artifact with per-step approve/reject (approvals recorded, **nothing executes in P3**).
- `agent_autonomy_config` with conservative defaults from §4; admin and user settings pages; migration of `trust_level` / `channel_approvals` onto the ladder.
- **Tests:** plan persistence, the default ladder per class, L5 forced generate-only, `dca_schedule` execution disabled, and no default above L3.

### P4 — L2 execution, reversible agents only
- An executor runs approved plan steps through `tool_span(side_effecting=True)`, so guardrails are checked at the boundary. It writes `execution_runs` and `artifact_actions`, plus the audit log. Approvals surface in Escalations.
- **In scope:** publish page (Cal.com booking config / landing page where the integration supports it) and **create a Stripe payment link**. No sends, no signatures, no posts.
- Admin-configurable limits: per-agent / per-cycle step limits and budget caps (`platform_settings`).
- **Tests:** an unapproved step never runs; a guardrail block stops execution (fail closed); a capture failure doesn't (fail open); limits enforced; a reversible action can be undone where the API allows (payment link deactivation).

### P5 — The safe send pipeline (unlocks send_blast)
- The send goes through the **user's own integration** (per §8 Q1). Mandatory preview, an explicit per-batch confirmation (hard gate, not bypassable), an unsubscribe link and suppression, the recipient ledger, rate limits, budget caps, and audit.
- The existing SendGrid / orchestrator auto-send paths are routed through the same gate (per §8 Q2).
- **Tests:** no send without a confirmation record; confirmation is bound to the exact recipients and content (an edit after confirming invalidates it); unsubscribe honoured; limits; the L4 setting is rejected.

**Out of scope (v1):** L5 execution, Alpaca / `dca_schedule` trade execution, L4 autonomy, LinkedIn posting (attorney review), Kajabi (no client).

---

## 6. Cross-cutting risks
- **GoDaddy hosting:** background threads die when Passenger recycles idle processes, and the keepalive cron was broken after the cutover. Executors must be resumable and idempotent, with an `execution_runs` status machine and the stale-run recovery that already exists for the Journey tab.
- **Large templates** (`layer6.html` ~3.8k lines, `artifact_view.html`): the action bar must be an include/macro, not more inline code.
- **Behaviour changes** §3.1–3.4 touch live outreach. Each lands behind a platform setting with a clear default (§8).
- **Migrations:** each phase ships its own `sql/05x` file, to run before uploading that phase's zip.

## 7. What Phase 1 did not touch
No application code changed. The only new file is this plan. (The Apollo fix from 2026-10-29 is still uncommitted in the working tree and unrelated to this branch's history.)

## 8a. Decisions (owner, 2026-10-29)
1. **Sending identity:** keep today's SimiSI-domain SendGrid sends. P5's "user's own" path to be decided at P5.
2. **Auto-send:** keep the orchestrator's existing auto-send, governed by today's channel approvals. The spec's per-send hard gate applies to the new execution layer's send actions (P5), not to the existing pipeline. *(Owner override of the spec's "no autonomy level bypasses the hard gate" for the pre-existing pipeline.)*
3. **Data model:** reuse `agent_actions` / `artifact_versions` (§2).
4. **Classification:** precedence accepted; role_search → none; newsletter and group_coaching → send_blast; waitlist_landing_page stays send_blast.
5. **Re-run:** never replaces a user-edited current version; dispatch moves off Celery.
6. **Export:** PDF only (PyMuPDF `Story`, already a dependency; WeasyPrint needs system libraries the host lacks).

P1 consequence of the immutability rule: agent-generated versions are protected by a model-level guard, and the in-place writers (Consulting Outreach inline edit, send/skip status, the send hooks) copy-on-write into a new version the first time they touch an original. The admin "release quarantined output" path is the one documented exception.

## 8b. Progress
- **P1 — done** (commit `9320cfe`). No migration.
- **P2 — done.** `config/agent_execution.json` is generated by `app/services/agent_execution.py` (`python -m app.services.agent_execution --write`); a test fails on drift. The owner's classification decisions are rule refinements, not exceptions. `artifact_units` and `artifact_actions` (`sql/056`). Contact picker, send-as-draft with merge fields and an unresolved-placeholder preview, admin cap `exec_draft_max_recipients` (default 50); P1 verbs are logged too.
  Deviation from §5: no `shape`/`state` columns on `agent_actions`. Shape comes from the generated config by agent type, and P2 needed no state, so both columns would have been unused. Drafts do not claim recipients in the ledger; a draft has not contacted anyone.
- **P3 — done.** `tool_registry`, `agent_plans`, `plan_steps`, `agent_autonomy_config`, `action_approvals` (`sql/057`).
  - Plans are built deterministically from each agent's `methods` template (no model call) and created the first time an artifact's plan is opened. Each step names a registered tool with its reversibility, reach and cost; approving or rejecting a step only records the decision.
  - Autonomy is per user and per agent: L0–L3, never L4; Layer 5 is locked at L0; money movement is capped at L1. Settings page at `/settings/agent-autonomy`.
  - Tools seeded disabled: Alpaca orders, LinkedIn posting, Kajabi publishing.
  - Deviations from §5: the autonomy ladder governs the new execution layer only. The existing `trust_level` / `channel_approvals` keep governing today's pipeline, per owner decision 8a-2. Plans aren't emitted inside the agent run, which keeps the run paths untouched.
- **P4 — next, awaiting go-ahead:** L2 execution of approved reversible steps (Stripe payment link, Cal.com booking link) through the guardrail boundary, with `execution_runs`, step limits and budget caps, and approvals surfacing in Escalations.

## 8. Decisions needed (original questions)
1. **Sending identity (§3.1).** Which "user's own" send path for P5: (a) Apollo sequences via the user's linked mailbox (no new OAuth; Apollo plan/credits apply), (b) Gmail / Microsoft 365 send-as (new OAuth, Google app verification), or (c) both, with (a) first? And until P5 ships, keep today's SimiSI-domain sends, or pause them?
2. **Auto-send (§3.2).** Is a **per-batch** confirmation (one click approves an exact recipient list plus exact content, follow-ups included, stop-on-reply) acceptable as "explicit human confirmation", or must every individual send be confirmed? And should today's orchestrator auto-send move behind that gate in P1 (the safety-first reading) or in P5 (the "additive" reading)?
3. **Data model (§2).** OK to reuse `agent_actions` / `artifact_versions` as the spec's `artifacts` / `artifact_versions`, instead of new parallel tables?
4. **Classification (§4).** Accept the precedence order and the four ⚠ resolutions (role_search → none; newsletter and group_coaching → send_blast; waitlist stays send_blast)?
5. **Re-run (§3.3/3.4).** OK to change re-run so it never replaces a user-edited current version, and to switch its dispatch off Celery?
6. **Export formats** for P1: Markdown + PDF + DOCX, or a subset?
