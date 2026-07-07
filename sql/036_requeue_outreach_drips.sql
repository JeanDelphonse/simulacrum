-- SIM-PRD-OUTREACH-001 follow-up — re-queue drip sends that captured the old
-- dashboard URL. Early builds rendered {{dashboard_url}} as /simulations; it is now
-- /dashboard. Delete the not-yet-sent drip rows that still contain the old URL so the
-- scheduler's self-heal step (_ensure_pending_sends) re-materializes them with the
-- corrected copy on the next tick.
--
-- Only Email 2 and Email 3 reference the dashboard URL, so Email 1 rows are untouched.
-- Sent emails and admin edits without the old URL are left alone. Safe to run once.

DELETE FROM outreach_sends
WHERE kind = 'drip'
  AND status IN ('queued', 'awaiting_approval', 'paused', 'scheduled')
  AND body_snapshot LIKE '%simulacrumai.io/simulations%';
