"""sql/tools/delete_user.sql: deletes exactly one user's rows, keeps everyone else's.

Seeds one row per user in every table the script deletes from (wired to that
user's parent rows), runs the script on SQLite with the MySQL-only lines
translated, and checks: user A's rows are gone everywhere, user B's are all
still there, kept tables keep A's rows, and actor columns are cleared.

    python test_delete_user_sql.py
"""
import os
import re
import runpy
import sys
from datetime import datetime

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-only')
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label, ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


SET_TABLE = {'d_sims': 'simulations', 'd_actions': 'agent_actions', 'd_contacts': 'contacts', 'd_bio': 'bio_pages',
             'd_mail': 'email_accounts', 'd_cycles': 'layer6_cycles', 'd_queue': 'layer6_action_queue',
             'd_steps': 'action_steps', 'd_layers': 'simulation_layers', 'd_streams': 'income_streams',
             'd_runs': 'agent_runs', 'd_plans': 'agent_plans', 'd_chatsess': 'bio_chat_sessions',
             'd_pchats': 'platform_chats', 'd_convos': 'simi_conversations', 'd_shares': 'agent_shares',
             'd_advisor': 'advisor_access', 'd_collabs': 'collaborations', 'd_posts': 'blog_posts',
             'd_comments': 'blog_comments', 'd_partner': 'referral_partners', 'd_enroll': 'outreach_enrollments',
             'd_resumes': 'resumes', 'd_seats': 'transition_seats', 'd_csess': 'coaching_sessions'}


def dummy(col, now, u):
    import sqlalchemy as sa
    t = col.type
    if isinstance(t, sa.Enum):
        return t.enums[0]
    if isinstance(t, sa.Boolean):
        return False
    if isinstance(t, (sa.Integer, sa.Numeric, sa.Float)):
        return 0
    if isinstance(t, (sa.DateTime, sa.Date)):
        return now
    if isinstance(t, sa.JSON):
        return {}
    return ('x%s' % u)[:getattr(t, 'length', None) or 64]


def main():
    gen = runpy.run_path(os.path.join(ROOT, 'sql', 'tools', 'gen_delete_user.py'), run_name='gen')
    committed = open(os.path.join(ROOT, 'sql', 'tools', 'delete_user.sql'), encoding='utf-8').read()

    print('\n1. The committed script is current')
    check('delete_user.sql matches the generator (re-run sql/tools/gen_delete_user.py after model changes)',
          committed == gen['SQL_TEXT'])
    check('every table is classified', not gen['unknown'], gen['unknown'])

    from app import create_app
    from app.extensions import db
    app = create_app('testing')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    with app.app_context():
        import importlib
        import pkgutil
        import app.models as M
        for m in pkgutil.iter_modules(M.__path__):
            importlib.import_module('app.models.' + m.name)
        db.create_all()
        tables = {t.name: t for t in db.metadata.sorted_tables}
        deletes, KEEP = gen['deletes'], gen['KEEP']
        PARENT_COLS, USER_COLS, SKIP = gen['PARENT_COLS'], gen['USER_COLS'], gen['SKIP_COLS']
        now = datetime.utcnow()
        users = {'A': 'UA0000001', 'B': 'UB0000001'}

        def rid(table, u):
            return ('%s_%s' % (u, table))[:60]

        def row_for(table, u):
            t = tables[table]
            vals = {}
            for c in t.columns:
                if (table, c.name) in SKIP:
                    pass
                elif c.name in USER_COLS:
                    vals[c.name] = users[u]
                elif c.name in PARENT_COLS:
                    vals[c.name] = rid(SET_TABLE[PARENT_COLS[c.name][0]], u)
                if c.name not in vals:
                    if c.primary_key and c.name == 'id':
                        # integer keys get a number (BigInteger doesn't autoincrement in SQLite)
                        vals['id'] = (len(keys) + 1000) if isinstance(c.type, db.Integer) else rid(table, u)
                    elif not c.nullable and c.default is None and c.server_default is None:
                        vals[c.name] = dummy(c, now, u)
            if 'email' in t.columns and table == 'users':
                vals['email'] = '%s@x.com' % u.lower()
            return vals

        seeded, keys, failed = {}, {}, []
        for u in users:
            db.session.execute(tables['users'].insert().values(**dict(row_for('users', u), id=users[u])))
            db.session.commit()
            for name in list(deletes) + list(KEEP):
                if name not in tables:
                    continue
                v = row_for(name, u)
                # make every row link to its user even where the user column is in SKIP or kept tables
                if name in KEEP and 'user_id' in tables[name].columns:
                    v['user_id'] = users[u]
                if name == 'action_approvals':
                    v['decided_by'] = users[u]       # approvals are matched by who decided them
                if name == 'coaching_notes':
                    v['session_id'] = rid('coaching_sessions', u)
                try:
                    with db.session.begin_nested():        # a failed insert can't undo the others
                        res = db.session.execute(tables[name].insert().values(**v))
                    keys[(name, u)] = res.inserted_primary_key[0]
                    seeded.setdefault(name, set()).add(u)
                except Exception as exc:
                    failed.append((name, str(exc)[:80]))
            db.session.commit()
        # Actor columns on another user's (B's) row and on a platform table.
        db.session.execute(tables['platform_settings'].insert().values(id='PS1', key='k', value='v', updated_by=users['A']))
        db.session.commit()
        check('every table could be seeded for both users', not failed, failed)

        def count(table, u):
            t = tables[table]
            pk = list(t.primary_key.columns)[0]
            return db.session.execute(db.select(db.func.count()).select_from(t).where(pk == keys[(table, u)])).scalar()

        before_b = {n: count(n, 'B') for n in seeded}

        print('\n2. Run the script (SQLite translation) for user A')
        sql = committed
        sql = re.sub(r"^SET @email.*$", '', sql, flags=re.M)
        sql = re.sub(r"^SET @uid.*$", '', sql, flags=re.M)
        sql = re.sub(r"^SET FOREIGN_KEY_CHECKS.*$", '', sql, flags=re.M)
        sql = sql.replace('@uid', "'%s'" % users['A']).replace('START TRANSACTION;', 'BEGIN;')
        sql = sql.replace('ROLLBACK;   -- <<<', 'COMMIT;   -- <<<')
        raw = db.engine.raw_connection()
        try:
            raw.executescript(sql)
            ran = True
        except Exception as exc:
            ran = exc
        check('the script runs end to end', ran is True, ran)
        db.session.expire_all()

        print('\n3. Results')
        left_a = [n for n in seeded if n in deletes and 'A' in seeded[n] and count(n, 'A')]
        check('every one of user A\'s rows is deleted (%d tables)' % len([n for n in seeded if n in deletes]),
              not left_a, left_a)
        lost_b = [n for n in seeded if 'B' in seeded[n] and before_b[n] and not count(n, 'B')]
        check('user B\'s rows are all still there', not lost_b, lost_b)
        check('user A is deleted, user B is not',
              db.session.execute(db.text("SELECT COUNT(*) FROM users WHERE id='%s'" % users['A'])).scalar() == 0
              and db.session.execute(db.text("SELECT COUNT(*) FROM users WHERE id='%s'" % users['B'])).scalar() == 1)
        kept = [n for n in KEEP if n in seeded and 'A' in seeded[n] and not count(n, 'A')]
        check('kept tables keep A\'s rows (consents, payments, audit, global opt-outs)', not kept, kept)
        check('actor columns naming A are cleared on rows that stay',
              db.session.execute(db.text("SELECT updated_by FROM platform_settings WHERE id='PS1'")).scalar() is None)
        leftovers = db.session.execute(db.text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'zz_del_%'")).fetchall()
        check('helper tables are dropped', not leftovers, leftovers)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
