-- delete_user.sql — permanently delete ONE user and everything that is theirs.
-- Generated from the models (all 162 tables reviewed). MySQL 5.7+/MariaDB.
--
-- BEFORE RUNNING
--   1. Back up the database (cPanel > Backup, or mysqldump). This cannot be undone.
--   2. Prefer the app's soft delete first (Admin > Users, or the user's own
--      "Delete account" with its 30-day recovery window). Use this script only
--      for a confirmed permanent erasure request.
--   3. Put the user's email on the line below. Run the whole file in one session
--      (phpMyAdmin SQL tab or the mysql client) — @uid and the helper tables
--      belong to that session.
--   4. Check the preview counts it prints. The transaction ends with ROLLBACK:
--      when the counts look right, change the last line to COMMIT and run again.
--
-- KEPT ON PURPOSE (not deleted):
--   audit_logs               audit trail (kept as evidence)
--   resume_consents          consent records must survive deletion (FR-TOS-09)
--   payment_records          financial record (tax / chargebacks)
--   commissions              partner financial record
--   partner_payouts          partner financial record
--   admin_access_audit       admin access audit trail
--   integration_audit_log    admin audit trail
--   feature_changes          admin audit trail
--   email_suppressions       global opt-out list: an opt-out must outlive the account
-- Other users' rows that merely mention this user as the actor (created_by,
-- decided_by, granted_by …) are kept with that column set to NULL.
-- If the user is the admin of a corporate account, that organisation and its
-- other members are NOT deleted — reassign it first (Admin > Organizations).
--
-- FILES ON DISK are not touched by SQL: resumes, avatars, videos and QR images
-- under the uploads folder. Remove them separately.

SET @email := 'user@example.com';   -- <<< the user to delete
SET @uid := (SELECT id FROM users WHERE LOWER(email) = LOWER(@email));
SELECT @uid AS user_id, (SELECT full_name FROM users WHERE id = @uid) AS name,
       (SELECT COUNT(*) FROM corporate_accounts WHERE admin_user_id = @uid) AS orgs_they_admin;
-- Stop here if user_id is NULL (no such email) or orgs_they_admin > 0.

-- Helper tables (snapshots of the ids to delete). Created before the
-- transaction because CREATE TABLE commits implicitly in MySQL.
DROP TABLE IF EXISTS zz_del_sims;
DROP TABLE IF EXISTS zz_del_actions;
DROP TABLE IF EXISTS zz_del_contacts;
DROP TABLE IF EXISTS zz_del_bio;
DROP TABLE IF EXISTS zz_del_mail;
DROP TABLE IF EXISTS zz_del_cycles;
DROP TABLE IF EXISTS zz_del_queue;
DROP TABLE IF EXISTS zz_del_steps;
DROP TABLE IF EXISTS zz_del_layers;
DROP TABLE IF EXISTS zz_del_streams;
DROP TABLE IF EXISTS zz_del_runs;
DROP TABLE IF EXISTS zz_del_plans;
DROP TABLE IF EXISTS zz_del_chatsess;
DROP TABLE IF EXISTS zz_del_pchats;
DROP TABLE IF EXISTS zz_del_convos;
DROP TABLE IF EXISTS zz_del_shares;
DROP TABLE IF EXISTS zz_del_advisor;
DROP TABLE IF EXISTS zz_del_collabs;
DROP TABLE IF EXISTS zz_del_posts;
DROP TABLE IF EXISTS zz_del_comments;
DROP TABLE IF EXISTS zz_del_partner;
DROP TABLE IF EXISTS zz_del_enroll;
DROP TABLE IF EXISTS zz_del_resumes;
DROP TABLE IF EXISTS zz_del_seats;
DROP TABLE IF EXISTS zz_del_csess;
DROP TABLE IF EXISTS zz_del_smesess;
DROP TABLE IF EXISTS zz_del_goals;
DROP TABLE IF EXISTS zz_del_preview;
CREATE TABLE zz_del_sims (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_actions (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_contacts (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_bio (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_mail (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_cycles (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_queue (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_steps (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_layers (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_streams (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_runs (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_plans (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_chatsess (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_pchats (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_convos (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_shares (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_advisor (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_collabs (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_posts (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_comments (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_partner (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_enroll (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_resumes (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_seats (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_csess (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_smesess (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_goals (id CHAR(9) PRIMARY KEY);
CREATE TABLE zz_del_preview (tbl VARCHAR(64) PRIMARY KEY, rows_to_delete INT);

START TRANSACTION;

INSERT INTO zz_del_sims SELECT id FROM simulations WHERE user_id = @uid;
INSERT INTO zz_del_actions SELECT id FROM agent_actions WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_contacts SELECT id FROM contacts WHERE user_id = @uid;
INSERT INTO zz_del_bio SELECT id FROM bio_pages WHERE user_id = @uid;
INSERT INTO zz_del_mail SELECT id FROM email_accounts WHERE user_id = @uid;
INSERT INTO zz_del_cycles SELECT id FROM layer6_cycles WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_queue SELECT id FROM layer6_action_queue WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_steps SELECT id FROM action_steps WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_layers SELECT id FROM simulation_layers WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_streams SELECT id FROM income_streams WHERE layer_id IN (SELECT id FROM zz_del_layers);
INSERT INTO zz_del_runs SELECT id FROM agent_runs WHERE user_id = @uid OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_plans SELECT id FROM agent_plans WHERE user_id = @uid OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_chatsess SELECT id FROM bio_chat_sessions WHERE user_id = @uid OR bio_page_id IN (SELECT id FROM zz_del_bio);
INSERT INTO zz_del_pchats SELECT id FROM platform_chats WHERE owner_user_id = @uid OR chatter_user_id = @uid;
INSERT INTO zz_del_convos SELECT id FROM simi_conversations WHERE user_id = @uid OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_shares SELECT id FROM agent_shares WHERE owner_user_id = @uid OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_advisor SELECT id FROM advisor_access WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_collabs SELECT id FROM collaborations WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR invitee_id = @uid;
INSERT INTO zz_del_posts SELECT id FROM blog_posts WHERE author_user_id = @uid;
INSERT INTO zz_del_comments SELECT id FROM blog_comments WHERE user_id = @uid OR post_id IN (SELECT id FROM zz_del_posts);
INSERT INTO zz_del_partner SELECT id FROM referral_partners WHERE user_id = @uid;
INSERT INTO zz_del_enroll SELECT id FROM outreach_enrollments WHERE user_id = @uid;
INSERT INTO zz_del_resumes SELECT id FROM resumes WHERE user_id = @uid;
INSERT INTO zz_del_seats SELECT id FROM transition_seats WHERE user_id = @uid;
INSERT INTO zz_del_csess SELECT id FROM coaching_sessions WHERE seat_id IN (SELECT id FROM zz_del_seats);
INSERT INTO zz_del_smesess SELECT id FROM sme_sessions WHERE user_id = @uid;
INSERT INTO zz_del_goals SELECT id FROM goal_cards WHERE user_id = @uid;

-- Preview: rows that will be deleted, per table.
INSERT INTO zz_del_preview SELECT 'email_logs', COUNT(*) FROM email_logs WHERE action_id IN (SELECT id FROM zz_del_actions) OR contact_id IN (SELECT id FROM zz_del_contacts) OR simulation_id IN (SELECT id FROM zz_del_sims) OR step_id IN (SELECT id FROM zz_del_steps);
INSERT INTO zz_del_preview SELECT 'layer6_outcomes', COUNT(*) FROM layer6_outcomes WHERE income_stream_id IN (SELECT id FROM zz_del_streams) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'layer6_execution_log', COUNT(*) FROM layer6_execution_log WHERE action_id IN (SELECT id FROM zz_del_actions) OR cycle_id IN (SELECT id FROM zz_del_cycles) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'action_steps', COUNT(*) FROM action_steps WHERE agent_action_id IN (SELECT id FROM zz_del_actions) OR parent_action_id IN (SELECT id FROM zz_del_queue) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'layer6_share_tokens', COUNT(*) FROM layer6_share_tokens WHERE cycle_id IN (SELECT id FROM zz_del_cycles) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'layer6_action_queue', COUNT(*) FROM layer6_action_queue WHERE agent_action_id IN (SELECT id FROM zz_del_actions) OR cycle_id IN (SELECT id FROM zz_del_cycles) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'income_streams', COUNT(*) FROM income_streams WHERE layer_id IN (SELECT id FROM zz_del_layers);
INSERT INTO zz_del_preview SELECT 'cycle_posterior_snapshots', COUNT(*) FROM cycle_posterior_snapshots WHERE cycle_id IN (SELECT id FROM zz_del_cycles) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'collab_activities', COUNT(*) FROM collab_activities WHERE collaboration_id IN (SELECT id FROM zz_del_collabs) OR collaborator_id = @uid OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'artifact_versions', COUNT(*) FROM artifact_versions WHERE action_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'artifact_dependencies', COUNT(*) FROM artifact_dependencies WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR upstream_action_id IN (SELECT id FROM zz_del_actions);
INSERT INTO zz_del_preview SELECT 'advisor_notes', COUNT(*) FROM advisor_notes WHERE advisor_access_id IN (SELECT id FROM zz_del_advisor) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'user_feedback', COUNT(*) FROM user_feedback WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'simulation_visibility', COUNT(*) FROM simulation_visibility WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'simulation_videos', COUNT(*) FROM simulation_videos WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'simulation_layers', COUNT(*) FROM simulation_layers WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'prefill_corrections', COUNT(*) FROM prefill_corrections WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'layer6_momentum', COUNT(*) FROM layer6_momentum WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'layer6_cycles', COUNT(*) FROM layer6_cycles WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'layer6_configs', COUNT(*) FROM layer6_configs WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'collaborations', COUNT(*) FROM collaborations WHERE invitee_id = @uid OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'blog_comment_likes', COUNT(*) FROM blog_comment_likes WHERE comment_id IN (SELECT id FROM zz_del_comments) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'artifact_bundles', COUNT(*) FROM artifact_bundles WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'ai_interactions', COUNT(*) FROM ai_interactions WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'agent_context', COUNT(*) FROM agent_context WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'agent_actions', COUNT(*) FROM agent_actions WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'advisor_access', COUNT(*) FROM advisor_access WHERE partner_id IN (SELECT id FROM zz_del_partner) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'user_profiles', COUNT(*) FROM user_profiles WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'sme_recommendations', COUNT(*) FROM sme_recommendations WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'simulations', COUNT(*) FROM simulations WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'referral_signups', COUNT(*) FROM referral_signups WHERE partner_id IN (SELECT id FROM zz_del_partner) OR referred_user_id = @uid;
INSERT INTO zz_del_preview SELECT 'corporate_employees', COUNT(*) FROM corporate_employees WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'blog_likes', COUNT(*) FROM blog_likes WHERE post_id IN (SELECT id FROM zz_del_posts) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'blog_comments', COUNT(*) FROM blog_comments WHERE post_id IN (SELECT id FROM zz_del_posts) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'user_sessions', COUNT(*) FROM user_sessions WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'simi_messages', COUNT(*) FROM simi_messages WHERE conversation_id IN (SELECT id FROM zz_del_convos);
INSERT INTO zz_del_preview SELECT 'resumes', COUNT(*) FROM resumes WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'referral_partners', COUNT(*) FROM referral_partners WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'profile_inquiries', COUNT(*) FROM profile_inquiries WHERE profile_user_id = @uid;
INSERT INTO zz_del_preview SELECT 'blog_reports', COUNT(*) FROM blog_reports WHERE reporter_user_id = @uid OR target_id IN (SELECT id FROM zz_del_comments) OR target_id IN (SELECT id FROM zz_del_posts);
INSERT INTO zz_del_preview SELECT 'blog_posts', COUNT(*) FROM blog_posts WHERE author_user_id = @uid;
INSERT INTO zz_del_preview SELECT 'user_integrations', COUNT(*) FROM user_integrations WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'user_guide_prefs', COUNT(*) FROM user_guide_prefs WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'user_feature_overrides', COUNT(*) FROM user_feature_overrides WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'user_events', COUNT(*) FROM user_events WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'user_discounts', COUNT(*) FROM user_discounts WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'user_discount_redemptions', COUNT(*) FROM user_discount_redemptions WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'user_connections', COUNT(*) FROM user_connections WHERE user_a_id = @uid OR user_b_id = @uid;
INSERT INTO zz_del_preview SELECT 'transition_seats', COUNT(*) FROM transition_seats WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'transition_outcomes', COUNT(*) FROM transition_outcomes WHERE seat_id IN (SELECT id FROM zz_del_seats);
INSERT INTO zz_del_preview SELECT 'sync_runs', COUNT(*) FROM sync_runs WHERE account_id IN (SELECT id FROM zz_del_mail);
INSERT INTO zz_del_preview SELECT 'suppression_list', COUNT(*) FROM suppression_list WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'suggestion_blends', COUNT(*) FROM suggestion_blends WHERE share_id IN (SELECT id FROM zz_del_shares);
INSERT INTO zz_del_preview SELECT 'social_post_queue', COUNT(*) FROM social_post_queue WHERE artifact_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'sme_turns', COUNT(*) FROM sme_turns WHERE session_id IN (SELECT id FROM zz_del_smesess);
INSERT INTO zz_del_preview SELECT 'sme_sessions', COUNT(*) FROM sme_sessions WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'sme_briefings', COUNT(*) FROM sme_briefings WHERE session_id IN (SELECT id FROM zz_del_smesess);
INSERT INTO zz_del_preview SELECT 'sme_access_log', COUNT(*) FROM sme_access_log WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'skill_suggestions', COUNT(*) FROM skill_suggestions WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'simulation_chat_messages', COUNT(*) FROM simulation_chat_messages WHERE session_id IN (SELECT id FROM zz_del_chatsess) OR simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'simi_conversations', COUNT(*) FROM simi_conversations WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'signing_documents', COUNT(*) FROM signing_documents WHERE action_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'sender_signals', COUNT(*) FROM sender_signals WHERE account_id IN (SELECT id FROM zz_del_mail);
INSERT INTO zz_del_preview SELECT 'review_items', COUNT(*) FROM review_items WHERE account_id IN (SELECT id FROM zz_del_mail) OR contact_id IN (SELECT id FROM zz_del_contacts) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'reciprocity_ledger', COUNT(*) FROM reciprocity_ledger WHERE peer_user_id = @uid;
INSERT INTO zz_del_preview SELECT 'published_pages', COUNT(*) FROM published_pages WHERE action_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'prospect_research_runs', COUNT(*) FROM prospect_research_runs WHERE action_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'platform_chats', COUNT(*) FROM platform_chats WHERE bio_page_id IN (SELECT id FROM zz_del_bio) OR chatter_user_id = @uid OR contact_id IN (SELECT id FROM zz_del_contacts) OR owner_user_id = @uid;
INSERT INTO zz_del_preview SELECT 'platform_chat_messages', COUNT(*) FROM platform_chat_messages WHERE chat_id IN (SELECT id FROM zz_del_pchats);
INSERT INTO zz_del_preview SELECT 'plan_steps', COUNT(*) FROM plan_steps WHERE plan_id IN (SELECT id FROM zz_del_plans);
INSERT INTO zz_del_preview SELECT 'peer_invites', COUNT(*) FROM peer_invites WHERE owner_user_id = @uid OR peer_user_id = @uid;
INSERT INTO zz_del_preview SELECT 'parameter_suggestions', COUNT(*) FROM parameter_suggestions WHERE peer_user_id = @uid OR share_id IN (SELECT id FROM zz_del_shares);
INSERT INTO zz_del_preview SELECT 'page_views', COUNT(*) FROM page_views WHERE session_id IN (SELECT id FROM zz_del_chatsess) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'outreach_sends', COUNT(*) FROM outreach_sends WHERE enrollment_id IN (SELECT id FROM zz_del_enroll) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'outreach_enrollments', COUNT(*) FROM outreach_enrollments WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'outcome_reports', COUNT(*) FROM outcome_reports WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'outcome_checkins', COUNT(*) FROM outcome_checkins WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'observability_flags', COUNT(*) FROM observability_flags WHERE run_id IN (SELECT id FROM zz_del_runs) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'notifications', COUNT(*) FROM notifications WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'notification_preferences', COUNT(*) FROM notification_preferences WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'layer_income_records', COUNT(*) FROM layer_income_records WHERE action_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'kajabi_products', COUNT(*) FROM kajabi_products WHERE action_id IN (SELECT id FROM zz_del_actions) OR artifact_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'integration_signals', COUNT(*) FROM integration_signals WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'integration_activity_log', COUNT(*) FROM integration_activity_log WHERE action_id IN (SELECT id FROM zz_del_actions) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'goal_cards', COUNT(*) FROM goal_cards WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'goal_card_agent_changes', COUNT(*) FROM goal_card_agent_changes WHERE goal_card_id IN (SELECT id FROM zz_del_goals);
INSERT INTO zz_del_preview SELECT 'execution_runs', COUNT(*) FROM execution_runs WHERE obs_run_id IN (SELECT id FROM zz_del_runs) OR plan_id IN (SELECT id FROM zz_del_plans) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'email_campaigns', COUNT(*) FROM email_campaigns WHERE action_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'email_accounts', COUNT(*) FROM email_accounts WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'credit_redemptions', COUNT(*) FROM credit_redemptions WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'contacts', COUNT(*) FROM contacts WHERE source_action_id IN (SELECT id FROM zz_del_actions) OR source_cycle_id IN (SELECT id FROM zz_del_cycles) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'contact_invites', COUNT(*) FROM contact_invites WHERE contact_id IN (SELECT id FROM zz_del_contacts) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'contact_field_sources', COUNT(*) FROM contact_field_sources WHERE account_id IN (SELECT id FROM zz_del_mail) OR contact_id IN (SELECT id FROM zz_del_contacts);
INSERT INTO zz_del_preview SELECT 'contact_dnc_flags', COUNT(*) FROM contact_dnc_flags WHERE contact_id IN (SELECT id FROM zz_del_contacts);
INSERT INTO zz_del_preview SELECT 'contact_activities', COUNT(*) FROM contact_activities WHERE action_id IN (SELECT id FROM zz_del_actions) OR contact_id IN (SELECT id FROM zz_del_contacts) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'contact_account_links', COUNT(*) FROM contact_account_links WHERE account_id IN (SELECT id FROM zz_del_mail) OR contact_id IN (SELECT id FROM zz_del_contacts);
INSERT INTO zz_del_preview SELECT 'compliance_events', COUNT(*) FROM compliance_events WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'coaching_sessions', COUNT(*) FROM coaching_sessions WHERE seat_id IN (SELECT id FROM zz_del_seats);
INSERT INTO zz_del_preview SELECT 'coaching_notes', COUNT(*) FROM coaching_notes WHERE session_id IN (SELECT id FROM zz_del_csess);
INSERT INTO zz_del_preview SELECT 'calibration_runs', COUNT(*) FROM calibration_runs WHERE action_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'bio_pages', COUNT(*) FROM bio_pages WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'bio_page_visits', COUNT(*) FROM bio_page_visits WHERE bio_page_id IN (SELECT id FROM zz_del_bio);
INSERT INTO zz_del_preview SELECT 'bio_page_likes', COUNT(*) FROM bio_page_likes WHERE bio_page_id IN (SELECT id FROM zz_del_bio) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'bio_chat_sessions', COUNT(*) FROM bio_chat_sessions WHERE bio_page_id IN (SELECT id FROM zz_del_bio) OR contact_id IN (SELECT id FROM zz_del_contacts) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'bio_chat_messages', COUNT(*) FROM bio_chat_messages WHERE session_id IN (SELECT id FROM zz_del_chatsess);
INSERT INTO zz_del_preview SELECT 'bio_access_rules', COUNT(*) FROM bio_access_rules WHERE owner_user_id = @uid;
INSERT INTO zz_del_preview SELECT 'bio_access_requests', COUNT(*) FROM bio_access_requests WHERE owner_user_id = @uid;
INSERT INTO zz_del_preview SELECT 'bio_access_grants', COUNT(*) FROM bio_access_grants WHERE owner_user_id = @uid;
INSERT INTO zz_del_preview SELECT 'bayesian_posteriors', COUNT(*) FROM bayesian_posteriors WHERE simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'artifact_units', COUNT(*) FROM artifact_units WHERE artifact_id IN (SELECT id FROM zz_del_actions) OR contact_id IN (SELECT id FROM zz_del_contacts);
INSERT INTO zz_del_preview SELECT 'artifact_actions', COUNT(*) FROM artifact_actions WHERE artifact_id IN (SELECT id FROM zz_del_actions);
INSERT INTO zz_del_preview SELECT 'agent_shares', COUNT(*) FROM agent_shares WHERE owner_user_id = @uid OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'agent_runs', COUNT(*) FROM agent_runs WHERE action_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'agent_run_tools', COUNT(*) FROM agent_run_tools WHERE run_id IN (SELECT id FROM zz_del_runs);
INSERT INTO zz_del_preview SELECT 'agent_recipients', COUNT(*) FROM agent_recipients WHERE action_id IN (SELECT id FROM zz_del_actions) OR contact_id IN (SELECT id FROM zz_del_contacts) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'agent_plans', COUNT(*) FROM agent_plans WHERE action_id IN (SELECT id FROM zz_del_actions) OR simulation_id IN (SELECT id FROM zz_del_sims) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'agent_events', COUNT(*) FROM agent_events WHERE run_id IN (SELECT id FROM zz_del_runs);
INSERT INTO zz_del_preview SELECT 'agent_autonomy_config', COUNT(*) FROM agent_autonomy_config WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'agent_artifacts', COUNT(*) FROM agent_artifacts WHERE run_id IN (SELECT id FROM zz_del_runs) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'advisor_flags', COUNT(*) FROM advisor_flags WHERE action_id IN (SELECT id FROM zz_del_actions) OR advisor_access_id IN (SELECT id FROM zz_del_advisor) OR simulation_id IN (SELECT id FROM zz_del_sims);
INSERT INTO zz_del_preview SELECT 'activity_events', COUNT(*) FROM activity_events WHERE user_id = @uid;
INSERT INTO zz_del_preview SELECT 'action_items', COUNT(*) FROM action_items WHERE simulation_id IN (SELECT id FROM zz_del_sims) OR source_action_id IN (SELECT id FROM zz_del_actions) OR source_contact_id IN (SELECT id FROM zz_del_contacts) OR user_id = @uid;
INSERT INTO zz_del_preview SELECT 'action_approvals', COUNT(*) FROM action_approvals WHERE decided_by = @uid OR target_id IN (SELECT id FROM plan_steps WHERE plan_id IN (SELECT id FROM zz_del_plans));
INSERT INTO zz_del_preview SELECT 'account_exclusions', COUNT(*) FROM account_exclusions WHERE account_id IN (SELECT id FROM zz_del_mail);
SELECT * FROM zz_del_preview WHERE rows_to_delete > 0 ORDER BY tbl;

SET FOREIGN_KEY_CHECKS = 0;   -- every child row is deleted explicitly below

DELETE FROM email_logs WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR contact_id IN (SELECT id FROM zz_del_contacts)
    OR simulation_id IN (SELECT id FROM zz_del_sims)
    OR step_id IN (SELECT id FROM zz_del_steps);
DELETE FROM layer6_outcomes WHERE income_stream_id IN (SELECT id FROM zz_del_streams)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM layer6_execution_log WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR cycle_id IN (SELECT id FROM zz_del_cycles)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM action_steps WHERE agent_action_id IN (SELECT id FROM zz_del_actions)
    OR parent_action_id IN (SELECT id FROM zz_del_queue)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM layer6_share_tokens WHERE cycle_id IN (SELECT id FROM zz_del_cycles)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM layer6_action_queue WHERE agent_action_id IN (SELECT id FROM zz_del_actions)
    OR cycle_id IN (SELECT id FROM zz_del_cycles)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM income_streams WHERE layer_id IN (SELECT id FROM zz_del_layers);
DELETE FROM cycle_posterior_snapshots WHERE cycle_id IN (SELECT id FROM zz_del_cycles)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM collab_activities WHERE collaboration_id IN (SELECT id FROM zz_del_collabs)
    OR collaborator_id = @uid
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM artifact_versions WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM artifact_dependencies WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR upstream_action_id IN (SELECT id FROM zz_del_actions);
DELETE FROM advisor_notes WHERE advisor_access_id IN (SELECT id FROM zz_del_advisor)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM user_feedback WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM simulation_visibility WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM simulation_videos WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM simulation_layers WHERE simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM prefill_corrections WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM layer6_momentum WHERE simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM layer6_cycles WHERE simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM layer6_configs WHERE simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM collaborations WHERE invitee_id = @uid
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM blog_comment_likes WHERE comment_id IN (SELECT id FROM zz_del_comments)
    OR user_id = @uid;
DELETE FROM artifact_bundles WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM ai_interactions WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM agent_context WHERE simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM agent_actions WHERE simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM advisor_access WHERE partner_id IN (SELECT id FROM zz_del_partner)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM user_profiles WHERE user_id = @uid;
DELETE FROM sme_recommendations WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM simulations WHERE user_id = @uid;
DELETE FROM referral_signups WHERE partner_id IN (SELECT id FROM zz_del_partner)
    OR referred_user_id = @uid;
DELETE FROM corporate_employees WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM blog_likes WHERE post_id IN (SELECT id FROM zz_del_posts)
    OR user_id = @uid;
DELETE FROM blog_comments WHERE post_id IN (SELECT id FROM zz_del_posts)
    OR user_id = @uid;
DELETE FROM user_sessions WHERE user_id = @uid;
DELETE FROM simi_messages WHERE conversation_id IN (SELECT id FROM zz_del_convos);
DELETE FROM resumes WHERE user_id = @uid;
DELETE FROM referral_partners WHERE user_id = @uid;
DELETE FROM profile_inquiries WHERE profile_user_id = @uid;
DELETE FROM blog_reports WHERE reporter_user_id = @uid
    OR target_id IN (SELECT id FROM zz_del_comments)
    OR target_id IN (SELECT id FROM zz_del_posts);
DELETE FROM blog_posts WHERE author_user_id = @uid;
DELETE FROM user_integrations WHERE user_id = @uid;
DELETE FROM user_guide_prefs WHERE user_id = @uid;
DELETE FROM user_feature_overrides WHERE user_id = @uid;
DELETE FROM user_events WHERE user_id = @uid;
DELETE FROM user_discounts WHERE user_id = @uid;
DELETE FROM user_discount_redemptions WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM user_connections WHERE user_a_id = @uid
    OR user_b_id = @uid;
DELETE FROM transition_seats WHERE user_id = @uid;
DELETE FROM transition_outcomes WHERE seat_id IN (SELECT id FROM zz_del_seats);
DELETE FROM sync_runs WHERE account_id IN (SELECT id FROM zz_del_mail);
DELETE FROM suppression_list WHERE user_id = @uid;
DELETE FROM suggestion_blends WHERE share_id IN (SELECT id FROM zz_del_shares);
DELETE FROM social_post_queue WHERE artifact_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM sme_turns WHERE session_id IN (SELECT id FROM zz_del_smesess);
DELETE FROM sme_sessions WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM sme_briefings WHERE session_id IN (SELECT id FROM zz_del_smesess);
DELETE FROM sme_access_log WHERE user_id = @uid;
DELETE FROM skill_suggestions WHERE user_id = @uid;
DELETE FROM simulation_chat_messages WHERE session_id IN (SELECT id FROM zz_del_chatsess)
    OR simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM simi_conversations WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM signing_documents WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM sender_signals WHERE account_id IN (SELECT id FROM zz_del_mail);
DELETE FROM review_items WHERE account_id IN (SELECT id FROM zz_del_mail)
    OR contact_id IN (SELECT id FROM zz_del_contacts)
    OR user_id = @uid;
DELETE FROM reciprocity_ledger WHERE peer_user_id = @uid;
DELETE FROM published_pages WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM prospect_research_runs WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM platform_chats WHERE bio_page_id IN (SELECT id FROM zz_del_bio)
    OR chatter_user_id = @uid
    OR contact_id IN (SELECT id FROM zz_del_contacts)
    OR owner_user_id = @uid;
DELETE FROM platform_chat_messages WHERE chat_id IN (SELECT id FROM zz_del_pchats);
DELETE FROM plan_steps WHERE plan_id IN (SELECT id FROM zz_del_plans);
DELETE FROM peer_invites WHERE owner_user_id = @uid
    OR peer_user_id = @uid;
DELETE FROM parameter_suggestions WHERE peer_user_id = @uid
    OR share_id IN (SELECT id FROM zz_del_shares);
DELETE FROM page_views WHERE session_id IN (SELECT id FROM zz_del_chatsess)
    OR user_id = @uid;
DELETE FROM outreach_sends WHERE enrollment_id IN (SELECT id FROM zz_del_enroll)
    OR user_id = @uid;
DELETE FROM outreach_enrollments WHERE user_id = @uid;
DELETE FROM outcome_reports WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM outcome_checkins WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM observability_flags WHERE run_id IN (SELECT id FROM zz_del_runs)
    OR user_id = @uid;
DELETE FROM notifications WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM notification_preferences WHERE user_id = @uid;
DELETE FROM layer_income_records WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM kajabi_products WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR artifact_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM integration_signals WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM integration_activity_log WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR user_id = @uid;
DELETE FROM goal_cards WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM goal_card_agent_changes WHERE goal_card_id IN (SELECT id FROM zz_del_goals);
DELETE FROM execution_runs WHERE obs_run_id IN (SELECT id FROM zz_del_runs)
    OR plan_id IN (SELECT id FROM zz_del_plans)
    OR user_id = @uid;
DELETE FROM email_campaigns WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM email_accounts WHERE user_id = @uid;
DELETE FROM credit_redemptions WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM contacts WHERE source_action_id IN (SELECT id FROM zz_del_actions)
    OR source_cycle_id IN (SELECT id FROM zz_del_cycles)
    OR user_id = @uid;
DELETE FROM contact_invites WHERE contact_id IN (SELECT id FROM zz_del_contacts)
    OR user_id = @uid;
DELETE FROM contact_field_sources WHERE account_id IN (SELECT id FROM zz_del_mail)
    OR contact_id IN (SELECT id FROM zz_del_contacts);
DELETE FROM contact_dnc_flags WHERE contact_id IN (SELECT id FROM zz_del_contacts);
DELETE FROM contact_activities WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR contact_id IN (SELECT id FROM zz_del_contacts)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM contact_account_links WHERE account_id IN (SELECT id FROM zz_del_mail)
    OR contact_id IN (SELECT id FROM zz_del_contacts);
DELETE FROM compliance_events WHERE user_id = @uid;
DELETE FROM coaching_sessions WHERE seat_id IN (SELECT id FROM zz_del_seats);
DELETE FROM coaching_notes WHERE session_id IN (SELECT id FROM zz_del_csess);
DELETE FROM calibration_runs WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM bio_pages WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM bio_page_visits WHERE bio_page_id IN (SELECT id FROM zz_del_bio);
DELETE FROM bio_page_likes WHERE bio_page_id IN (SELECT id FROM zz_del_bio)
    OR user_id = @uid;
DELETE FROM bio_chat_sessions WHERE bio_page_id IN (SELECT id FROM zz_del_bio)
    OR contact_id IN (SELECT id FROM zz_del_contacts)
    OR user_id = @uid;
DELETE FROM bio_chat_messages WHERE session_id IN (SELECT id FROM zz_del_chatsess);
DELETE FROM bio_access_rules WHERE owner_user_id = @uid;
DELETE FROM bio_access_requests WHERE owner_user_id = @uid;
DELETE FROM bio_access_grants WHERE owner_user_id = @uid;
DELETE FROM bayesian_posteriors WHERE simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM artifact_units WHERE artifact_id IN (SELECT id FROM zz_del_actions)
    OR contact_id IN (SELECT id FROM zz_del_contacts);
DELETE FROM artifact_actions WHERE artifact_id IN (SELECT id FROM zz_del_actions);
DELETE FROM agent_shares WHERE owner_user_id = @uid
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM agent_runs WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM agent_run_tools WHERE run_id IN (SELECT id FROM zz_del_runs);
DELETE FROM agent_recipients WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR contact_id IN (SELECT id FROM zz_del_contacts)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM agent_plans WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR simulation_id IN (SELECT id FROM zz_del_sims)
    OR user_id = @uid;
DELETE FROM agent_events WHERE run_id IN (SELECT id FROM zz_del_runs);
DELETE FROM agent_autonomy_config WHERE user_id = @uid;
DELETE FROM agent_artifacts WHERE run_id IN (SELECT id FROM zz_del_runs)
    OR user_id = @uid;
DELETE FROM advisor_flags WHERE action_id IN (SELECT id FROM zz_del_actions)
    OR advisor_access_id IN (SELECT id FROM zz_del_advisor)
    OR simulation_id IN (SELECT id FROM zz_del_sims);
DELETE FROM activity_events WHERE user_id = @uid;
DELETE FROM action_items WHERE simulation_id IN (SELECT id FROM zz_del_sims)
    OR source_action_id IN (SELECT id FROM zz_del_actions)
    OR source_contact_id IN (SELECT id FROM zz_del_contacts)
    OR user_id = @uid;
DELETE FROM action_approvals WHERE decided_by = @uid
    OR target_id IN (SELECT id FROM plan_steps WHERE plan_id IN (SELECT id FROM zz_del_plans));
DELETE FROM account_exclusions WHERE account_id IN (SELECT id FROM zz_del_mail);

-- Keep other people's rows, clear this user's id from them.
UPDATE action_approvals SET decided_by = NULL WHERE decided_by = @uid;
UPDATE artifact_actions SET actor_id = NULL WHERE actor_id = @uid;
UPDATE bio_chat_sessions SET takeover_by = NULL WHERE takeover_by = @uid;
UPDATE contact_dnc_flags SET set_by = NULL WHERE set_by = @uid;
UPDATE contact_dnc_flags SET lifted_by = NULL WHERE lifted_by = @uid;
UPDATE contact_invites SET joined_user_id = NULL WHERE joined_user_id = @uid;
UPDATE feature_flags SET updated_by = NULL WHERE updated_by = @uid;
UPDATE guardrail_policies SET created_by = NULL WHERE created_by = @uid;
UPDATE guardrail_rules SET created_by = NULL WHERE created_by = @uid;
UPDATE layer_income_records SET voided_by_id = NULL WHERE voided_by_id = @uid;
UPDATE legal_texts SET updated_by = NULL WHERE updated_by = @uid;
UPDATE outreach_templates SET updated_by = NULL WHERE updated_by = @uid;
UPDATE transition_cohorts SET created_by = NULL WHERE created_by = @uid;
UPDATE user_discounts SET granted_by = NULL WHERE granted_by = @uid;
UPDATE user_feature_overrides SET set_by = NULL WHERE set_by = @uid;
UPDATE bundle_type_configs SET updated_by = NULL WHERE updated_by = @uid;
UPDATE corporate_accounts SET admin_user_id = NULL WHERE admin_user_id = @uid;
UPDATE platform_settings SET updated_by = NULL WHERE updated_by = @uid;
UPDATE simi_smes SET auth_user_id = NULL WHERE auth_user_id = @uid;
UPDATE simulation_discounts SET created_by = NULL WHERE created_by = @uid;
-- advisor_access.granted_by is NOT NULL: rows naming this user there are left as-is (actor record).
UPDATE agent_actions SET created_by = NULL WHERE created_by = @uid;
UPDATE collaborations SET created_by = NULL WHERE created_by = @uid;
UPDATE artifact_versions SET created_by = NULL WHERE created_by = @uid;
-- layer6_share_tokens.created_by is NOT NULL: rows naming this user there are left as-is (actor record).

DELETE FROM users WHERE id = @uid;
SET FOREIGN_KEY_CHECKS = 1;

ROLLBACK;   -- <<< change to COMMIT once the preview counts look right

-- Clean up the helper tables (after the transaction ends).
DROP TABLE IF EXISTS zz_del_sims;
DROP TABLE IF EXISTS zz_del_actions;
DROP TABLE IF EXISTS zz_del_contacts;
DROP TABLE IF EXISTS zz_del_bio;
DROP TABLE IF EXISTS zz_del_mail;
DROP TABLE IF EXISTS zz_del_cycles;
DROP TABLE IF EXISTS zz_del_queue;
DROP TABLE IF EXISTS zz_del_steps;
DROP TABLE IF EXISTS zz_del_layers;
DROP TABLE IF EXISTS zz_del_streams;
DROP TABLE IF EXISTS zz_del_runs;
DROP TABLE IF EXISTS zz_del_plans;
DROP TABLE IF EXISTS zz_del_chatsess;
DROP TABLE IF EXISTS zz_del_pchats;
DROP TABLE IF EXISTS zz_del_convos;
DROP TABLE IF EXISTS zz_del_shares;
DROP TABLE IF EXISTS zz_del_advisor;
DROP TABLE IF EXISTS zz_del_collabs;
DROP TABLE IF EXISTS zz_del_posts;
DROP TABLE IF EXISTS zz_del_comments;
DROP TABLE IF EXISTS zz_del_partner;
DROP TABLE IF EXISTS zz_del_enroll;
DROP TABLE IF EXISTS zz_del_resumes;
DROP TABLE IF EXISTS zz_del_seats;
DROP TABLE IF EXISTS zz_del_csess;
DROP TABLE IF EXISTS zz_del_smesess;
DROP TABLE IF EXISTS zz_del_goals;
DROP TABLE IF EXISTS zz_del_preview;
