# Design system rollout (docs/simisi_system_mockup.html)

**Scope, chosen by the owner on 2026-11-04:** everything, fully. That means the home page, every public page and every signed-in app page.

## Foundation
| Piece | Where |
|---|---|
| Tokens, type (Archivo), buttons, forms, flash, nav, compact footer, tables, page tabs | `app/static/css/system.css`, loaded after `main.css` by `base.html` |
| Logo: the mascot (blinks, waves on hover) and the Simi + gold SI wordmark | `partials/_logo.html`, used in the nav, the public footer and the onboarding wizard |
| Nav | Sticky, with a mobile drawer, in `base.html`. Signed-out and signed-in versions; Watch demo opens from any `[data-demo]` |
| Public footer | Mockup layout in `partials/_footer_public.html`. It stays self-contained for standalone pages |
| Home page | `landing.html` extends `base.html`, with `css/home.css` (generated from the mockup, scoped under `.home`) and `js/home.js` |

**Legacy token names** (`--navy`, `--teal`, `--muted`, `--border` and so on) are mapped onto the system values in `system.css`, so older page CSS follows the new palette without edits.

**Button classes:**
- `.btn-gold`, `.btn-secondary` and `.btn.primary` are the gold primary button.
- `.btn-primary` and `.btn-teal` are solid ink.
- `.btn-ghost` and `.btn-outline` are the light outline.

## Pages moved onto `base.html`
These were standalone documents with their own nav:
- `/explore`
- `/help/simi`
- `/feed`
- `/my-chats` (a small Explore / Feed / My chats tab row replaces their private nav)

## Light touch: system font and `system.css`, own layout kept
- The bio editor (an editor tool, so it keeps its editor bar)
- The onboarding wizard (retokened; logo swapped in)
- The bio teaser, legacy profile and unpublished-profile pages
- The corporate invite page
- ROI export
- The share-ROI card, which was rewritten as a light system card

## Dark-theme leftovers converted
White text, white borders and dark `#1a22xx` panels on light pages were converted in these templates:
- settings
- admin (index, audit, feedback, user integrations, partners)
- partner dashboards
- corporate
- the GCC
- dashboard, simulation detail and resume detail
- layer detail, which had dark cards

Light accents that only read on dark were mapped to their dark equivalents, as were old greys like `#9CA3AF` used for text. Deliberately dark chips (toasts, tooltips, ink bands) were left alone.

The Transition Package page's cream "Midnight Gold" paper became white and cool-grey panels, per the mockup's "never cream" rule.

## Kept as they are, on purpose
- **SME Console (`/guide`):** its own approved dark console design (SME-001 mockup).
- **The bio page (`/u/<slug>`):** owner-chosen themes. It now uses Archivo by default.
- **Ink insets:** the calibration instrument panel and Wealth Engine World, which fit the system's ink panels.
- **The Journal's ink hero and calendar.**
- **Use-case samples (Maya, Marcus):** dark editorial pages. Fonts moved to Archivo, and the broken hero layout and white-on-white Marcus page were fixed.

## Copy: the mockup's claims checked against the product
The home page uses the mockup's layout with the truthful copy decided on 2026-10-29 (`SITE_UPDATE_PLAN.md` §2):

| Mockup said | Used instead | Why |
|---|---|---|
| Agents act on the path "only with your approval" | "act on it, on the channels you allow, with every action logged" | The email channel is on by default |
| Draft card "From: Your Gmail" | "From: Your name, sent through SimiSI" | Email goes through SimiSI's SendGrid unless the user connects their own |
| "It waits, follows up once, and stops the moment someone replies" | "Follow-ups go out on schedule and stop for anyone who replies" | The existing follow-up behaviour |
| Demo "Undo" after sending | "Reset the example" | Sent email can't be undone |
| SME Console "In development / Join the waitlist" | "In beta"; "Open the SME Console" for allowed users, otherwise "Ask for beta access" | It's built and gated |
| Hard-coded people, 5.0 rating, "54 simulations" | Live published bio pages, approved-feedback rating and simulation count; hidden when empty | Every figure a visitor could check is real |
| Static integrations list | Kept hidden (`site_integrations_section`); when switched on it renders from `site_context.integration_unlocks()` | Owner decision 2026-10-29 |
| "About the book" link to `/playbook` | Dropped | There's no such page |

The hero chart and the three sample runs stay illustrative and are labelled as such.

## Retired
The walking hero mascot with per-load colours. `test_mascot.py` now guards the logo mascot instead. `partials/_mascot_mini.html` was removed.

## Tests updated for the new markup (the honesty checks are unchanged)
- `test_site_pricing.py`: layer stack, hero line, section ids.
- `test_transition_page.py`: nav class, Enterprise tier.
- `test_favicon.py`: the landing page inherits the partial.
- `test_mascot.py`: rewritten.

## Deploy
- No SQL and no environment variables.
- New static files: `css/system.css`, `css/home.css`, `js/home.js`.
