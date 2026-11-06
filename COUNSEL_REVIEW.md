# SimiSI: proposed wording for counsel review

**Status:** every text below is a **DRAFT** proposed by engineering. Nothing here is final until counsel approves it. In the product, these texts live in one editable place (Admin → Legal text). Each one starts as "draft" and only an admin can mark it "counsel approved".

Product context:
- SimiSI simulates a member's possible income across five "layers": consulting, teaching and speaking, products, recurring income, and wealth planning.
- AI agents draft and send outreach on channels the member allows.
- Layer 5 produces financial, tax and legal *education* documents.
- No agent can move money or place trades.

Questions for counsel are marked **Q**.

---

## 1. Layer names and framing (earnings claims)

| Where | Today | Proposed |
|---|---|---|
| Layer 4 name (site, app, emails) | Automated Residual Income / Automated Residual | **Recurring income** |
| Layer 4 tagline (home page) | Systems that run while you sleep | **Income that keeps working after the initial build** |
| Layer 4 long name (simulation results) | Automated Residual Systems — Running Without You | **Recurring Income — After the Initial Build** |
| Layer 3 subtitle (simulation results) | Earn While You Sleep | **Built once, sold repeatedly** |
| Layer 5 long name | Wealth Deployment — Money Working for You | **Wealth Planning — Education and Planning** |
| Layer 5 subtitle | Money That Makes Money | **Planning what to do with what you earn** |
| Simi suggested question | What's my path to passive income? | **Which layers fit my situation?** |
| Employee invite page | …from active consulting to passive assets | **…from consulting to longer-term planning** |

**Q1.** Is "Recurring income" acceptable for Layer 4, or should we avoid "income" in the layer name entirely (e.g. "Recurring offers")?

---

## 2. Marketing emails SimiSI sends to its own members

| Where | Today | Proposed |
|---|---|---|
| Drip 3 subject | How SimiSI builds income while you sleep | **How the deeper layers work, and what they take to build** |
| Drip 3 body | "The deeper layers build income that compounds on its own." | **"The deeper layers take more work up front: a course, a newsletter, a licensing offer. Once built, they can keep selling without your time on every sale. Results vary widely."** |
| Drip 3 body | "…create revenue streams that run without your daily involvement." | **"…can keep working without your daily involvement once they are built and maintained."** |
| Drip 3 body | "…so your income builds lasting wealth." | **"…educational planning on saving, tax structure and entities, with questions to take to a licensed professional."** |
| Drip 2 body | "Most users see their first opportunities within the first few cycles." | **Remove**, or: **"Results depend on your field, effort and market."** |

---

## 3. Voice-training script (read aloud by the member to train a voice model)

**Today** (excerpt): "My simulation generated 46,000 dollars in confirmed income across 5 layers… Within two weeks, I had my first client. Within a month, I had three income streams running on autopilot… books discovery calls."

**Proposed** (no income or results claims; eight short paragraphs for voice coverage):
1. "I help professionals turn what they know into work people will pay for."
2. "SimiSI reads my resume and simulates the next twenty-four months across five layers of income."
3. "Each layer has agents that draft real work: a rate card, outreach, a course outline, a newsletter plan."
4. "I review what they draft, change what I want, and choose what goes out."
5. "Every number in my simulation is an estimate with a range, a source and a confidence level."
6. "My bio page answers visitors' questions when I'm not available and passes their details to me."
7. "If you have years of experience, your resume may hold more options than you think."
8. "You can create a free bio page at simisi dot io."

---

## 4. Income estimates (shown wherever a projected figure appears)

| Key | Proposed text |
|---|---|
| `estimate_label` (next to every projected range) | **Estimate, not a promise** |
| `estimate_basis_intro` (the "How we estimated this" panel) | **"This range is a model estimate based on the assumptions and sources below. It is not a forecast of what you will earn. Your results depend on your field, effort, market and many factors we cannot see."** |
| `base_rate_l3_l4` (Layers 3 and 4 only) | **"Results for courses, products, newsletters and similar offers vary widely. Most people who try earn less than the upper end of this range, and many earn little or nothing."** |
| `self_reported_label` (income a member recorded themselves) | **Self-reported by the member, not verified by SimiSI** |

**Q2.** Do we need a source for the base-rate statement ("most people… earn less") before publishing it? Engineering has no outcome data yet. The new outcome check-ins will collect it, but none of it is published until you approve.

**Q3.** The home page shows a sample chart for an illustrative member. Is the caption "A sample simulation… Illustrative" sufficient, or must the per-layer figures also be ranges?

---

## 5. Testimonials and case studies

| Key | Proposed text |
|---|---|
| `typical_results` (under any testimonial mentioning money, clients or results) | **"Individual experience. Results vary and are not typical; most members earn less. Testimonials are from real members and were not paid for."** |
| `illustrative_label` (Maya and Marcus case studies, sample runs, Playbook case studies) | **"Illustrative case study: a composite example, not a real customer's results."** |
| `share_roi_disclosure` (public share card "Captured $X from a $Y simulation") | **"Self-reported by the member and not verified. Results vary; most members earn less."** |

**Q4.** Members can add their own client testimonials to their bio pages. Should SimiSI add the typical-results line to those too (it's the member's own advertising), or only offer it?

**Q5.** The share card shows a return multiple (e.g. "4.2x ROI"). Keep it with the disclosure, or show only the self-reported amount?

---

## 6. Layer 5: education and planning, not investment advice

| Where | Today | Proposed |
|---|---|---|
| Home page, Layer 5 note | Advisory only. These agents plan; they never trade or move money. | **"Education and planning only. These agents explain options; they never trade or move money."** |
| Integrations (Alpaca) | advisory only; SimiSI never places trades | **"Read-only: compares your plan with your account balances. SimiSI never places trades."** |
| Autonomy settings | Layer 5 agents only generate advisory documents | **"Layer 5 agents only generate education and planning documents; they never execute anything."** |
| Simi help page | Financial and legal guidance is advisory | **"Financial and legal topics are education, not advice"** |
| `l5_banner` (top of every Layer 5 result) | — | **"Education and planning only, not investment, tax or legal advice. SimiSI is not a registered investment adviser, broker-dealer, CPA or law firm."** |
| `l5_referral_adviser` (end of allocation, projection, fund-type and policy outputs) | (a generic disclaimer, written by the AI) | **"Before acting on any of this, talk to a fee-only fiduciary financial adviser who can look at your full situation."** |
| `l5_referral_cpa` (tax and entity outputs) | (generic) | **"Before filing or changing your entity, review this with a CPA or enrolled agent."** |
| `l5_referral_attorney` (estate and entity outputs) | (generic) | **"Wills, trusts, beneficiaries and entity documents should be prepared or reviewed by a licensed attorney in your state."** |
| `l5_fallback` (shown if the AI output names products or gives trade instructions twice) | — | **"We couldn't produce this planning document within our education-only rules. Try again, or take your questions to a fee-only fiduciary adviser, CPA or attorney."** |

Two Layer 5 agents are **reframed** (product decisions; counsel's view welcome):

| Agent | Today | Proposed |
|---|---|---|
| Index Fund & ETF Recommendations | "Curate 8–12 recommended funds with a model portfolio allocation… tickers" | **"Fund Types Explained":** index fund and ETF categories, costs, account types and questions to ask, with **no named funds or tickers** |
| Dollar-Cost Averaging Schedule | contribution amount, a chosen brokerage, a "brokerage-specific setup guide" | **"Regular Contribution Concepts":** how regular investing works, with an illustrative schedule using the member's own example number labelled as an example; **no platform instructions** |

**The prompt rules given to every Layer 5 agent** (`l5_agent_rules`; not shown to users, included for completeness):
> "You provide general financial, tax and legal education, not personalised advice. Never name a security, fund, ticker, crypto asset or financial product to buy or sell. Never tell the user to buy, sell, or allocate a specific amount or percentage; describe scenarios as general illustrations ('a common approach is…', 'in a scenario where…'). Do not give tax-filing or legal advice; explain concepts and list questions to ask a professional. Do not promise or imply returns. End with the referral line provided."

Also removed for accuracy: a Settings toggle labelled **"Enable automated trading"** with the consent text *"I understand that SimiSI will place trades in my brokerage account…"*. No code can place trades; the toggle and the text are removed.

**Q6.** Is "fee-only fiduciary financial adviser" the right referral wording, or a more general "licensed financial professional"?

**Q7.** Do the two reframed agents need to go further (e.g. no illustrative percentages at all in allocation scenarios)?

---

## 7. Outreach email (sent under a member's name through SimiSI)

| Key | Proposed text |
|---|---|
| `email_footer` (bottom of every outreach and campaign email) | **"Sent by {sender_name} via SimiSI · {sender_postal_address} · You received this because {sender_name} thought it relevant to your work. Unsubscribe: {link} · or reply STOP."** |
| `email_reply_optout` | **"Reply STOP to stop receiving emails from {sender_name}."** |
| `postal_address_required` (blocks sending until set) | **"Add your mailing address in Settings before sending. The law requires a valid postal address in commercial email."** |
| `campaign_paused` (automatic pause) | **"We paused this campaign: {bounce_rate}% of recent emails bounced and {complaint_rate}% were marked as spam, above the safe limits. Check your list, then resume."** |
| `import_source_blocked` | **"We can't import purchased, rented or scraped lists. Import only people you have a relationship with or who agreed to hear from you."** |
| `linkedin_manual` (LinkedIn drafts) | **"SimiSI doesn't post or message on LinkedIn for you. Copy this draft and post or send it yourself."** |

**Q8.** Is a post-office box acceptable as the member's postal address (CAN-SPAM allows a registered PO box or private mailbox)? Should the UI say so?

**Q9.** Opt-outs take effect immediately (well inside the 10-business-day window). Should the footer state the timing?

**Q10.** Outreach goes out "From: {Member Name} <simi@simulacrumai.io>" with replies routed back to the member. Is that sufficiently non-deceptive for the From line, or should it read "{Member Name} via SimiSI"?

---

## 8. Not changed in this task
- `/legal/privacy` and `/legal/terms` are linked to, not rewritten. Terms §7.2 "No Guarantees" already exists.
- The brand and the contact address simi@simulacrumai.io are unchanged.
- Transactional emails (verification, password, receipts, invites) are left as they are. **Q11:** please confirm they fall outside the commercial-email rules.
