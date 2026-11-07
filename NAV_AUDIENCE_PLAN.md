# Audience navigation: Phase 1 inventory and plan

Read-only inventory for replacing the flat visitor nav with two audience menus ("For professionals", "For employers") plus Playbook and Pricing. Nothing has been edited yet; this file is the only change.

---

## 0. Decisions needed before editing

| # | Question | Recommendation |
|---|---|---|
| D1 | **Base branch.** `master` doesn't have the design system: its `base.html` has no `ss-nav`, the mascot logo or `system.css`. The live nav exists only on `fix/compliance-risks` (81 commits ahead of master; deployed 2026-11-06). | Branch `feat/nav-audience` from `fix/compliance-risks` (74e293c). A branch from master would rebuild an old nav that no longer ships. |
| D2 | **Signed-in visitors.** The brief describes the visitor nav. Signed-in pages show an app nav (Dashboard, Journal, Partner dashboard, SME Console, Admin, Settings, Watch demo, name, Sign out). | Use the new audience nav for **signed-out visitors only**, and leave the signed-in app nav unchanged. It sits in the same shell, sticky header and logo. |
| D3 | **Employer promo card when the page is in quote mode.** `/transition-package` switches between `design_partner` and `quote` (`enterprise_inquiry_service.page_mode()`, Admin setting `transition_package_mode`). The badge and CTA already change with it. | Follow the page. Design-partner mode uses the brief's card: "Now enrolling design partners", "Priced per seat. Shape the package with us.", "Become a design partner". Quote mode reads **"Request a quote" / "Priced per seat. Tell us your headcount and timing." / "Request a quote"** (draft copy, for you to edit). Both link to `/transition-package#inquiry`. |
| D4 | **Watch demo.** It has a real destination: `[data-demo]` opens the demo video modal (base.html inline script, `DEMO_VIDEO_URL` = `static/media/Simulacrum_Revenue_Engine.mp4` for visitors). | Keep it as a 4th item under **Plan your path**: "Watch the demo", described as **"See SimiSI in action"** (draft copy; you set the wording). It renders as a `<button data-demo>`, because the modal isn't a URL. |
| D5 | **Journal gate.** The current nav hides Journal until the blog has `blog_nav_min_posts` posts (`blog_nav_visible`). `/blog` itself always works. | Keep the gate: "The Journal" shows only when `blog_nav_visible` is true, so the column then has 2 items. |
| D6 | **Analytics.** There is no client-side analytics helper. The existing pattern is server-side `UserEvent.track(None, '<event>', {...})` for anonymous marketing events (the enterprise inquiry uses it; `user_id` has been nullable since sql/062). | Add `POST /api/nav-event` (`navigator.sendBeacon`). It writes `UserEvent.track(None, 'nav_open', {'panel': ...})` or `('nav_click', {'item': ...})`, and only for panel names and item ids that exist in the nav config. No user id, no IP and no referrer are stored. No migration. |
| D7 | **axe.** It isn't installed. `axe-playwright-python` 0.1.8 is on PyPI. | Install it locally as a **test-only** tool (not added to `requirements.txt`). Record a baseline on `/` and `/transition-package` **before** any edit, so that "no new violations" is measured against it. |
| D8 | **Screenshots.** The brief asks for `/docs/screenshots/nav-audience/`. `docs/` is never committed and is excluded from the deploy zip (standing rule). | Save them there as asked. They stay local and are listed in the final report, not committed. |

---

## 1. The shared header

**Template:** `app/templates/base.html`, lines 22–84:
- `{% block announce %}` (used only by the landing page for the Playbook bar);
- `<nav class="ss-nav is-top" id="ssNav" aria-label="Main">` holding `.wrap`, the logo (`partials/_logo.html` → `logo_ui.logo(...)`, mascot SVG + "Simi**SI**"), `ul.ss-nav-links`, `.ss-nav-right` and `#ssMenuBtn`;
- `<div class="ss-drawer" id="ssDrawer">` after the nav.

Signed-in and signed-out each have their own link list, written twice (bar and drawer).

**Pages that use it:** every template that extends `base.html`. That is 72 templates, including:
- the landing page, login and register;
- careers, the blog, `/explore`, `/transition-package`, the samples and the legal pages;
- the dashboard, simulations, settings, admin and the SME guide.

The visitor nav therefore changes on every signed-out page at once.

**Current visitor nav (bar):** The model (`/#model`), How it works (`/#how`), Bio page (`/#bio`), Playbook (`/#playbook`), Pricing (`/#pricing`), For employers (`/transition-package`), Journal (`/blog`, gated), then Log in and Get started free.
**Drawer (phone) adds:** People on SimiSI (`/#people`), Questions (`/#faq`) and Watch demo, which is why the brief lists those two.

**CSS:** `app/static/css/system.css`, `/* ── Nav ── */` lines 164–197:
- `.ss-nav` is sticky at top 0 with z-index 100 and a blurred white background;
- `.is-top` hides the bottom border at the top of the page;
- `.ss-nav .wrap` is a 64px flex row, with `.ss-logo { margin-right:auto }` (added 2026-11-06);
- `.ss-nav-links`, `.ss-nav-right`, `.ss-menu-btn` and `.ss-drawer` follow;
- the bar collapses to the drawer at **≤1000px**.

Tokens already exist in `:root`: `--ink #0D1B3E`, `--ink-2 #46526B`, `--mute #68738C`, `--rule #DCE1E9`, `--gold #C9952A`, `--gold-hi #E4B855`, `--sans` (Archivo), `--wrap 1180px`, `--r-ui 12px`, `--r-btn 8px`. **No new colour variables are needed.** The 14px panel radius is new; it will be one `--r-panel: 14px` unless you'd rather reuse `--r-ui`.

**JS:** inline in `base.html`, lines 122–144:
- sign-out handler;
- a scroll listener toggling `.is-top` (kept as is);
- Menu open/close (`.open` class, label Menu/Close, closes on link click and on Escape).

It has no focus management, no scroll lock and no `hidden` handling. The video modal script follows at line 146 (`[data-demo], #videoDemoBtn`).

---

## 2. Every link, mapped to a real route

| Item | Target | Exists? |
|---|---|---|
| Run a simulation | `/register` (`pages.register_page`). Simulations start from the dashboard after sign-up, and there is no public start route. | ✅ |
| How it works | `/#how` | ✅ `landing.html` `id="how"` |
| Sample simulations | `/#sims` | ✅ `id="sims"` |
| Watch the demo (D4) | `[data-demo]` video modal | ✅ |
| Free bio page | `/#bio` | ✅ `id="bio"` |
| Agents | `/#act` | ✅ `id="act"` |
| SME Console ("In development") | `/#more`. **⚠ No waitlist route exists.** The `#more` card has "Ask for beta access" (mailto simi@simulacrumai.io) or "Open the SME Console" when the beta is on. | ⚠ flagged |
| People on SimiSI | `/explore` (`public.explore`) | ✅ |
| The Journal | `/blog` (`pages.blog_index`), gated by `blog_nav_visible` (D5) | ✅ |
| Questions | `/#faq` | ✅ `id="faq"` |
| Playbook (bar) and the promo card's "Get it on Amazon" | `playbook_url` (context global from `PLAYBOOK_URL`; the Amazon listing), opened in a new tab with `rel="noopener"` like the landing page. **Falls back to `/#playbook` if the setting is empty.** | ✅ |
| Pricing | `/#pricing` | ✅ `id="pricing"` |
| Log in / Get started free | `/login` / `/register` | ✅ |
| What people get | `/transition-package#included` | ✅ |
| Human coaching | `/transition-package#included`. **⚠ No coaching anchor:** coaching is a card inside "What's included". I won't add an id to that page unless you ask. | ⚠ flagged |
| How it works for HR | `/transition-package#how` | ✅ |
| Privacy | `/transition-package#privacy` | ✅ |
| Become a design partner / Request a quote (D3) | `/transition-package#inquiry`. The form section is `id="inquiry"`; the form itself is `#partnerForm`. | ✅ |
| No-JS fallback for the triggers | `/#how` (professionals) and `/transition-package` (employers) | ✅ |

**Removed from the bar** (all still reachable): The model, Bio page, People on SimiSI, For employers (flat), Journal, Questions and Watch demo. The footer link columns are untouched.

---

## 3. Where the nav config lives (one source)

`app/services/site_nav.py` defines `NAV` once. It is a list of groups, each with:
- `id`, `label`, `nojs_href`;
- columns, each with a `label` and items;
- each item: `id`, `title`, `desc`, plus `endpoint`+`anchor`, `config_url`+`fallback`, or `action: 'demo'`, and an optional `gate`;
- the promo card (`title`, `desc`, `link_label`, `href`, and the D3 variant).

Top-level links (Playbook, Pricing) and the two right-hand actions are in the same file.

- `site_nav.resolve()` builds the URLs with `url_for` at request time, applies the gates (`blog_nav_visible`, transition `page_mode`) and returns plain dicts.
- A context processor in `app/__init__.py` exposes `site_nav` to every template, next to the existing `blog_nav_visible` and `playbook_url` processors.
- `app/templates/partials/_site_nav.html` has two macros, `desktop(nav)` and `drawer(nav)`, both looping over that one structure. `base.html` calls them in the signed-out branch.
- The item ids double as the analytics allowlist (D6). A test asserts that every item renders in both places and that no item title is written literally in any template.

---

## 4. Pages with their own header (listed, not changed)

These full-page templates don't extend `base.html` and keep their own top area:

| Template | Its header |
|---|---|
| `public/bio_page.html` | the member's bio page: breadcrumb and on-page TOC |
| `public/bio_editor.html` | editor top bar (left title, right actions) |
| `public/bio_teaser.html`, `public/profile.html`, `public/profile_unpublished.html` | private-bio teaser and legacy profile pages, no site nav |
| `onboarding/wizard.html` | wizard header and progress bar |
| `corporate/invite.html` | invite landing, no site nav |
| `simulations/export.html` | print/PDF export |
| `simulations/share_roi.html` | share card |

The other `<nav>` elements on base pages are on-page tabs, not site menus, and are not changed: `tp-subnav` on /transition-package, `ss-subtabs` on feed and chats, the settings nav, the admin subnav, and the sample-page progress bars.

**Noted, not fixed (pre-existing):** `/transition-package`'s `.tp-subnav` is also sticky at `top:0` (z-index 40), so it slides under the main nav when you scroll. The new panels overlay both. Say if you want it moved below the 64px bar in a separate commit.

---

## 5. Behaviour

**Desktop (≥900px):**
- Bar: logo · For professionals ▾ · For employers ▾ · Playbook · Pricing … Log in · **Get started free** (gold).
- The two triggers are rendered as `<a href="nojs_href">`. The JS module swaps each for a `<button aria-expanded aria-controls>` (progressive enhancement), so with JS off they are plain links.
- Panels sit `position:absolute` under the bar, inside the page container (max-width 1180px). They are white, with a 1px `--rule` border, 14px radius, soft shadow and 28px padding, and they use the `hidden` attribute when closed.
- A fixed scrim (navy at 8%) sits below the nav's z-index. Nothing in the page flow moves, so the hero doesn't shift.
- Panels open on click, Enter or Space; one at a time; clicking the open trigger closes it.
- They close on Escape (focus returns to the trigger), on a click on the scrim or anywhere outside, on choosing a link, and on resizing below 900px.
- The open trigger is navy with a 2px gold underline and its chevron rotated up.

**Phone (<900px):**
- Bar: logo and Menu/Close.
- Drawer, in order:
  1. For professionals (an expandable `<button aria-expanded>`, open by default)
  2. For employers (expandable)
  3. Playbook
  4. Pricing
  5. full-width **Get started free** (gold)
  6. full-width **Log in** (outline)
- Expanded sections show titles only.
- While open, the page behind is scroll-locked. Focus moves to the first control on open and back to Menu on close; Escape closes it.

**Signed in:** unchanged links and drawer (D2). That nav keeps its ≤1000px breakpoint, because it has up to seven links plus a name.

**Motion:** a 120ms opacity fade on panels and the drawer, removed under `prefers-reduced-motion: reduce`. Every control has a visible `:focus-visible` ring.

**Code:** `app/static/js/site_nav.js` is a small vanilla IIFE with no framework, deferred from `base.html`. The old inline drawer script stays for the signed-in drawer only.

---

## 6. Ordered edits (after approval). One commit each; tests after each.

| # | What | Files | Risk | Verify |
|---|---|---|---|---|
| 0 | Baseline: axe on `/` and `/transition-package`, 1440/390 screenshots of the current nav | `.ds_tmp/` (scratch) | — | Baseline violation list saved |
| 1 | Nav config, `resolve()`, context processor | `app/services/site_nav.py`, `app/__init__.py` | Low | Unit test: every endpoint resolves; gates hide Journal and switch the D3 card; no prices in the config |
| 2 | Partial + `base.html` visitor branch (bar, panels, drawer from the one config; no-JS anchors) | `partials/_site_nav.html`, `base.html` | Medium (every public page) | Test client: `/`, `/login`, `/careers`, `/explore`, `/blog`, `/transition-package` render; each item id appears once in the bar and once in the drawer; signed-in nav unchanged; `test_mascot.py` and `test_transition_page.py` still pass |
| 3 | Styles | `system.css` (Nav section; reuses existing tokens) | Medium | 1440 / 1100 / 900 / 390 renders, no horizontal scroll, contrast audit |
| 4 | Behaviour | `static/js/site_nav.js` | Medium | Playwright 1440: click opens, one at a time, Escape returns focus, outside click closes, a link click closes. Playwright 390: drawer, sections, scroll lock, focus in and out. Keyboard-only pass |
| 5 | `nav_open` / `nav_click` events | `POST /api/nav-event` in the pages blueprint, using `UserEvent.track(None, ...)` | Low | Allowlisted ids are stored; unknown ids are rejected (400); no IP or user id on the row |
| 6 | Browser test committed as `test_site_nav_browser.py` (renders through the test client to files and drives Playwright; skips if Playwright is missing) plus a link check that every href returns 200 and every `#anchor` exists on its page | test files | Low | Green |
| 7 | Full suite, axe compared with the baseline, site render and contrast audit, screenshots to `docs/screenshots/nav-audience/` (1440: each panel open; 390: drawer open), hero layout-shift check | — | — | Report |

**Not touched:** homepage content, layer names, pricing, legal copy, the footer, the signed-in nav, the pages in §4, the brand, the mascot logo and simi@simulacrumai.io. The nav contains no prices.

---

## 7. Results (2026-11-06)

- **Tests:** `test_site_nav.py` 36/36; `test_site_nav_browser.py` 76/76 (1440 and 390 px, keyboard only, every nav link returns 200 and every `#anchor` exists, no JS fallback, no console errors, no hero shift). Full suite 43/43 files.
- **axe** on `/` and `/transition-package`, each panel open (1440), drawer open (390) and closed: **no violations beyond the pre-change baseline** (taken at 74e293c). The first run flagged the open drawer as outside a landmark; the visitor drawer is now `<nav aria-label="Menu">`. Pre-existing findings are unchanged: the gold "SI" in the logo (brand colour), a few homepage text contrasts, heading order, and an empty table header on /transition-package.
- **Site render and contrast audit** across 73 pages: same flags as before the change; no horizontal overflow at 390 px.
- **Screenshots:** `docs/screenshots/nav-audience/1440-professionals-open.png`, `1440-employers-open.png`, `390-drawer-open.png` (local only; `docs/` is not committed).

---

## 8. Signed-in members: grouped menus (2026-11-06, branch `feat/nav-member`)

Chosen option: grouped menus in the same panel style. Config: `MEMBER_GROUPS`, `MEMBER_ROLE_LINKS` and `MEMBER_ACCOUNT` in `app/services/site_nav.py`; `resolve_member()` is exposed to templates as `member_nav()`.

- **Bar:** Dashboard · My work ▾ · Community ▾ · role links (Partner dashboard, SME Console, Admin, each only for that role) … the member's name ▾.
- **My work:** Simulations, My bio page (the member's own editor, or Settings → Profile until they have a username), Resumes, Contacts, SME guide (only when the guide beta is on for them).
- **Community:** Feed, People on SimiSI, My chats, The Journal (blog nav gate), Write a post (only when they're allowed to post).
- **Account (name ▾):** Settings, Integrations, Notifications, Watch the demo, About Simi, then Sign out below a divider.
- **Phone (≤1000px, unchanged breakpoint):** Dashboard, My work (open), Community, role links, Account (collapsed), a full-width Sign out, "Signed in as …".
- Compact panels open under their trigger; the account panel is right-aligned. Same behaviour as the visitor nav (click/Enter/Space, one at a time, Escape returns focus, outside click closes). Member panel and item ids join the `nav-event` allowlist.
- The old inline drawer script in `base.html` is gone; both navs use `js/site_nav.js`.
- Descriptions on member items are draft copy.

**Results:** `test_site_nav.py` 53/53, `test_site_nav_browser.py` 102/102 (member checks at 1440, 1100 and 390 px). axe on the dashboard and admin pages, closed and with each menu or the drawer open: no violations beyond the baseline at 0321448. Screenshots in `docs/screenshots/nav-member/` (local only).
