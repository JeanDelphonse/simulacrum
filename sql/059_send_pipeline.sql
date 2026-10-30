-- Agent Execution Layer P5: the safe send pipeline. Run once on deploy. Additive.
--
-- Drafts prepared with "Send to contacts" (artifact_units, kind 'variant') can
-- now be SENT — only after the user explicitly confirms a batch, and only the
-- exact recipients and content they previewed (the confirmation carries a hash
-- of the batch; any change invalidates it). Every recipient is re-checked at send
-- time (do-not-contact, unsubscribed, placeholder, already contacted by the
-- agent), every email carries a one-click unsubscribe link and List-Unsubscribe
-- headers, and sends pass the observability guardrail check at the tool
-- boundary. Confirmations are recorded in action_approvals (target_type
-- 'send_batch').
--
-- Sending account: platform setting exec_send_provider
--   simisi_sendgrid (default) — SimiSI's SendGrid account
--   own_sendgrid              — each user's own SendGrid account (API key + a
--                               sender verified in their SendGrid), stored as a
--                               user_integrations row with provider 'sendgrid'
-- Limits: exec_send_max_per_day (default 50 per user), exec_send_cost_cents
-- (default 0, counted against exec_budget_cents_per_month).

ALTER TABLE artifact_units
    ADD COLUMN sent_at      DATETIME     NULL AFTER created_at,
    ADD COLUMN email_log_id CHAR(9)      NULL AFTER sent_at,
    ADD COLUMN error        VARCHAR(300) NULL AFTER email_log_id;
