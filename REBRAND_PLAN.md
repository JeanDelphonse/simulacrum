# REBRAND PLAN — Simulacrum → SimiSI (simisi.io)

Phase 1 inventory. Read-only scan of all git-tracked files on branch `rebrand/simisi`
(534 files). No other files were edited.

> **Pre-existing uncommitted work.** `app/templates/landing.html` already had uncommitted
> changes when I created this branch (a mascot/sign-in rework that adds
> `aria-label="Sign in to Simulacrum"`), and the untracked `test_mascot.py` asserts on that
> string. Both came onto this branch with me. **Ruling needed (R0):** should that work be
> committed on its own first, so the rebrand diffs stay clean?

---

## 1. Variant inventory

Counts are whole-word matches in tracked files. Area key: **py** = `app/` + `utils/` Python,
**tpl** = `app/templates`, **static** = `app/static`, **config** = `config.py`, `config/*.json`,
`.env.example`, `.gitignore`, **root** = top-level scripts, **tests** = `test_*.py`,
**docs** = `docs/` + top-level `.md`, **sql** = `sql/` + `migrations/`.

| Variant | Count | Where |
|---|---:|---|
| `Simulacrum` (display word) | 401 | py 121, tpl 100, docs 161, config 6, static 5 (header comments), sql 4 (comments), tests 3, root 1 |
| `simulacrum` (lowercase; mostly identifiers) | 131 | py 50, docs 36, root 17, tpl 13, tests 10, config 4, sql 1 |
| `SimulacrumAI.io` (wordmark) | 90 | py 73 (mostly email copy), tpl 11, docs 3, tests 2, config 1 |
| `https://simulacrumai.io/...` (web URLs) | 60 | static 16 (sitemap), py 14, docs 12, tpl 9, config 6 (OAuth + BASE_URL), tests 3 |
| `simi@simulacrumai.io` (**protected email**) | 32 | tpl 15, py 12, docs 3, config 2 |
| bare `simulacrumai.io` (no scheme) | 21 | tpl 9, docs 8, py 2, config 1, sql 1 |
| `SIMULACRUM` | 7 | docs 5, tpl 1 (`share_roi.html` badge), root 1 (`SIMULACRUM_PING_URL` env key) |
| `Simulacrum AI` | 6 | py 2, tpl 3, docs 1 |
| `SimulacrumAI` (handle/hashtag) | 2 | `@SimulacrumAI` (wizard share), `#SimulacrumAI` (voice share) |
| `reply-{contact_id}@mail.simulacrumai.io` | 3 | py 1, docs 2 |
| `simulacrum.io` (stale domain) | 3 | docs only |
| `simulacrum.ai` / `simulacrum.app` (stale domains) | 2 | `simulations/export.html`, `simulations/share_roi.html` |
| `noreply@simulacrum.ai`, `admin@simulacrum.ai` | 2 | `.env.example`, `create_admin.py` |
| **Filenames** containing `simulacrum` | 25 | 1 icon, 2 mp4s, 2 sample templates, 18 `docs/*.docx`, 1 docs png, 1 docs html |

Top files by hit count: `app/services/email_service.py` (117), `docs/homepage_preview.html` (25),
`project_review_report.md` (23), `landing.html` (21), `stripe_connect_service.py` (18, all
metadata keys), `sitemap.xml` (17), `settings/index.html` (16).

---

## 2. Classification

### (a) BRAND/DISPLAY — safe to change

User-facing copy, titles, meta, alt/ARIA text, email bodies and subjects, generated content,
LLM prompt copy, and web links. These make up the bulk: about 300 code/template hits, all
listed by surface in §3.

### (b) EMAIL / MAIL-CONTEXT — must NOT change (please confirm)

All 32 `simi@simulacrumai.io` occurrences stay byte-for-byte:

| File | Lines |
|---|---|
| `config.py` | 18 (`MAIL_DEFAULT_SENDER` default), 25 (`FOUNDER_EMAIL` default) |
| `app/blueprints/admin/routes.py` | 571 |
| `app/services/action_step_service.py` | 533 |
| `app/services/admin_crm_service.py` | 584 |
| `app/services/consulting_outreach_service.py` | 481 |
| `app/services/notification_service.py` | 135 (`From('simi@simulacrumai.io', …)`) |
| `app/services/outreach_campaign_service.py` | 570, 723 |
| `app/services/outreach_email_service.py` | 50 (sandbox address) |
| `app/services/email_service.py` | 661, 671 (body copy "email simi@…"), 972 (recipients) |
| `app/templates/landing.html` | 1210 (`mailto:` corporate licensing) |
| `app/templates/legal/privacy.html` | 86, 100, 116 (×2) |
| `app/templates/legal/terms.html` | 103 (×2), 104 (×2) |
| `app/templates/public/unsubscribe.html` | 14 (×2), 21 (×2) |
| `docs/*` | 3 (PRD text) |
| untracked `send_checklist.py` | 25, 87 (not in git; listed for completeness) |

Other mail-context `simulacrumai.io` occurrences I will also leave alone:

| File:line | String | Why |
|---|---|---|
| `app/services/outreach_email_service.py:101` | `INBOUND_PARSE_DOMAIN` default `mail.simulacrumai.io` | SendGrid Inbound Parse MX host; reply tracking breaks if it moves |
| `app/blueprints/integrations/routes.py:2137` | `reply-{contact_id}@mail.simulacrumai.io` (docstring) | Same inbound-parse domain |
| `app/services/outreach_email_service.py:99` | comment "Reply-to routes through Simulacrum" | Comment next to mail plumbing. It's harmless to change, but I'll leave it unless told otherwise |

### (c) TECHNICAL IDENTIFIERS — leave by default; listed for your decision

| # | Identifier | Locations | Why I'd keep it |
|---|---|---|---|
| C1 | Stripe / PandaDoc / Kit **metadata keys** `simulacrum_simulation_id`, `_action_id`, `_user_id`, `_action_type`, `_layer_number`, `_artifact_id`, `_income_type`, `_layer`, `_contact_id` | `stripe_connect_service.py`, `cal_service.py`, `pandadoc_service.py`, `wire_service.py`, `email_campaign_service.py`, `integrations/routes.py`, `payments/routes.py` | Stored on live external objects. Webhook readers would miss in-flight payments/docs (income attribution, FR-STRIPE-05) |
| C2 | Kit/ConvertKit **tags** `simulacrum:{action_type}`, `simulacrum_lead`; settings placeholder `simulacrum_` tag prefix | `wire_service.py:192,227`; `settings/index.html:1586` | Existing subscriber segmentation in users' Kit accounts |
| C3 | Email **headers** `X-Simulacrum-Simulation/-Contact/-Step` | `outreach_email_service.py:80-82` | Parsed by SendGrid webhooks for event attribution |
| C4 | Embed attribute `data-simulacrum-card` | `public/embed_card.js:32,34`, `bio_editor.html:790`, `public/routes.py:593` | Already pasted on third-party sites. Changing it breaks live embeds (could support both later) |
| C5 | UTM value `utm_source=simulacrum_bio` | `bio_page.html:1028` | Keeps the analytics series continuous for users' booking tools |
| C6 | ElevenLabs voice name `simulacrum_{slug}` | `elevenlabs_service.py:29` | External resource name; not shown to end users |
| C7 | DB names `simulacrum.db`, `simulacrum_dev.db`, MySQL db `simulacrum` | `config.py:71,79`, `.env.example:7`, `migrations/sql/023…` | Data location |
| C8 | Deploy plumbing: `/home/…/public_html/simulacrum` paths, WSGI prefixes `/simulacrum` and `/simulacrumai`, `simulacrum_error.log`, `SIMULACRUM_PING_URL` env key, `SimulacrumKeepalive/1.0` UA, `/simulacrum/ping` | `wsgi.py`, `wsgi_app.py`, `passenger_wsgi.py`, `cron_*.py`, `test_email.py`, `test_outreach_email.py` | Server filesystem and cPanel routing |
| C9 | Deploy artifact `simulacrum_deploy.zip` + `.gitignore` entry | `build_zip.py:3`, `.gitignore:20` | You asked for this exact zip name (standing rule) |
| C10 | Template filenames `samples/simulacrum_usecase_{marcus,maya}.html` | `pages/routes.py:824,829`, `test_footer.py:137-138` | Internal paths; the public URLs are `/samples/marcus` and `/samples/maya` and don't include them |
| C11 | Asset filenames `static/icons/simulacrum-icon-512.png`, `static/media/Simulacrum_Career_as_Asset.mp4`, `Simulacrum_Revenue_Engine.mp4` | `_favicon.html:15`, `pages/routes.py:160` (manifest), `landing.html:16,26,1366`, `base.html:93`, `test_favicon.py` | The rename itself is safe if every reference moves together. But scrapers and social caches hold the old og:image URL. The MP4s **show the old brand** (manual re-render) |
| C12 | Python module/class/function names | none found | — |
| C13 | Applied SQL migrations: comments in `sql/create_tables.sql`, `patch_v2.sql`, `048`, `049`, `migrations/sql/023`; `LIKE '%simulacrumai.io/simulations%'` in `sql/036` | sql/ | **Never touch** (Rule 5) |

### (d) AMBIGUOUS — needs your ruling

| # | Item | Location | My recommendation |
|---|---|---|---|
| D1 | Email **sender display name** `MAIL_DEFAULT_SENDER_NAME` default `'SimulacrumAI.io'` (the From *name*, not the address) | `config.py:19` + 4 fallback copies in `admin/routes.py`, `outreach_campaign_service.py`, `email_service.py`, `test_outreach_email.py` | Change to `SimiSI`. The address stays `simi@simulacrumai.io`. Note: if prod sets the env var, cPanel must be updated too |
| D2 | **OAuth redirect URI defaults** (LinkedIn ×2, Google, Apollo, PandaDoc) | `config.py:42-55` | **Hold until simisi.io is live and the new URIs are registered with each provider.** A mismatch breaks login/connect. Ship as a separate last batch |
| D3 | `BASE_URL` default + hardcoded `https://simulacrumai.io` fallbacks | `config.py:56`, `layer6.py:1004,1117`, `outreach_campaign_service.py:24`, `qr_service.py:56`, `notification_service.py:87`, `peer_service.py:1810`, `bio_privacy_service.py:539`, `admin_crm_service.py:585`, `email_service.py` (6 links), `utils/action_items.py:291` | Change, but **only after the 301 is live**. New QR codes encode BASE_URL. Already-generated cached QR PNGs keep the old URL and still work through the redirect |
| D4 | SEO: canonical, og:url, og:image, twitter:image, sitemap `<loc>` ×16 | `landing.html:9,16,21,26`, `sitemap.xml`, `bio_seo.py` | Same gate as D3. Pointing canonicals at a domain that doesn't resolve yet would hurt indexing |
| D5 | Stale/wrong domains: `simulacrum.ai` (PDF export footer), `https://simulacrum.app` (ROI share CTA) | `simulations/export.html:38`, `share_roi.html:59` | Change both to `simisi.io` (they're already broken links) |
| D6 | Non-protected emails `noreply@simulacrum.ai`, `admin@simulacrum.ai` | `.env.example:19`, `create_admin.py:8` | Leave: they're mail context. Note that `.env.example` probably should show `simi@simulacrumai.io` |
| D7 | **Social handle / hashtag** `@SimulacrumAI`, `#SimulacrumAI` | `onboarding/wizard.html:1257`, `voice/routes.py:594` | Does an `@SimiSI` handle exist? If not, drop the @-mention rather than tag someone else's account |
| D8 | **TTS pronunciation**: video scripts say "simulacrum AI dot IO" | `voice/routes.py:264`, `settings/index.html:2238` | How should "SimiSI" be spoken? e.g. "simmy S I dot I O" or "simmy-see dot I O" |
| D9 | **Legal pages** (Terms, Privacy): "operates Simulacrum at simulacrumai.io", "6.1 Simulacrum IP", etc. | `legal/terms.html` (13), `legal/privacy.html` (9) | Change the product name and domain. Bay Area Experiences Ventures stays the legal entity. Do you want a "Last updated" date bump and/or a "formerly Simulacrum" line? Legal review is advisable |
| D10 | **Downloaded filenames**: `simulacrum_{id}.pdf`, `simulacrum_{id}_L{n}_{type}.txt`, `Simulacrum_{type}_…zip`, `{slug}-simulacrum-qr.png`, deck fallback `'Simulacrum'` | `simulations/routes.py:482,979`, `detail.html:486`, `tasks/bundle.py:228`, `bio_editor.html:451`, `bio_page.html:1071,2816` | Change to `simisi_…`. Check: is the bundle zip filename stored in the DB and used to find the file later? If so, keep old names readable (I'll verify before editing) |
| D11 | **DB-seeded copy**: outreach drip templates are seeded into the DB (`seed_default_templates`). Existing rows keep "Simulacrum" even after a code change. Stored artifacts, bios, email snapshots and platform_settings also hold old text | `outreach_campaign_service.py:63-133` | Code change covers only new seeds. Updating the live rows needs either an admin edit in /admin/outreach or a **data migration (new SQL file)**. Which do you want? |
| D12 | **LLM system prompts** naming the product (Simi co-pilot, chat co-pilot, member chat, admin CRM pitch, discovery thesis) | `simi_service.py:331`, `chat_service.py:19`, `social/routes.py:29,38`, `admin_crm_service.py:213-237`, `discovery_service.py:220` | Change. The models will then say "SimiSI" to users. No logic change |
| D13 | **Code comments / docstrings / file-header comments** (≈30, e.g. `main.css`, `sse.js`, `model_router.py`, `calibration_cohort.py`) | various | Leave. Not user-facing, and churning them adds diff noise. Happy to sweep them if you want zero survivors |
| D14 | **docs/** historical PRDs (32 text/html files, 18 `.docx` with brand in the filename), `project_review_report.md`, `walkthrough.md` | docs/ | Leave the historical PRDs and .docx filenames as records (`careers_service.py`, `config/jobs.json`, `sql/048` cite `docs/Simulacrum_Team_Job_Specs.docx` by path). Update living docs only. There is no README |
| D15 | Mascot naming in the uncommitted landing work: `title="Simulacrum — walks in…"`, `aria-label="Simulacrum mascot"` | `landing.html` (working copy), `test_mascot.py` | The mascot is "Simi". Should the ARIA text read "SimiSI" or stay "Simi"? |

---

## 3. Surfaces to update (category (a) plus any approved (d))

**UI chrome / components**
- `base.html`: `<title>` default, logo text `SimulacrumAI.io`, footer ©, feedback modal copy (3)
- `partials/_footer_public.html`: wordmark + ©
- `social/feed.html`, `social/my_chats.html`: titles, `.brand` wordmark, empty-state and event copy
- `auth/login.html`, `auth/register.html`, `auth/verify_email.html`: subtitles and titles
- `dashboard/index.html`, `partners/dashboard.html`, `resumes/list.html` (consent copy), `simulations/layer6.html` (share caption)
- `settings/index.html`: `simulacrumai.io/u/<username>` previews (×2), "Powered by Simulacrum" tier label, integration and consent copy (≈10)
- `blog/index.html`, `blog/post.html`, `blog/editor.html`, `admin/blog.html`: "The Simulacrum Journal" → "The SimiSI Journal"
- `careers/index.html`, `careers/detail.html`, `config/jobs.json` (about/shared_note copy)
- `corporate/invite.html`, `corporate/routes.py` (invite emails)
- `config/integrations.json` `benefit_copy` / `advanced_note`
- Flash and error strings: `peer/routes.py:186`, `pages/routes.py:1198`, `public/routes.py:570` (console warn)

**Marketing / landing**
- `landing.html`: title, description, og/twitter titles, wordmark, hero/lead copy, FAQ, "On Simulacrum" label, sign-in ARIA (≈15)
- `onboarding/wizard.html`: title, wordmark, URL hint, share text (D7)
- `public/explore.html`, `public/simi_help.html`, `samples/simulacrum_usecase_{marcus,maya}.html` (copy only)

**Bio page and embeds**
- `public/bio_page.html`: "Built with / Powered by Simulacrum", badge link, chat widget link
- `public/bio_teaser.html`, `public/bio_editor.html` (title, badge toggle label), `public/profile.html`, `public/profile_unpublished.html`, `public/unsubscribe.html` (copy only; the emails stay)
- `public/embed_badge.js`, `public/embed_card.js`: "Powered by" text and links (the attribute stays, C4)
- `services/bio_seo.py`: `SITE_NAME`, description template

**Email template bodies (copy only; the from-address doesn't change)**
- `services/email_service.py`: about 105 copy hits (subjects, preheaders, bodies, `— SimulacrumAI.io` sign-offs, footer). Keep lines 661/671/972
- `outreach_campaign_service.py` (drip copy + HTML shell), `notification_service.py` (digest copy + footer; the `From` address stays), `bio_privacy_service.py`, `tasks/bundle.py`, `simulations/routes.py` (share/artifact emails), `admin/routes.py` (test email), `action_step_service.py` / `consulting_outreach_service.py` (fallback from-*name* only)

**Generated artifacts / reports / exports / video**
- `simulations/export.html` footer (D5), `share_roi.html` badge `SIMULACRUM ROI REPORT` → `SIMISI ROI REPORT` + CTA (D5)
- `video_renderer.py`: burned-in `SimulacrumAI.io` footer and drawtext
- `stripe_service.py`: line-item names "Simulacrum Pro Simulation", "… credits", "Voice Training — Simulacrum AI" (text only, **no price change**)
- `email_campaign_service.py:154` campaign name, `tasks/bundle.py:191` bundle name, `models/feedback.py` "Simulacrum User" fallback, `peer_service.py` "A Simulacrum member"
- Download filenames (D10), LLM prompts (D12)

**SEO**
- `landing.html` canonical, og:*, twitter:* (D4); `explore.html` title/og; `sitemap.xml` (16 locs, D4); `bio_seo.py`
- `robots.txt` (`pages/routes.py:172`) is built dynamically from `bio_seo.canonical_origin(request.url_root)`, so there's no hardcoded domain. It follows whichever host serves it. The bio-page sitemap entries are generated the same way

**Manifest / favicon / logos**
- `pages/routes.py:154-155` web manifest `name` / `short_name` → `SimiSI`
- Icon filename (C11), favicon partial, `test_favicon.py` assertions
- `alt` text: no `alt` contains the brand; ARIA in `landing.html`

**Docs**
- Living docs only, per D14

**Config / constants holding a display name or web URL**
- `config.py` `MAIL_DEFAULT_SENDER_NAME` (D1), `BASE_URL` (D3), OAuth URIs (D2)
- `outreach_campaign_service.py:24` `BASE_URL`, `bio_seo.SITE_NAME`
- Tests that pin strings: `test_bio_seo.py:48`, `test_favicon.py`, `test_outreach_email.py`, `test_email.py`, `test_mascot.py`

---

## 4. MANUAL / INFRA — do not attempt in code

1. Register **simisi.io** and add it to cPanel as an addon/alias domain with SSL (AutoSSL) on the same Passenger app.
2. **301-redirect** `simulacrumai.io/*` → `simisi.io/*`, preserving path and query. Old bio links, QR codes, embeds and emails depend on this.
3. **Keep simulacrumai.io's MX, SPF, DKIM, DMARC and SendGrid domain authentication** so `simi@simulacrumai.io` keeps sending and receiving. Keep the `mail.simulacrumai.io` Inbound Parse MX record. Make sure the web 301 doesn't break the DNS records.
4. Set the cPanel env vars after the domain is live: `BASE_URL`, `MAIL_DEFAULT_SENDER_NAME` (if set), and every `*_REDIRECT_URI`.
5. Register the new OAuth redirect URIs with **LinkedIn (×2), Google, Apollo, PandaDoc**. Keep the old ones until the cutover is done.
6. Update **Stripe** (business/statement descriptor, Checkout branding, Connect platform name, webhook endpoint URLs), **SendGrid** (event webhook and inbound-parse POST URLs, link branding), **Kit/ConvertKit**, **Cal.com**, **ElevenLabs** and **Apollo** dashboards if they show the brand or call back to the old domain.
7. Analytics / Search Console: add the simisi.io property, submit the new sitemap, use the Change of Address tool.
8. Social: claim or rename handles (D7). Update the LinkedIn company page.
9. Re-render the brand assets: icon PNG (if it shows the wordmark), demo MP4s, playbook cover `static/img/playbook-cover.jpg`, and any OG images.
10. Third-party embeds already installed keep working through the 301 (C4).
11. After code ships: decide on the DB-stored copy (D11), and optionally regenerate cached bio QR codes.

---

## Rulings I need before Phase 2

- **R0**: what to do with the uncommitted `landing.html` work and `test_mascot.py`
- **(b)**: confirm the protected email list
- **C1–C13**: keep all? (my default: yes; C11 icon rename optional)
- **D1–D15**: see the recommendations above. D2/D3/D4 depend on DNS, so they'd ship as a final "domain cutover" batch.

---

# PHASE 2 — Edit batches

## Verification gate (runs after every batch)

There is no build step (Flask + Jinja), so "build" here means:

1. **Import/compile:** `python -m compileall -q app utils config.py`, plus a `create_app()` smoke import.
2. **Template parse:** load every `app/templates/**` file through the app's Jinja env, so a broken
   tag fails the gate even on a page no test renders.
3. **Test suite:** all 18 `test_*.py` suites (they use the Flask test client; no server started).
   - **Baseline on this branch before any edit: 18/18 suites green, 1,458 checks passed, 0 failed, 1 skipped.**
   - `test_email.py` and `test_outreach_email.py` are excluded because they send real mail.
     Their brand copy is still edited in the batches.
4. **Survivor grep:** `git grep -i simulacrum` scoped to the batch's files. Every hit must be
   (b), (c), or deferred to a later batch.
5. **Protected-email check:** `git grep -c "simi@simulacrumai.io"` stays at **32** in every batch.

If anything goes red: stop, report, and don't commit. Each batch gets one commit,
`rebrand(simisi): <area>`. Then I stop and show you the diff.

## Batches (in order)

| # | Batch | Scope | Hits | Risk | How it's verified |
|---|---|---|---:|---|---|
| **0** | *(prereq, R0)* Commit your in-flight mascot/landing work as-is | `landing.html` working-copy diff + `test_mascot.py` | — | none: your code, your message | suite still green |
| **1** | **UI chrome & app pages** | `base.html` (title, logo text, footer ©, feedback modal), `_footer_public.html`, auth pages, dashboard, partners, resumes consent, settings copy (*not* the `/u/` URL previews, which are batch 7), social feed/chats, blog ×4 ("The SimiSI Journal"), careers ×2 + `config/jobs.json` copy, corporate invite page + emails, `integrations.json` copy, flash/error strings, `layer6.html` share caption, web-manifest `name`/`short_name` | ~75 | Low: text only. `test_footer` checks "The Journal" (still matches) | gate + render `/`, `/login`, `/blog`, `/careers` |
| **2** | **Landing & marketing** | `landing.html` title, description, og/twitter *titles*, wordmark, hero/FAQ/lead copy, sign-in ARIA (and `test_mascot.py` to match). `wizard.html` title/wordmark/share text. `explore.html`, `simi_help.html`, both sample use-case templates (copy only; filenames stay, C10) | ~45 | Low. ARIA string is pinned by `test_mascot` (updated in this batch) | gate + render `/`, `/explore`, `/samples/maya` |
| **3** | **Bio page, embeds, SEO site name** | `bio_page.html` ("Built with / Powered by"), `bio_teaser`, `bio_editor` (title, badge label), `profile`, `profile_unpublished`, `unsubscribe` (copy only; the 4 emails stay), `embed_badge.js` / `embed_card.js` text (the attribute stays, C4), `bio_seo.SITE_NAME` + description | ~25 | Low–med: `og:site_name` changes on every bio page (intended) | gate (`test_bio_seo`, `test_contact_masking`, `test_bio_readiness`) + render one bio page, teaser, embed JS |
| **4** | **Email copy** | `email_service.py` (~105 copy hits: subjects, preheaders, bodies, sign-offs, footer). `notification_service.py` copy + From *name*. `outreach_campaign_service.py` drip copy + HTML shell. `bio_privacy_service.py`, `tasks/bundle.py` email, `simulations/routes.py` share/artifact emails, `admin/routes.py` + `test_outreach_email.py` test-mail copy, `test_email.py`. Sender display name defaults → `SimiSI` (D1). In-body links to `https://simulacrumai.io/...` **stay** until batch 7 | ~140 | **Medium**: largest file. Mitigation: line-by-line edit, never a blanket replace. The 32-count email check and an `email_service` import smoke both gate it | gate + render 3 representative emails (verify-email, digest, drip 1) to HTML and show you the text |
| **5** | **Generated artifacts, billing text, LLM prompts** | `export.html` footer + `share_roi.html` (badge `SIMISI ROI REPORT`; the broken `simulacrum.ai` / `simulacrum.app` links → `https://simisi.io`, D5). `video_renderer.py` burned-in wordmark. `stripe_service.py` line-item *names* (amounts untouched). Campaign/bundle names, "SimiSI User" / "A SimiSI member" fallbacks. Download filenames → `simisi_…` (D10; safe because bundles store their full path). LLM prompts (D12). Voice script text (D8) | ~35 | Low–med: Stripe receipts show the new name; no price or logic change, and I'll diff-check that no numeric literal moved | gate + `git diff -U0 \| grep -E '^[-+].*[0-9]{2,}'` check + show the rendered export footer and ROI card |
| **6** | **Living docs + code comments** *(optional, D13/D14)* | `walkthrough.md`, `project_review_report.md` header, ~30 comment/docstring mentions. Historical PRDs + `.docx` untouched | ~60 | none | gate |
| **7** | **Domain cutover** *(hold until simisi.io resolves and 301 is live)* | `config.py` `BASE_URL` default; the hardcoded `https://simulacrumai.io` fallbacks (`layer6`, `outreach_campaign` constant, `qr_service`, `notification`, `peer`, `bio_privacy`, `admin_crm`, `action_items`, `email_service` links). Canonical/og:url/og:image/twitter:image, `sitemap.xml` ×16. `settings` + `wizard` `/u/` URL hints, privacy/terms domain mentions, `test_bio_seo` / `test_favicon` origins | ~60 | **High if early**: it drives canonicals and QR codes. robots/bio sitemap already follow `BASE_URL`, so the cPanel env var is the real switch | gate + render `/sitemap.xml`, `/robots.txt`, landing `<head>` |
| **8** | **OAuth redirect defaults** *(hold until the new URIs are registered at LinkedIn, Google, Apollo, PandaDoc)* | `config.py:42-55` | 5 | **High**: a mismatch breaks login/connect | gate. Prod is really controlled by the env vars (MANUAL #4–5) |
| **9** | **Legal pages** *(after your D9 ruling)* | `terms.html` (13), `privacy.html` (9): product name (+ domain, if batch 7 is done). Bay Area Experiences Ventures unchanged | ~20 | Legal | gate + show you the full rendered text diff |
| **—** | **Final verification + `REBRAND_REPORT.md`** | case-insensitive survivor grep with every hit justified; asset-rename list (none planned unless you pick C11) | | | |

**Not in any batch** (default "leave"): all of (b), C1–C13, the DB-stored copy (D11) until you
choose admin-edit vs. a new SQL migration, and the stale `noreply@` / `admin@simulacrum.ai` (D6).

Batches 1–6 can ship now. Users will see "SimiSI" while links still point at simulacrumai.io,
which keeps working. Batches 7–8 wait on the MANUAL list.
