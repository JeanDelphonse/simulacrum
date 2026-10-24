# REBRAND REPORT — Simulacrum → SimiSI (simisi.io)

Branch `rebrand/simisi` (from `obs-001-agent-observability`). Plan and rulings: [REBRAND_PLAN.md](REBRAND_PLAN.md).

## Result

- **Gate:** green after every batch and at the end.
  - All modules compile and `create_app()` imports.
  - All 77 templates parse.
  - **18/18 test suites: 1,458 passed, 0 failed.** That's the same as the pre-rebrand baseline.
  - Excluded: `test_email.py` and `test_outreach_email.py`, because they send real mail. Their copy was still updated.
- **`simi@simulacrumai.io`:** 39 occurrences on 29 lines before and after the rebrand. Not one was changed.
- **No files renamed.** No asset, template or route path changed, so no reference can 404 from a rename. Only *generated download names* changed (§1, batch 5).
- **No logic or pricing change.** Automated checks showed that every edited line differs only by the brand/domain swap, and that no numeric literal moved.

## 1. What changed, by area

| Commit | Area | Lines | Highlights |
|---|---|---:|---|
| `3875139` | *(prereq)* your in-flight mascot/sign-in work, committed as-is | — | — |
| `6c467df` | Plan | — | `REBRAND_PLAN.md` |
| `ade2c2e` | UI chrome & app pages | 62 | header wordmark, titles, footers, auth/dashboard/settings/social/careers/corporate copy, "The SimiSI Journal", web-manifest name, Plaid Link `client_name`. The Alpaca consent sentence changed in the settings JS **and** the server's exact-match check together |
| `2ca9a8e` | Landing & marketing | 32 | homepage title/description/og+twitter titles/hero/FAQ, "Sign in to SimiSI", wizard (the `@SimulacrumAI` mention is dropped), Explore, Meet Simi, sample use cases |
| `275a7f9` | Bio page, embeds, SEO | 18 | "Built with / Powered by SimiSI", teaser, editor, embed scripts, `og:site_name` = SimiSI |
| `5f86ca9` | Email copy | 147 | all email subjects/bodies/sign-offs/footer. `MAIL_DEFAULT_SENDER_NAME` default is now `SimiSI` (the address doesn't change) |
| `5f27f25` | Artifacts, billing text, AI prompts | 35 | PDF export footer, **SIMISI ROI REPORT**, video wordmark, Stripe line-item names, `simisi_…` download names, AI system prompts, TTS "Simmy S I dot I O", `#SimiSI` |
| `e064adb` | Comments & docs | 19 | code comments, docstrings, review-report title |
| `9bc8fee` | Domain cutover | 49 | `BASE_URL` default + every hardcoded `https://simulacrumai.io` fallback, canonical/og:url/og:image host, sitemap ×16, email/bio/embed links, `/u/` hints |
| `3d8f772` | OAuth redirect defaults | 5 | LinkedIn ×2, Google, Apollo, PandaDoc → `https://simisi.io/...` |
| `3e723a7` | Legal pages | 17 | Terms + Privacy: rename only, entity and dates unchanged |
| `fa04247` | Stored outreach copy | — | **`sql/053_rebrand_simisi_outreach.sql`** (see §3) |

## 2. Intentionally kept (`git grep -i simulacrum` survivors)

Every survivor falls into an approved category below. Nothing else is left.

| Kept | Count | Why |
|---|---:|---|
| (b) `simi@simulacrumai.io` | 29 lines | Hard rule: the email address never changes |
| (b) `mail.simulacrumai.io` (inbound-parse reply domain) | 2 | Reply tracking depends on its MX record |
| C1/C2 `simulacrum_*` Stripe/PandaDoc/Kit metadata keys, Kit tags, and the comments that describe them | ~46 | Stored on live external objects; webhooks read them back |
| C3 `X-Simulacrum-*` email headers | 3 | Parsed by the SendGrid event webhook |
| C4 `data-simulacrum-card` | 4 | Already pasted on third-party sites |
| C5 `utm_source=simulacrum_bio` | 1 | Analytics continuity |
| C6 ElevenLabs voice name `simulacrum_{slug}` | 1 | External resource name |
| C7 DB names (`simulacrum.db`, `simulacrum_dev.db`, MySQL `simulacrum`) | 3 | Data location |
| C8 deploy plumbing (cPanel paths, WSGI prefixes, error log, `SIMULACRUM_PING_URL`, keepalive UA) | 22 | Server filesystem and routing |
| C9 `simulacrum_deploy.zip` | 2 | Your standing naming rule |
| C10 template filenames `samples/simulacrum_usecase_*.html` and path references to docs files | 9 | Internal paths; public URLs are `/samples/maya` and `/samples/marcus` |
| C11 `simulacrum-icon-512.png`, `Simulacrum_*.mp4` | 11 | Filenames kept; the asset *content* needs redesign (§4) |
| C13 applied migrations (`sql/create_tables`, `patch_v2`, `036`, `048`, `049`, `migrations/023`) | 6 | Never edit an applied migration |
| D6 `noreply@simulacrum.ai` (`.env.example`), `admin@simulacrum.ai` (`create_admin.py`) | 2 | Mail context; not the protected address |
| D14 historical PRDs in `docs/` (32 files) + 18 `.docx` filenames | 199 | Records of what was specified at the time |
| `walkthrough.md`, `project_review_report.md` `file://…/simulacrum/…` links | 30 | Local folder name |
| `test_mascot.py` guard that the old "Simulacrum mascot" label is gone | 2 | Regression check |

## 3. Deploy steps for this branch

1. **Run `sql/053_rebrand_simisi_outreach.sql`** in phpMyAdmin. It rewrites the brand and web links in `outreach_templates` and in *pending* `outreach_sends`. Sent mail is left as sent. It's idempotent and never touches `simi@` or `mail.` addresses. It was verified on SQLite against the real schema: the migrated drips come out equal to the new code defaults, and a second run is a no-op.
2. **cPanel environment variables** (each one overrides a code default):
   - `BASE_URL=https://simisi.io`
   - `LINKEDIN_REDIRECT_URI`, `LINKEDIN_BIO_REDIRECT_URI`, `GOOGLE_REDIRECT_URI`, `APOLLO_REDIRECT_URI`, `PANDADOC_REDIRECT_URI` → their `https://simisi.io/...` values
   - `MAIL_DEFAULT_SENDER_NAME`: set it to `SimiSI` or delete it
   - Leave `MAIL_DEFAULT_SENDER` and `FOUNDER_EMAIL` as `simi@simulacrumai.io`
3. Build `simulacrum_deploy.zip` as usual, without `.htaccess`.
4. **Smoke test after deploy:**
   - the homepage `<head>`, `/robots.txt` and `/sitemap.xml` all show simisi.io
   - sign in with Google; connect LinkedIn, Apollo and PandaDoc
   - submit a private-bio LinkedIn access request
   - send yourself a test email from /admin and check the From line reads "SimiSI <simi@simulacrumai.io>"
   - load an old `simulacrumai.io/u/<slug>` link and confirm it 301s

## 4. Outstanding MANUAL / INFRA checklist

- [ ] simisi.io registered and live on cPanel with SSL *(you confirmed done)*
- [ ] `simulacrumai.io/*` → `simisi.io/*` 301, preserving path and query *(you confirmed done)*
- [ ] **Keep simulacrumai.io's MX, SPF, DKIM, DMARC, SendGrid domain authentication and the `mail.simulacrumai.io` Inbound Parse MX.** `simi@` mail and reply tracking depend on them
- [ ] New OAuth redirect URIs registered at LinkedIn ×2, Google, Apollo, PandaDoc *(you confirmed done)*. Remove the old ones only after the smoke test
- [ ] cPanel env vars from §3 step 2
- [ ] Stripe: business name, statement descriptor, Checkout branding, Connect platform name, webhook endpoint URL
- [ ] SendGrid: event-webhook and inbound-parse POST URLs, link branding
- [ ] Kit/ConvertKit, Cal.com, ElevenLabs, Apollo, PandaDoc and Plaid dashboards: app name and callback URLs (Plaid may require the app name to match the dashboard)
- [ ] Search Console + analytics: add the simisi.io property, submit the sitemap, use Change of Address
- [ ] Social handles: claim `@SimiSI` if wanted, then add it back to the wizard share text; update the LinkedIn company page
- [ ] **Brand assets with the old wordmark baked in:** `static/icons/simulacrum-icon-512.png` (if it shows text), demo videos `Simulacrum_Career_as_Asset.mp4` and `Simulacrum_Revenue_Engine.mp4`, `static/img/playbook-cover.jpg`, any OG images
- [ ] Stored data that code can't reach: previously generated bios and artifacts, CRM notes, `platform_settings` values and cached bio QR PNGs may still contain "Simulacrum" or old-domain URLs. Old URLs keep working through the 301. Regenerate the QR codes if you want new prints to show simisi.io
- [ ] Optional follow-ups: `.env.example` sender (`noreply@simulacrum.ai` → `simi@simulacrumai.io`); accept `data-simisi-card` alongside `data-simulacrum-card` in `embed_card.js` for new embeds
