# Transition Package marketing page: Phase 1 inventory and plan

Source: `docs/CLAUDE_CODE_PROMPT_Transition_Package_Page.md`. Branch: `feat/transition-package-page`, cut from `feat/transition` (it links to the Transition Package built there). Phase 1 only: no code has been changed.

## 0. Blocker
**The design and copy source is missing.** The prompt says to port `/docs/marketing/simisi_transition_package_page.html` verbatim, but there is no `docs/marketing/` folder, and no mockup HTML anywhere in the repo. Without it I can't keep the copy verbatim or match the approved design. **Please add the file.** Everything below can proceed as soon as it's there.

## 1. Homepage and the Enterprise price card
The homepage is `app/templates/landing.html`, rendered by `pages.index`. It's a **standalone page** with its own `<head>`, nav and inline Midnight Gold CSS. It does not extend `base.html`.

The Enterprise card is at `landing.html:1248-1264`. In summary:
```html
<div class="price-card fade-up d3" style="border:2px solid var(--navy);background:var(--navy);color:#fff;">
  <div class="price-badge-row"><span class="price-badge" …>Enterprise</span></div>
  <div class="price-tier" …>Enterprise</div>
  <div class="price-amount" style="color:#fff;">Custom</div>
  <div class="price-per" …>per seat · annual contract</div>
  <div class="price-features"> … 7 feature rows … </div>
  <a href="mailto:simi@simulacrumai.io?subject=Corporate%20Licensing%20Enquiry" class="btn" …>Contact sales →</a>
</div>
```
**Plan:** add one small text link, "See the Transition Package →", to `/transition-package`, directly under the "Contact sales →" button, styled to the card's muted white.
- Nothing else in the card changes: the price text, the features, and "Contact sales" stay as they are.
- The `fade-up d3` classes and the scroll animation are untouched. The link is a child element, and the animation targets the card.
- The other two price cards are not touched.

## 2. Base template and shared CSS
- **`base.html`** is the app shell. It provides the navbar (logged-in and anonymous variants), the `head` / `content` / `footer` / `scripts` blocks, and `static/css/main.css`, a light theme. Public pages extend it and include `partials/_footer_public.html`: careers, blog, legal, partners.
- **Midnight Gold** (navy and gold, Playfair Display headings, `lp-s` / `lp-dark` / `s-label` / `btn-gold` / `fade-up`) lives only in `landing.html`'s inline `<style>`.

**Plan:**
- `templates/marketing/transition_package.html` extends `base.html`, as the prompt requires, and gets the site nav and `_footer_public.html`.
- A new `static/css/marketing.css` carries the Midnight Gold tokens and section, button and `fade-up` styles the page needs. They're copied from `landing.html`, which I won't refactor (a 1,500-line page, not worth the risk here). The page loads it in `{% block head %}`.
- *Risk:* the two copies of the tokens can drift. They're flagged in a comment in both files.

## 3. Existing inquiry endpoints and tables: reuse or create?
| Candidate | Fit |
|---|---|
| `profile_inquiries` (`ProfileInquiry`) | The visitor-to-bio-owner contact form. Wrong owner and wrong audience. |
| `admin_prospects` (`AdminProspect`, admin CRM) | Staff's outbound pipeline of firms, with stages, follow-ups and onboarding to an organization. Not an inbound log, but the natural next step for a qualified inquiry. |
| "Contact sales" on the homepage | Just a `mailto:`. No endpoint. |
| Partners apply (`/partners/apply`) | Referral-partner applications. A different program. |

**Plan:** create `enterprise_inquiries` as specified. In the admin list, an **"Add to prospects"** action turns an inquiry into an `AdminProspect` (category `transition`, contact path `form`) so it enters the existing sales pipeline. This reuses the CRM without making it the inbound log.

## 4. How admin settings are stored
- Settings are `platform_settings` key/value rows (`PlatformSetting.get(key, default)` / `.set`), edited through `PUT /api/admin/settings/<key>`. A missing row means the code default.
- New keys:

| Key | Default |
|---|---|
| `transition_package_mode` | `design_partner` \| `general` (default `design_partner`) |
| `enterprise_inquiry_notify_email` | `simi@simulacrumai.io` |
| `enterprise_inquiry_rate_limit` | `5` per IP per hour |

## 5. Gaps and decisions (my proposals)
1. **CSRF.** The app has no CSRF framework (no Flask-WTF). **Proposal:** a per-session token for this form only: a hidden field checked against the session with `hmac.compare_digest`. No new dependency.
2. **Rate limiting.** The careers form uses an in-memory per-process counter, which is weak on multi-process Passenger. **Proposal:** count recent rows in `enterprise_inquiries` by `ip_hash` (a SHA-256 of the IP, never the raw IP). It persists across processes, and the limit is an admin setting.
3. **Analytics event.** There's no browser-side analytics. Events are server-side: the `page_views` middleware, and `UserEvent.track()`, which **requires a `user_id`**, while inquiries come from anonymous visitors. **Proposal:** make `user_events.user_id` nullable (migration) and record `transition_package_inquiry` with no user and no personal data, only `{source, mode}`. *Alternative:* record a pseudo page view at `/events/transition_package_inquiry` with no migration, but that mixes events into page-view counts.
4. **IDs.** The prompt says `generate_pk()`, 9 characters, **uppercase** alphanumeric. The repo's generator is `utils.id_gen.generate_id()`: 9 characters, **mixed case** (e.g. `L3JxpP2ms`), used for every table. **Proposal:** use `generate_id()` for consistency.
5. **Admin console placement.** **Proposal:** Admin → Growth → **Inquiries** (`/admin/inquiries`), with filters for status (new / contacted / closed), plus status controls and "Add to prospects".
6. **Honeypot.** A hidden `website` field that people never see. A filled-in honeypot gets the same success response, with nothing stored and nothing sent.

## 6. Ordered edits (after approval and once the mockup is added)
| # | What | Files | Risk | Verification |
|---|---|---|---|---|
| 1 | `EnterpriseInquiry` model and migration (`sql/062_enterprise_inquiries.sql`): id, name, work_email, company, role, headcount_band, timing, notes, source, status, ip_hash, created_at, contacted_at. `user_events.user_id` nullable (decision 3). | `app/models/enterprise_inquiry.py`, `models/__init__.py` | Low (additive) | `create_all` in tests |
| 2 | Service: validation (email syntax, required fields, length caps, allowed bands), CSRF token, honeypot, DB rate limit, notify email, event | `app/services/enterprise_inquiry_service.py`, `email_service.py` | Low | Unit tests |
| 3 | Routes: `GET /transition-package` (mode-aware) and `POST /enterprise/inquiry` (JSON or form post; thank-you in place of the form) | `app/blueprints/pages/routes.py` (public pages live there) | Low | Route tests |
| 4 | Page: port the mockup verbatim. Drop the mockup banner and the fake-submit JS. Both modes' copy lives in the template, behind `{% if mode == 'design_partner' %}`. "Per-seat quote" only. The HR report is labelled "Illustrative data". | `templates/marketing/transition_package.html`, `static/css/marketing.css` | Medium (layout) | Render test; check at 390 px for no horizontal scroll and no console errors (on the deployed site) |
| 5 | Homepage link in the Enterprise card | `landing.html` | Low | Render test: the card still has its price text, features and "Contact sales", plus the new link; the other cards are unchanged |
| 6 | Sitemap entry and title/meta from the mockup | `static/sitemap.xml` | Trivial | `/sitemap.xml` test |
| 7 | Admin: inquiries list, status controls, "Add to prospects"; settings card for mode, notify email and rate limit | new `templates/admin/inquiries.html`, `partials/_admin_subnav.html`, admin routes | Low | Admin render test; status update test |
| 8 | Tests: 200 on the page; invalid email rejected; honeypot not stored and not emailed; a valid post creates exactly one row and one notification; the rate limit holds; a bad CSRF token is rejected; the mode flag swaps the badge and CTAs and removes the perks | `test_transition_page.py` | (none) | Full suite |

**Copy honesty, kept as the rules require:** "sent only on their approval", "never moves anyone's money", "Illustrative data" on the sample HR report, no invented logos or testimonials, the brand, simi@simulacrumai.io, and `/legal/privacy` and `/legal/terms`. Submitted data is emailed only to the admin-set notify address.

## 7. Needed from you
1. **Add `docs/marketing/simisi_transition_package_page.html`** (the blocker).
2. Approve or change decisions 1–6 in §5. Decision 3, the analytics event, needs a one-line migration.
