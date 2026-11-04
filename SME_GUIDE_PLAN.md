# PRD-SIM-SME-001: SME Console Conversational Guide (beta)

Source: `docs/PRD-SIM-SME-001 SME Console Conversational Guide.docx` and the mockup `docs/simisi_sme_console_mockup.html`.
Branch: `feat/transition-package-page` (uncommitted until the owner asks).

**Naming.** The route is `/guide`, and the code uses the `guide_` prefix. That keeps it apart from the human-SME console, which already lives at `/sme` (SME-002).

## Built
| Piece | Where |
|---|---|
| Tables | `app/models/sme_guide.py`, `sql/063_sme_guide.sql` |
| Service | `app/services/guide_service.py` |
| Console UI (mockup look, scoped under `.smc`) | `/guide`; `guide/console.html`, `static/css/guide.css`, `static/js/guide_console.js` |
| History (view, delete; goals and skills are kept) | `/guide/history` |
| Admin: beta gate, per-user access, guide on/off, metrics | `/admin/guide` (Admin → People → SME Console) |
| Approved goals feed agent work | `Briefing.goal` in `app/services/agent_briefing.py` |
| Delete-user SQL | `sql/tools/gen_delete_user.py` (regenerated) |
| Tests | `test_guide.py` (50 checks, AI client faked) |
| Real-API eval (costs money; not in the suite) | `python -X utf8 guide_eval.py [--quick]` |

The tables are `sme_guides`, `user_guide_prefs`, `sme_sessions`, `sme_turns`, `sme_briefings`, `skill_suggestions`, `goal_cards` and `goal_card_agent_changes`.

The service covers:
- the beta gate;
- the 11 guides;
- briefing facts with source ids;
- GROW turns through a forced tool;
- citation checks;
- the crisis check;
- limits;
- skills and goal cards.

## Rules as implemented
- **Beta gate.** The gate reuses `feature_flags` and `user_feature_overrides` (key `sme_console`) and fails closed.
  - The modes are off, allowlist (the default) and on.
  - Admins can use the console whenever the mode isn't off.
- **Same guidance from every guide.** The guide's name is the only thing that changes between guides, so the rules, briefing and model are identical. The eval checks the GROW structure across two guides.
- **The guide only uses facts it can cite.** Facts come from a briefing built from:
  - the profile, career, resume achievement lines and skills;
  - the simulation layers with their ranges;
  - the calibrated rate card, the layer's agents and existing goals;
  - the last session's summary.
  - Contacts are never included.
- **Checking each turn.** Every statement about the user must cite a briefing id.
  - A turn with an unknown id or a missing citation is regenerated once (`guide_regen_attempts`).
  - If the retry also fails, the turn becomes a safe fallback question.
  - Agent changes are limited to the layer's own agents.
- **Safety.**
  - A crisis phrase is caught before any model call, and the user is shown 988 resources.
  - If the model reports a crisis, the same resources are shown.
  - Every L5 turn includes a no-advice note.
  - Text the user types is passed as `<user_said>` data, so the model doesn't follow it as instructions.
- **Deadlines.** The prompt carries today's date, so deadlines are counted forward from today. The eval fails any past deadline.
- **The user decides.**
  - Skills are proposed, and the user accepts or declines each one.
  - A goal card saves only when the user presses Approve. Approving retires the previous goal for that layer and sets a review date (`guide_review_days`, 90).
- **Privacy.**
  - Turn text, briefings and session summaries are encrypted (Fernet).
  - The admin page shows metrics only, never transcripts.
- **Limits.** Turns per session, sessions per day, and a cost cap per session and per month are all `guide_*` platform settings. Hitting a limit returns 429 with a friendly message.
- **Models.** Turns use Sonnet (`sme_guide_turn`) and summaries use Haiku (`sme_guide_summary`). The briefing block is prompt-cached.

## Deviations from the PRD (owner to decide)
1. **Voice.** The console uses the browser's built-in speech for output and push-to-talk for input. There's no neural text-to-speech provider yet, and replies aren't streamed sentence by sentence; that needs a provider choice and a key. In Chrome, push-to-talk sends audio to Google, which the console's privacy note says.
2. **Photoreal video avatars aren't built.** The switch is there (`guide_video_avatars`, off), and the console uses the mockup portraits.
3. **Agent changes in a goal card are recorded but not applied.** They're stored in `goal_card_agent_changes` with `applied_at` left NULL, to be applied through the EXEC config path later. Approved goals already reach agents through the briefing.

## Deploy
- Run `sql/063_sme_guide.sql`.
- The gate defaults to allowlist. Grant users at `/admin/guide`, or set the mode to on.
- No new environment variables or cron lines.
