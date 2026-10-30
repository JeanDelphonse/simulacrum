# Site update plan: "agents that act" positioning

Source: `docs/CLAUDE_CODE_PROMPT_Website_Updates.md`. Phase 1 (inventory and plan). No template or code has changed yet.

Branch: `feat/site-execute-positioning`, cut from `feat/agent-execution` rather than `master`. The new copy describes the execution layer (P1–P5), and only that branch ships it. Production runs `feat/agent-execution` plus the two fix branches.

---

## 1. Inventory

### 1.1 Marketing templates
The whole homepage is one template: `app/templates/landing.html` (1,557 lines), rendered by `pages.index` (`app/blueprints/pages/routes.py:310`). It has no section partials. The public footer is `partials/_footer_public.html`.

| Section | Spec | Lines in `landing.html` |
|---|---|---|
| Meta (description, OG, Twitter) | A | 8, 11, 25 |
| Hero (h1 + subheadline) | A | 777–953 (subheadline at 885) |
| The Model, the five-layer ladder | Rule 3 | 955–1022 |
| How it works (4 steps) | B | 1025–1054 |
| Why you can trust it (Calibrated, Validated) | D | 1057–1075 |
| Bio on-ramp and bio strip | (none) | 1078–1136 |
| Pricing (Free, Simulation, Enterprise) | G | 1139–1217 |
| Social proof | (none) | 1221–1276 |
| FAQ (6 items) | F, H | 1279–1330 |
| Closing CTA | (none) | 1333–1344 |

The page has **no feature-card section**, so item E ("add three cards alongside the existing ones") has nothing to sit beside. See §3.4.

### 1.2 Prices: where they come from today
There is a single source of truth, `app/services/pricing_service.py`:
- `get_current_price()` returns `base_price_cents`, `discounted_price_cents`, `discount_percentage`, `is_discounted`, `label` and `expires_at`.
- The base price is the platform setting `simulation_price`, set from the Admin Console. Any discount is the active `SimulationDiscount`.
- Checkout calls `price_for_user()` (`simulations/routes.py:46-54`). It wraps `get_current_price()` and adds per-user comps.

The landing page **already** reads from this source, through `_sim_price_usd()` and `_sim_pricing()` (`pages/routes.py:218-244`). The pricing card at `landing.html:1169-1180` already shows a strikethrough when a discount is active.

**No price is hardcoded in the marketing templates.** The site-wide grep found no "$167", "$83.50" or "$295" in `app/templates`, `app/static` or the partials. The real defects are these:

| # | Where | Problem |
|---|---|---|
| P-1 | `landing.html:1309` (FAQ question) and `:1312` (answer, 3×) | Uses `sim_price_usd`, which is the **base** price. With the 50% launch discount on, the card says $83.50 while the FAQ says "$167 once". **This is the live inconsistency.** |
| P-2 | `landing.html:1214` | The footnote "Simulation price reflects the current rate configured at checkout." is vague. It can go once the prices are consistent. |
| P-3 | `config/jobs.json:97` (rendered on `/careers`) | The only true hardcoded price: "convert engaged free users to a $167 simulation". |
| P-4 | `pages/routes.py:224, 237` | The fallback when pricing fails is `SIMULATION_PRICE_CENTS` (69500, i.e. $695), so a DB hiccup would show $695. This isn't a template, but the display can silently disagree with checkout. |
| P-5 | (no tests) | No test pins the landing price to checkout. |

The illustrative dollar figures in the use-case samples (`samples/simulacrum_usecase_*.html`, such as "$185/hr" or "$168,200 new income") are example outcomes, not our price. They stay.

### 1.3 Layers
- The source of truth is `config/agents.json`: 49 agents, L1 = 11, L2 = 8, L3 = 10, L4 = 10, L5 = 10.
  - The canonical names are in `app/models/observability.py:72` `LAYER_NAMES`: Active Income, Leveraged Income, Productized Income, Automated Residual, Wealth Deployment. It also defines `L0` Free Tier and `L6` Orchestration, which are internal.
  - The names are also duplicated in `models/simulation.py`, `models/feedback.py`, `services/claude.py`, `world_service.py` and several templates.
- **The only "6 income layers" on the marketing site** is the FAQ answer at `landing.html:1305`: "deploys 49 specialized agents across 6 income layers".
- The Model ladder (`landing.html:962-1014`) hardcodes all five names, taglines, lenses and **per-layer agent counts** (11/8/10/10/10). They're correct today, but would drift if agents.json changes.
- In-app (not marketing), "Layer 6" / "L6 Growth Orchestrator" is the orchestrator: the feedback widget in `base.html:201`, `admin/feedback.html:128` and the GCC pages. It is labelled as the orchestrator, not as an income layer. **I propose leaving it** (out of scope for the marketing site; changing it touches stored feedback categories).

### 1.4 Integrations (for section F)
`config/integrations.json` lists 9 integrations:

| Integration | Mode | What it does today | Status |
|---|---|---|---|
| internal_email | brokered | Sends outreach (SendGrid) | live |
| apollo | brokered | Finds prospects | live |
| pandadoc | brokered | Proposals (`pandadoc_service.py`) | live |
| stripe | OAuth | Payment links | live |
| cal_com | OAuth | Creates a booking page | live |
| linkedin | OAuth | Profile import | live; **posting is disabled** pending attorney review |
| kajabi | advanced | Publishing | **no client; the tool is disabled** |
| convertkit | advanced | Newsletter / email funnels | live |
| alpaca | advanced | Portfolio | **order execution is disabled** |

"brokered" means SimiSI's own account, not the user's.

---

## 2. Copy honesty: the spec's claims checked against what the product does today
Rule 4 says copy must match current capability. Several lines in sections A, C and D would be **false today**:

| Spec copy | Reality in the code | Verdict |
|---|---|---|
| A: "prepares them to send **from your own account**"<br>D: "Runs on your own accounts — agents use your email and your tools, **never ours**" | By default, email goes out through **SimiSI's SendGrid**: from simi@simulacrumai.io with the user's name, and replies route through SimiSI (`exec_send_provider = simisi_sendgrid`). Apollo and PandaDoc are brokered on SimiSI's keys. Stripe, Cal.com and ConvertKit are the user's own accounts. | **False by default** |
| A: "with your approval **at every step**"<br>D: "You approve every send — **nothing goes out without your okay**"<br>H: "Will SimiSI email people without my permission? — No" | Only the **new** Send-to-contacts path (P5) needs a per-batch confirmation. The **orchestrator's existing auto-send** (cold email, consulting outreach, scheduled follow-ups) goes out when the simulation's `email` channel approval is on, and it is **on by default** (`Layer6Config.channel_approvals`, `email: True`). You confirmed this in EXEC_PLAN §8a-2. | **False by default** |
| A: "**books the calls**" | Agents create a Cal.com booking page (P4, after approval). Prospects book the calls themselves. | Overstated |
| C: "it follows up until someone replies" | True: scheduled follow-ups with a `no_reply` condition. But this runs on the existing auto-send path, not on the approval path. | True, but it's the ungated path |
| D: "Never touches your money — investing agents advise" | True. Layer 5 is locked at L0, `alpaca_order` is disabled, and Stripe only creates links. | OK |
| D: "You set the autonomy — from 'just draft it' to '**run it and tell me**'" | The per-agent ladder L0–L3 exists (`/settings/agent-autonomy`). But at L3, executable steps still need approval each time (EXEC_PLAN P4 deviation), so "run it and tell me" isn't real yet. | Partly ("rolling out") |
| H: "every action is logged" | True: the artifact action log, audit log and observability runs. | OK |

---

## 3. Decisions needed before editing

### 3.1 Approval claims (A, D, H)
Pick one:
- **(a) Copy-only (recommended to start).** Say what is true:
  - "You choose what your agents may send. Every message is drafted for your review, and nothing goes out on a channel you haven't switched on."
  - FAQ: "Only on channels you approve. Turn email off and every message waits for your one-click okay."
- **(b) Make the claim true.** Change the default `email` channel approval to **off** for new simulations, so the orchestrator's sends wait for approval. This is a behaviour change and reverses §8a-2 for new simulations. Existing simulations keep their setting.
- **(c)** Route the existing auto-send through the P5 confirmation gate. This is the largest change.

### 3.2 "Your own account" (A, D)
Pick one:
- **(a) Recommended.** "Sends under your name; connect your own SendGrid to send from your domain." Change the D bullet to "Works through your tools: your Stripe, your Cal.com, your newsletter."
- **(b)** Make the "own account" default by setting `exec_send_provider = own_sendgrid`. Every user would then have to connect SendGrid before any send.

### 3.3 "Rolling out" labels
I propose a small pill on these:
- Kajabi publishing
- LinkedIn posting
- "run it and tell me" (L3 without per-step approval)
- the self-follow-up step in section C, if you pick 3.1 (b) or (c). Under (a), the follow-up is live today.

### 3.4 Feature cards (E)
There's no existing feature-card section. I propose putting the three cards inside the new C section ("From plan to done."), under the 4-step loop, rather than adding another section.

---

## 4. Ordered edits (after approval). One commit per item, with tests run after each.

| Step | Section | Files | What | Risk | Verify |
|---|---|---|---|---|---|
| 0 | Rules 2 and 3 | `pages/routes.py`, new `app/services/site_context.py`, new `partials/_layers.html` | One `pricing` context: `original_price`, `current_price`, `discount_pct`, `is_discounted`, `label`, `expires_at`. It is built from `get_current_price()`, the same function checkout's `price_for_user()` wraps. Plus one `site_layers` list (code, name, tagline, lens, agent count from agents.json), shared by the ladder and the FAQ. Keep the old `sim_price_usd`/`sim_pricing` names until the last step. | Low | New `test_site_pricing.py` (discount on and off: card, FAQ and checkout price agree; no `$<digits>` literal in landing.html outside the allowlist) |
| 1 | H(1), Rule 3 | `landing.html` FAQ, ladder | Change "6 income layers" to five; the ladder and FAQ render from `site_layers` | Low | Test: no "6 layers"/"six layers" on `/`; 5 rows rendered; agent counts sum to 49 |
| 2 | H(2), G | `landing.html` pricing card and FAQ | The FAQ uses `current_price`, with the original price struck through when discounted. The card label is "{discount_pct}% launch pricing" (the admin label wins if set). The value line is "Not 49 documents. 49 agents that act." Drop the vague footnote. Free and Enterprise are unchanged. | Low | Pricing test, discount on and off |
| 3 | P-3 | `config/jobs.json` | "…to a paid simulation" (no number) | Trivial | Careers test |
| 4 | A | hero subheadline, meta description | New subheadline, worded per decisions 3.1 and 3.2 | Low | Render test for the string |
| 5 | B | how-it-works | 5 steps (generate, then act); the grid goes from 4 to 5 columns, stacking on mobile | Medium (layout) | Render test; check at 360px and 1280px on the deployed site |
| 6 | C + E | new `#plan-to-done` section after how-it-works | 4-step loop: draft → refine → send → follow up, with "rolling out" pills per 3.3, plus the 3 cards | Medium (new CSS) | Render test |
| 7 | D | `#proof` | Add a third column, "You're always in control", with 4 bullets (wording per 3.1/3.2) | Low | Render test |
| 8 | F | new `#integrations` section before pricing; FAQ item | "Connect your tools; your agents work through them." What each one unlocks, from §1.4, with rolling-out pills. The FAQ question becomes "What does each integration unlock?" | Low | Render test; the list comes from `integrations.json`, so it can't drift |
| 9 | H(3) | FAQ | 4 control and safety Q&As, worded per 3.1 | Low | Render test |
| 10 | (none) | | Run the full suite and `test_footer.py` (the public-page allowlist); build the zip | | |

**Not changed:** checkout and backend pricing logic, the brand, simi@simulacrumai.io, in-app "Layer 6" orchestrator labels, use-case sample figures, and the Free and Enterprise tiers.

**Testing:** all checks are render tests through the Flask test client. Per standing practice, no local server is started. Visual layout is checked on the deployed site.

---

## 5. Decisions (owner, 2026-10-29)
Option **(a)** for both §3.1 and §3.2: the copy states what is true today, and product behaviour is unchanged.

## 6. Progress
Steps 0–9 are done. `test_site_pricing.py` (45 checks) and the existing landing, careers and pricing tests pass.
- **Step 0.** `app/services/site_context.py` provides:
  - `pricing_context()`, built from `get_current_price()`, the same function checkout's `price_for_user()` wraps;
  - `site_layers()`, with the five layers in one constant and agent counts from agents.json;
  - `integration_unlocks()`, keyed by integrations.json.

  The old `_sim_price_usd` / `_sim_pricing` helpers are removed. When pricing can't be read, the page says "See price at checkout" instead of the $695 config fallback.
- **Steps 1–3.** The ladder renders from `partials/_layers.html`. The FAQ says 5 layers. The card and the FAQ show the same original and current prices. The jobs.json posting no longer names a price.
- **Steps 4–9.**
  - Hero, meta, a 5-step how-it-works, the new `#plan-to-done` section with the 3 cards, the control pillar in `#proof`, the new `#integrations` section, and 4 control and safety FAQs.
  - "Rolling out" pills: Kajabi, Alpaca, and "Run it and tell me".
  - LinkedIn posting is described as rolling out in its text.
- **Wording deviations from the spec,** all for Rule 4:
  - **Hero:** "sends them under your name, sets up the page prospects book calls on … only on the channels you allow". The spec said "from your own account, books the calls … approval at every step".
  - **Card E2:** titled "You decide what runs" instead of "You approve every move", because outreach on an allowed channel doesn't ask per send.
  - **Control bullet 2:** "Works through your tools" instead of "never ours", because email and Apollo run on SimiSI's accounts by default.
  - **FAQ "email without permission":** states that the email channel is on by default.
  - **Unsubscribe:** claimed only for confirmed sends, because the orchestrator's sends carry no unsubscribe footer yet.
- **Remaining:** check the layout on the deployed site at 360 px and 1280 px (the 5-column grid steps to 3, then 2, then 1).
