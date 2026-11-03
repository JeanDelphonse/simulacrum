"""Generate sql/tools/delete_user.sql from the models, so no table is missed.

Re-run after adding a model:   python sql/tools/gen_delete_user.py
test_delete_user_sql.py fails if the committed SQL is out of date or if any
table is left unclassified.
"""
import importlib
import os
import pkgutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault('SECRET_KEY', 't')
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
from app import create_app            # noqa
from app.extensions import db         # noqa

app = create_app('testing')
with app.app_context():
    import app.models as M
    for m in pkgutil.iter_modules(M.__path__):
        importlib.import_module('app.models.' + m.name)
    tables = {t.name: t for t in db.metadata.sorted_tables}

# ── Rows to keep (evidence / legal / other people's records) ────────────────
KEEP = {
    'audit_logs': 'audit trail (kept as evidence)',
    'resume_consents': 'consent records must survive deletion (FR-TOS-09)',
    'payment_records': 'financial record (tax / chargebacks)',
    'commissions': 'partner financial record',
    'partner_payouts': 'partner financial record',
    'admin_access_audit': 'admin access audit trail',
    'integration_audit_log': 'admin audit trail',
    'feature_changes': 'admin audit trail',
    'email_suppressions': 'global opt-out list: an opt-out must outlive the account',
}
# Platform tables with no per-user rows.
GLOBAL = {'admin_stage_rules', 'calibration_audit', 'calibration_configs', 'discovery_profiles',
          'discovery_candidates', 'expertise_categories', 'reference_data_points', 'reference_datasets',
          'tool_registry', 'platform_settings', 'bundle_type_configs', 'guardrail_rules',
          'guardrail_policies', 'outreach_templates', 'simulation_discounts', 'feature_flags',
          'job_applications', 'org_invoices', 'org_sme_pod', 'admin_prospects', 'admin_prospect_touches',
          'corporate_accounts', 'referral_invitations', 'simi_smes',
          'transition_cohorts'}   # employer-level records

# Columns that hold THE user's id: rows matching are the user's data -> delete.
USER_COLS = {'user_id', 'owner_user_id', 'author_user_id', 'profile_user_id', 'client_user_id',
             'invitee_id', 'collaborator_id', 'chatter_user_id', 'user_a_id', 'user_b_id', 'reporter_user_id',
             'peer_user_id', 'referred_user_id'}
# Columns that only record who acted on someone else's row -> set NULL.
ACTOR_COLS = {'created_by', 'updated_by', 'set_by', 'lifted_by', 'decided_by', 'granted_by', 'changed_by',
              'actor_id', 'takeover_by', 'voided_by_id', 'admin_user_id',
              'subject_user_id', 'target_user_id', 'auth_user_id'}

# Parent id sets, as temporary tables. Order matters (later ones use earlier ones).
SETS = [
    ('d_sims', "SELECT id FROM simulations WHERE user_id = @uid"),
    ('d_actions', "SELECT id FROM agent_actions WHERE simulation_id IN (SELECT id FROM d_sims)"),
    ('d_contacts', "SELECT id FROM contacts WHERE user_id = @uid"),
    ('d_bio', "SELECT id FROM bio_pages WHERE user_id = @uid"),
    ('d_mail', "SELECT id FROM email_accounts WHERE user_id = @uid"),
    ('d_cycles', "SELECT id FROM layer6_cycles WHERE simulation_id IN (SELECT id FROM d_sims)"),
    ('d_queue', "SELECT id FROM layer6_action_queue WHERE simulation_id IN (SELECT id FROM d_sims)"),
    ('d_steps', "SELECT id FROM action_steps WHERE simulation_id IN (SELECT id FROM d_sims)"),
    ('d_layers', "SELECT id FROM simulation_layers WHERE simulation_id IN (SELECT id FROM d_sims)"),
    ('d_streams', "SELECT id FROM income_streams WHERE layer_id IN (SELECT id FROM d_layers)"),
    ('d_runs', "SELECT id FROM agent_runs WHERE user_id = @uid OR simulation_id IN (SELECT id FROM d_sims)"),
    ('d_plans', "SELECT id FROM agent_plans WHERE user_id = @uid OR simulation_id IN (SELECT id FROM d_sims)"),
    ('d_chatsess', "SELECT id FROM bio_chat_sessions WHERE user_id = @uid OR bio_page_id IN (SELECT id FROM d_bio)"),
    ('d_pchats', "SELECT id FROM platform_chats WHERE owner_user_id = @uid OR chatter_user_id = @uid"),
    ('d_convos', "SELECT id FROM simi_conversations WHERE user_id = @uid OR simulation_id IN (SELECT id FROM d_sims)"),
    ('d_shares', "SELECT id FROM agent_shares WHERE owner_user_id = @uid OR simulation_id IN (SELECT id FROM d_sims)"),
    ('d_advisor', "SELECT id FROM advisor_access WHERE simulation_id IN (SELECT id FROM d_sims)"),
    ('d_collabs', "SELECT id FROM collaborations WHERE simulation_id IN (SELECT id FROM d_sims) OR invitee_id = @uid"),
    ('d_posts', "SELECT id FROM blog_posts WHERE author_user_id = @uid"),
    ('d_comments', "SELECT id FROM blog_comments WHERE user_id = @uid OR post_id IN (SELECT id FROM d_posts)"),
    ('d_partner', "SELECT id FROM referral_partners WHERE user_id = @uid"),
    ('d_enroll', "SELECT id FROM outreach_enrollments WHERE user_id = @uid"),
    ('d_resumes', "SELECT id FROM resumes WHERE user_id = @uid"),
    ('d_seats', "SELECT id FROM transition_seats WHERE user_id = @uid"),
    ('d_csess', "SELECT id FROM coaching_sessions WHERE seat_id IN (SELECT id FROM d_seats)"),
]
# Column name -> parent set(s) whose ids mean "this row belongs to the user".
PARENT_COLS = {
    'simulation_id': ['d_sims'],
    'action_id': ['d_actions'], 'agent_action_id': ['d_actions'], 'upstream_action_id': ['d_actions'],
    'source_action_id': ['d_actions'], 'artifact_id': ['d_actions'],
    'contact_id': ['d_contacts'], 'source_contact_id': ['d_contacts'],
    'bio_page_id': ['d_bio'], 'account_id': ['d_mail'],
    'cycle_id': ['d_cycles'], 'source_cycle_id': ['d_cycles'], 'parent_action_id': ['d_queue'],
    'step_id': ['d_steps'], 'layer_id': ['d_layers'], 'income_stream_id': ['d_streams'],
    'run_id': ['d_runs'], 'obs_run_id': ['d_runs'], 'plan_id': ['d_plans'],
    'session_id': ['d_chatsess'], 'chat_id': ['d_pchats'], 'conversation_id': ['d_convos'],
    'share_id': ['d_shares'], 'advisor_access_id': ['d_advisor'], 'collaboration_id': ['d_collabs'],
    'post_id': ['d_posts'], 'comment_id': ['d_comments'], 'enrollment_id': ['d_enroll'],
    'partner_id': ['d_partner'], 'resume_id': ['d_resumes'],
    'seat_id': ['d_seats'],
}
# Per-table overrides where a generic column means something else.
SKIP_COLS = {
    ('action_items', 'source_artifact_id'), ('action_items', 'source_income_id'),
    ('artifact_units', 'email_log_id'), ('artifact_units', 'batch_id'), ('artifact_units', 'unit_id'),
    ('contact_field_sources', 'message_id'), ('observability_flags', 'target_id'), ('observability_flags', 'rule_id'),
    ('action_approvals', 'target_id'), ('blog_reports', 'target_id'), ('admin_access_audit', 'target_id'),
    ('execution_runs', 'step_id'), ('artifact_actions', 'unit_id'), ('suggestion_blends', 'cycle_id'),
    ('user_profiles', 'sme_id'), ('user_profiles', 'org_id'), ('credit_redemptions', 'org_id'),
    ('corporate_employees', 'org_id'), ('sme_recommendations', 'sme_id'), ('sme_access_log', 'sme_id'),
    ('contact_dnc_flags', 'source_change_id'), ('outcome_reports', 'run_id'),
    ('artifact_versions', 'parent_version_id'), ('blog_comments', 'parent_comment_id'),
    ('simulations', 'resume_id'), ('resumes', 'consent_id'),
    ('contact_activities', 'created_by'),   # 'agent' | 'user', not a user id
    ('coaching_notes', 'session_id'),       # a coaching session, not a bio-chat session
    ('coaching_sessions', 'coach_id'), ('transition_seats', 'coach_id'),
}
# action_approvals.target_id can point at a plan step or a send batch; tie it to the user's plans.
EXTRA_WHERE = {
    'action_approvals': ["target_id IN (SELECT id FROM plan_steps WHERE plan_id IN (SELECT id FROM d_plans))",
                         "decided_by = @uid"],
    'blog_reports': ["target_id IN (SELECT id FROM d_posts)", "target_id IN (SELECT id FROM d_comments)"],
    'observability_flags': ["run_id IN (SELECT id FROM d_runs)"],
    'artifact_units': ["artifact_id IN (SELECT id FROM d_actions)"],
    'suppression_list': [],
    'coaching_notes': ["session_id IN (SELECT id FROM d_csess)"],
}

deletes, nulls, unknown = {}, [], []
for name, t in tables.items():
    if name in KEEP or name in GLOBAL or name == 'users':
        continue
    conds = []
    for c in t.columns:
        if (name, c.name) in SKIP_COLS:
            continue
        if c.name in USER_COLS:
            conds.append('%s = @uid' % c.name)
        elif c.name in PARENT_COLS:
            conds += ['%s IN (SELECT id FROM %s)' % (c.name, s) for s in PARENT_COLS[c.name]]
    conds += EXTRA_WHERE.get(name, [])
    if conds:
        deletes[name] = sorted(set(conds))
    else:
        unknown.append(name)
# Actor columns on rows that survive (other users' rows, kept tables): set NULL if nullable.
for name, t in tables.items():
    if name == 'users' or name in KEEP:
        continue                 # kept evidence keeps its ids as recorded
    for c in t.columns:
        if (name, c.name) in SKIP_COLS:
            continue
        if c.name in ACTOR_COLS and (name not in deletes or c.name not in USER_COLS):
            nulls.append((name, c.name, c.nullable))

if unknown:
    raise SystemExit('UNCLASSIFIED tables (add them to KEEP, GLOBAL or a column rule): %s' % unknown)

# Delete children before parents: reverse of FK-sorted order.
order = [n for n in reversed([t.name for t in db.metadata.sorted_tables]) if n in deletes]
out = []
w = out.append
w("""-- delete_user.sql — permanently delete ONE user and everything that is theirs.
-- Generated from the models (all %d tables reviewed). MySQL 5.7+/MariaDB.
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
%s
-- Other users' rows that merely mention this user as the actor (created_by,
-- decided_by, granted_by …) are kept with that column set to NULL.
-- If the user is the admin of a corporate account, that organisation and its
-- other members are NOT deleted — reassign it first (Admin > Organizations).
--
-- FILES ON DISK are not touched by SQL: resumes, avatars, videos and QR images
-- under the uploads folder. Remove them separately.
""" % (len(tables), '\n'.join('--   %-24s %s' % (k, v) for k, v in KEEP.items())))
w("SET @email := 'user@example.com';   -- <<< the user to delete")
w("SET @uid := (SELECT id FROM users WHERE LOWER(email) = LOWER(@email));")
w("SELECT @uid AS user_id, (SELECT full_name FROM users WHERE id = @uid) AS name,")
w("       (SELECT COUNT(*) FROM corporate_accounts WHERE admin_user_id = @uid) AS orgs_they_admin;")
w("-- Stop here if user_id is NULL (no such email) or orgs_they_admin > 0.\n")
# Ordinary helper tables, not TEMPORARY ones: MySQL refuses to open a temporary
# table twice in one statement, and the deletes below need that. CREATE TABLE
# commits implicitly, so they are created BEFORE the transaction starts, filled
# inside it, and dropped after COMMIT/ROLLBACK.
w("-- Helper tables (snapshots of the ids to delete). Created before the")
w("-- transaction because CREATE TABLE commits implicitly in MySQL.")
for name, _ in SETS + [('d_preview', '')]:
    w("DROP TABLE IF EXISTS %s;" % name)
for name, _ in SETS:
    w("CREATE TABLE %s (id CHAR(9) PRIMARY KEY);" % name)
w("CREATE TABLE d_preview (tbl VARCHAR(64) PRIMARY KEY, rows_to_delete INT);")
w("")
w("START TRANSACTION;\n")
for name, sql in SETS:
    w("INSERT INTO %s %s;" % (name, sql))
w("")
w("-- Preview: rows that will be deleted, per table.")
for n in order:
    w("INSERT INTO d_preview SELECT '%s', COUNT(*) FROM %s WHERE %s;" % (n, n, ' OR '.join(deletes[n])))
w("SELECT * FROM d_preview WHERE rows_to_delete > 0 ORDER BY tbl;\n")
w("SET FOREIGN_KEY_CHECKS = 0;   -- every child row is deleted explicitly below\n")
for n in order:
    w("DELETE FROM %s WHERE %s;" % (n, '\n    OR '.join(deletes[n])))
w("")
w("-- Keep other people's rows, clear this user's id from them.")
for n, c, nullable in nulls:
    if nullable:
        w("UPDATE %s SET %s = NULL WHERE %s = @uid;" % (n, c, c))
    else:
        w("-- %s.%s is NOT NULL: rows naming this user there are left as-is (actor record)." % (n, c))
w("")
w("DELETE FROM users WHERE id = @uid;")
w("SET FOREIGN_KEY_CHECKS = 1;\n")
w("ROLLBACK;   -- <<< change to COMMIT once the preview counts look right\n")
w("-- Clean up the helper tables (after the transaction ends).")
for name, _ in SETS + [('d_preview', '')]:
    w("DROP TABLE IF EXISTS %s;" % name)

# Helper tables get a prefix no real table could have.
import re as _re
_names = [n for n, _ in SETS] + ['d_preview']
SQL_TEXT = _re.sub(r'\b(%s)\b' % '|'.join(_names), lambda m: 'zz_del_' + m.group(1)[2:], '\n'.join(out) + '\n')
if __name__ == '__main__':
    open(os.path.join(ROOT, 'sql', 'tools', 'delete_user.sql'), 'w', encoding='utf-8', newline='\n').write(SQL_TEXT)
    print('tables', len(tables), 'deletes', len(deletes), 'nulls', len(nulls))
