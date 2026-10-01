"""Column filters on admin tables.

Two halves.

Server side: the users table is filtered in the browser, so it can only search
the rows the page renders. The page used to render the 50 newest users, which
made "no match" a false answer for everyone older. These checks pin the higher,
configurable cap and the notice shown when the list is still partial.

Browser side: run under jsdom against the real admin_table_filter.js and the
real pagination code lifted out of admin/index.html — not copies of either — so
what is tested is what ships. The one interaction that matters most is the one
most likely to regress: pagination and the filter both decide whether a row is
visible, and if they ever write the same property a page turn un-hides a
filtered-out row.

The browser half needs node and jsdom. Point ATF_JSDOM_DIR at a directory whose
node_modules contains jsdom; without it, those checks are reported as skipped
rather than silently passing.

    ATF_JSDOM_DIR=/path/to/scratch python test_admin_table_filter.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

os.environ.setdefault('SECRET_KEY', 'test-only')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS, FAIL, SKIP = [], [], []


def check(label, condition, detail=''):
    (PASS if condition else FAIL).append(label)
    line = '  {} {}{}'.format('ok  ' if condition else 'FAIL', label,
                              ('  -- ' + str(detail)) if detail else '')
    enc = sys.stdout.encoding or 'ascii'
    print(line.encode(enc, 'replace').decode(enc))


def section(title):
    print('\n' + title)


# ── The browser harness ─────────────────────────────────────────────────────
# Written to a temp file and run under node. Reports one JSON line per check so
# the Python side can fold them into the same pass/fail tally.
HARNESS = r'''
const fs = require('fs');
const { JSDOM } = require('jsdom');

const filterSrc = fs.readFileSync(process.env.ATF_FILTER_JS, 'utf8');
const paginationSrc = fs.readFileSync(process.env.ATF_PAGINATION_JS, 'utf8');

const results = [];
function check(label, ok, detail) {
  results.push({ label, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
}
const wait = ms => new Promise(r => setTimeout(r, ms));

function usersMarkup(n) {
  const rows = [];
  for (let i = 0; i < n; i++) {
    const sims = i % 7;                         // 0..6
    const spend = (i * 37.5).toFixed(2);        // "$0.00" .. growing
    const domain = i % 3 === 0 ? 'acme.com' : (i % 3 === 1 ? 'globex.io' : 'initech.org');
    const status = i % 10 === 0 ? 'Deactivated' : 'Active';
    rows.push(
      '<tr id="user-row-' + i + '">' +
      '<td>User ' + i + '</td>' +
      '<td>user' + i + '@' + domain + '</td>' +
      '<td>' + sims + '</td>' +
      '<td>$' + Number(spend).toLocaleString('en-US', {minimumFractionDigits: 2}) + '</td>' +
      '<td>2026-0' + (1 + (i % 9)) + '-15</td>' +
      '<td>—</td>' +
      '<td><span class="user-status-badge">' + status + '</span></td>' +
      '<td><button>Edit</button><button>Cycles</button></td>' +
      '</tr>');
  }
  return '<section>' +
    '<table class="admin-table" id="usersTable" data-filter="on">' +
    '<thead><tr><th>Name</th><th>Email</th><th>Simulations</th><th>Spend</th>' +
    '<th>Joined</th><th>Public Profile</th><th>Status</th><th>Actions</th></tr></thead>' +
    '<tbody>' + rows.join('') + '</tbody></table>' +
    '<div id="usersPagination" style="display:none">' +
    '<span id="usersPageInfo"></span>' +
    '<button id="usersPrevBtn">Prev</button><button id="usersNextBtn">Next</button>' +
    '</div></section>';
}

(async () => {
  const N = 60;
  const dom = new JSDOM('<!doctype html><html><head></head><body>' + usersMarkup(N) +
    '<div id="later"></div></body></html>', { runScripts: 'outside-only' });
  const { window } = dom;
  const doc = window.document;

  // Same order as the page: the filter script is included first, then the
  // inline pagination runs. jsdom is still `loading` at this point, exactly as
  // a browser is while it executes a body script, so the filter defers
  // attaching to DOMContentLoaded — wait for it, as a user implicitly does.
  check('the document is still loading when scripts run', doc.readyState === 'loading',
        doc.readyState);
  window.eval(filterSrc);
  window.eval(paginationSrc);
  await new Promise(resolve => {
    if (doc.readyState !== 'loading') return resolve();
    doc.addEventListener('DOMContentLoaded', () => resolve());
  });
  await wait(0);

  const table = doc.getElementById('usersTable');
  const rows = Array.from(table.tBodies[0].rows);
  const shown = () => rows.filter(r => r.style.display !== 'none' && !r.classList.contains('atf-hidden'));
  const matching = () => rows.filter(r => !r.classList.contains('atf-hidden'));
  const info = () => doc.getElementById('usersPageInfo').textContent;
  const inputFor = col => table.querySelector('tr.atf-row .atf-input[data-col="' + col + '"]');

  function type(col, value) {
    const input = inputFor(col);
    input.value = value;
    input.dispatchEvent(new window.Event('input'));
  }

  // ── Attachment ──────────────────────────────────────────────────────────
  const filterRow = table.querySelector('thead tr.atf-row');
  check('a filter row is added under the header', !!filterRow);
  check('it has one cell per column', filterRow && filterRow.cells.length === 8,
        filterRow && filterRow.cells.length);
  check('every data column gets a filter box',
        [0,1,2,3,4,5,6].every(c => inputFor(c)));
  check('the Actions column gets none', !inputFor(7));
  check('boxes are labelled for screen readers',
        inputFor(1).getAttribute('aria-label') === 'Filter Email',
        inputFor(1).getAttribute('aria-label'));
  check('the syntax is documented on hover', /compare numbers/.test(inputFor(2).title));
  check('styles are injected exactly once',
        doc.querySelectorAll('#atf-styles').length === 1);

  // ── Pagination before any filter ────────────────────────────────────────
  check('pagination starts on page one of everything',
        shown().length === 25 && info() === 'Showing 1–25 of 60', info());
  check('pagination controls are offered', doc.getElementById('usersPagination').style.display === 'flex');

  // ── Text filtering ─────────────────────────────────────────────────────
  type(1, 'acme');
  await wait(200);
  check('a text filter keeps only matching rows',
        matching().length === 20 && matching().every(r => /acme/.test(r.cells[1].textContent)),
        matching().length);
  check('pagination re-counts the filtered set',
        info() === 'Showing 1–20 of 20 (filtered from 60)', info());
  check('one page of matches hides the pager',
        doc.getElementById('usersPagination').style.display === 'none');
  check('matching is case-insensitive', (() => {
    type(1, 'ACME'); return true; })());
  await wait(200);
  check('...and finds the same rows', matching().length === 20, matching().length);

  // ── Negation ───────────────────────────────────────────────────────────
  type(1, '!acme');
  await wait(200);
  check('!text excludes', matching().length === 40
        && matching().every(r => !/acme/.test(r.cells[1].textContent)), matching().length);

  // ── Numeric comparison ─────────────────────────────────────────────────
  type(1, '');
  type(2, '>4');
  await wait(200);
  const expectGt4 = rows.filter(r => Number(r.cells[2].textContent) > 4).length;
  check('>n compares numbers', matching().length === expectGt4, matching().length + ' vs ' + expectGt4);
  type(2, '>=6');
  await wait(200);
  check('>=n compares numbers',
        matching().every(r => Number(r.cells[2].textContent) >= 6) && matching().length > 0);
  type(2, '=0');
  await wait(200);
  check('=n is numeric equality',
        matching().length === rows.filter(r => r.cells[2].textContent === '0').length);
  type(2, '');
  type(3, '>1000');
  await wait(200);
  check('numbers in "$1,234.50" cells parse past the $ and comma',
        matching().length > 0 && matching().every(r =>
          Number(r.cells[3].textContent.replace(/[$,]/g, '')) > 1000), matching().length);
  check('...and a smaller spend is excluded',
        matching().every(r => r.cells[3].textContent !== '$0.00'));

  // ── AND across columns ─────────────────────────────────────────────────
  type(3, '');
  type(1, 'globex');
  type(6, 'active');
  await wait(200);
  check('filters combine with AND', matching().length > 0 && matching().every(r =>
        /globex/.test(r.cells[1].textContent) && /Active/.test(r.cells[6].textContent)),
        matching().length);
  type(6, 'deactivated');
  await wait(200);
  const both = rows.filter(r => /globex/.test(r.cells[1].textContent)
                               && /Deactivated/.test(r.cells[6].textContent)).length;
  check('narrowing further narrows the set', matching().length === both, matching().length);

  // ── The regression that matters: page turns vs filtered rows ───────────
  type(1, ''); type(6, '');
  await wait(200);
  window.changeUsersPage(1);               // go to page 2 of all 60
  check('can move to page two', info() === 'Showing 26–50 of 60', info());

  // The new result set must itself span two pages, or the paginator's clamp to
  // the last page would land on page one anyway and hide a missing reset.
  // "!acme" leaves 40 rows: without the reset we would stay on page two.
  type(1, '!acme');
  await wait(200);
  check('a new filter resets to page one, even when page two still exists',
        info() === 'Showing 1–25 of 40 (filtered from 60)', info());

  type(1, 'acme');                         // and a 1-page result is fine too
  await wait(200);
  check('a single-page result shows page one', info().indexOf('Showing 1–') === 0, info());

  // The setup has to be non-trivial for this to mean anything: a filter that
  // matches more than one page AND excludes some rows. "!acme" keeps 40 rows
  // (two pages) and hides 20. A filter matching everything would pass this
  // check while testing nothing.
  type(1, '!acme');
  await wait(200);
  const excluded = rows.filter(r => /acme/.test(r.cells[1].textContent));
  const visibleToLayout = r => window.getComputedStyle(r).display !== 'none';
  check('the setup spans two pages and excludes rows',
        matching().length === 40 && excluded.length === 20,
        matching().length + ' matching, ' + excluded.length + ' excluded');
  check('excluded rows really are hidden from layout, via the class',
        excluded.length > 0 && excluded.every(r => !visibleToLayout(r)));
  check('page one shows a full page of matches', shown().length === 25, shown().length);

  window.changeUsersPage(1);
  check('page two shows the remaining 15', shown().length === 15
        && info() === 'Showing 26–40 of 40 (filtered from 60)', info());
  check('turning forward never reveals a filtered-out row',
        excluded.every(r => !visibleToLayout(r)),
        excluded.filter(visibleToLayout).length + ' leaked');

  window.changeUsersPage(-1);
  check('turning back never reveals one either',
        excluded.every(r => !visibleToLayout(r)) && shown().length === 25,
        excluded.filter(visibleToLayout).length + ' leaked');

  window.changeUsersPage(5);               // past the end
  check('paging past the end clamps to the last page of matches',
        info() === 'Showing 26–40 of 40 (filtered from 60)', info());
  check('...still without leaking', excluded.every(r => !visibleToLayout(r)));

  // NOT tested here, deliberately: that `.atf-hidden`'s !important beats
  // another script explicitly setting `style.display = 'table-row'`. Per the
  // CSS cascade it does, in every browser — but jsdom's partial cascade gets it
  // wrong and reports 'table-row'. A check here would fail with or without
  // !important, so it would discriminate nothing. The declaration is pinned
  // statically on the Python side instead. (Note too that `style.display = ''`
  // is not a valid stand-in: assigning '' removes the inline property rather
  // than setting one, so nothing competes with the class at all.)

  // Every row on screen must satisfy the filter, on every page.
  window.changeUsersPage(-5);
  const onScreen = rows.filter(visibleToLayout);
  check('every row visible to layout satisfies the filter',
        onScreen.length === 25 && onScreen.every(r => !/acme/.test(r.cells[1].textContent)),
        onScreen.length);

  // ── Empty state, status line, Escape, Clear ────────────────────────────
  type(1, 'zzz-no-such-user');
  await wait(200);
  const empty = table.querySelector('tr.atf-empty');
  check('no match says so instead of looking broken',
        !!empty && /No rows match/.test(empty.textContent));
  check('the empty-state row spans every column',
        empty && empty.cells[0].getAttribute('colspan') === '8');
  const status = table.previousElementSibling;
  check('a status line reports the count',
        status && status.classList.contains('atf-status')
        && /Showing 0 of 60 rows matching 1 filter/.test(status.textContent),
        status && status.textContent);
  check('the active box is highlighted', inputFor(1).classList.contains('atf-active'));
  check('pagination agrees there is nothing to show', info().indexOf('Showing 0–0 of 0') === 0, info());

  inputFor(1).dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape' }));
  check('Escape clears that box and restores the rows',
        inputFor(1).value === '' && matching().length === 60);
  check('the empty-state row is removed again', !table.querySelector('tr.atf-empty'));

  type(1, 'acme'); type(2, '>1');
  await wait(200);
  status.querySelector('.atf-clear').click();
  check('Clear filters empties every box',
        [0,1,2,3,4,5,6].every(c => inputFor(c).value === ''));
  check('...and restores every row', matching().length === 60);
  check('...and the status line hides itself', !status.classList.contains('atf-show'));
  check('...and pagination is back to page one of everything',
        info() === 'Showing 1–25 of 60', info());

  // ── Tables injected later (several admin tables are fetch-rendered) ────
  const later = doc.getElementById('later');
  later.innerHTML = '<table class="admin-table" id="small"><thead><tr><th>A</th><th>B</th></tr></thead>' +
    '<tbody><tr><td>1</td><td>x</td></tr><tr><td>2</td><td>y</td></tr></tbody></table>';
  await wait(20);
  check('a small injected table is left alone',
        !doc.querySelector('#small tr.atf-row'));

  let big = '<table class="admin-table" id="big"><thead><tr><th>Discount</th><th>Reason</th><th></th></tr></thead><tbody>';
  for (let i = 0; i < 8; i++) big += '<tr><td>' + (i * 10) + '%</td><td>reason ' + i + '</td><td><button>revoke</button></td></tr>';
  big += '</tbody></table>';
  later.insertAdjacentHTML('beforeend', big);
  await wait(20);
  const bigTable = doc.getElementById('big');
  check('a large injected table gets filters automatically',
        !!bigTable.querySelector('tr.atf-row'));
  check('a blank header gets no filter box',
        bigTable.querySelectorAll('tr.atf-row .atf-input').length === 2);
  const bigInput = bigTable.querySelector('.atf-input[data-col="0"]');
  bigInput.value = '>=50';
  bigInput.dispatchEvent(new window.Event('input'));
  await wait(200);
  check('a percentage column compares numerically',
        Array.from(bigTable.tBodies[0].rows).filter(r => !r.classList.contains('atf-hidden')).length === 3);

  later.insertAdjacentHTML('beforeend',
    '<table class="admin-table" id="off" data-filter="off"><thead><tr><th>X</th></tr></thead><tbody>' +
    '<tr><td>1</td></tr>'.repeat(10) + '</tbody></table>');
  await wait(20);
  check('data-filter="off" opts a table out', !doc.querySelector('#off tr.atf-row'));

  // Attaching twice must not stack a second filter row.
  window.AdminTableFilter.attach(table);
  check('attaching is idempotent',
        table.querySelectorAll('thead tr.atf-row').length === 1);

  process.stdout.write(JSON.stringify(results));
})().catch(err => {
  process.stdout.write(JSON.stringify([{ label: 'harness crashed', ok: false, detail: String(err && err.stack || err) }]));
});
'''


def extract_pagination(index_html: str) -> str:
    """Lift the users pagination block out of admin/index.html verbatim."""
    # From the section header, so the USERS_PER_PAGE / state declarations that
    # sit above the functions come along with them.
    start = index_html.index('// Users table pagination')
    end_marker = "table.addEventListener('admin-table:filtered'"
    end = index_html.index(end_marker, start)
    end = index_html.index('})();', end) + len('})();')
    src = index_html[start:end]
    # Function declarations become window properties under jsdom's eval, which
    # is how the harness calls changeUsersPage() the way the Prev/Next buttons do.
    return src


def run_browser_checks():
    section('Browser behaviour (jsdom, real sources)')
    jsdom_dir = os.environ.get('ATF_JSDOM_DIR', '')
    node = shutil.which('node')
    if not node or not jsdom_dir or not os.path.isdir(
            os.path.join(jsdom_dir, 'node_modules', 'jsdom')):
        SKIP.append('browser checks')
        print('  SKIP browser checks -- set ATF_JSDOM_DIR to a dir with node_modules/jsdom')
        return

    index_html = open(os.path.join(HERE, 'app/templates/admin/index.html'),
                      encoding='utf8').read()
    work = tempfile.mkdtemp()
    try:
        pag = os.path.join(work, 'pagination.js')
        with open(pag, 'w', encoding='utf8') as f:
            f.write(extract_pagination(index_html))
        harness = os.path.join(jsdom_dir, '_atf_harness.js')
        with open(harness, 'w', encoding='utf8') as f:
            f.write(HARNESS)

        env = dict(os.environ)
        env['ATF_FILTER_JS'] = os.path.join(HERE, 'app/static/js/admin_table_filter.js')
        env['ATF_PAGINATION_JS'] = pag
        proc = subprocess.run([node, harness], cwd=jsdom_dir, env=env,
                              capture_output=True, text=True, encoding='utf8',
                              timeout=120)
        try:
            results = json.loads(proc.stdout)
        except ValueError:
            check('the harness produced results', False,
                  (proc.stdout or '')[:300] + ' | ' + (proc.stderr or '')[:500])
            return
        for r in results:
            check(r['label'], r['ok'], r['detail'])
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            os.remove(os.path.join(jsdom_dir, '_atf_harness.js'))
        except OSError:
            pass


def main():
    from app import create_app
    from app.extensions import bcrypt, db
    from utils.id_gen import generate_id
    from datetime import datetime, timedelta

    app = create_app('testing')
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      WTF_CSRF_ENABLED=False)

    with app.app_context():
        from app.models.platform_settings import PlatformSetting
        from app.models.user import User
        db.create_all()
        pw = bcrypt.generate_password_hash('pw12345678').decode()
        base = datetime.utcnow()
        admin = User(id=generate_id(), email='admin@t.local', full_name='Admin',
                     password_hash=pw, email_verified=True, is_admin=True,
                     onboarding_completed_at=base, created_at=base)
        db.session.add(admin)
        # 79 more, so there are 80 in total — past the old cap of 50.
        for i in range(79):
            db.session.add(User(
                id=generate_id(), email='u%02d@example.com' % i,
                full_name='User %02d' % i, password_hash=pw,
                email_verified=True, onboarding_completed_at=base,
                created_at=base - timedelta(days=i + 1)))
        db.session.commit()

    client = app.test_client()
    client.post('/api/auth/login', json={'email': 'admin@t.local', 'password': 'pw12345678'})

    # ── Server side ─────────────────────────────────────────────────────────
    section('The users table renders everyone the filter needs')
    html = client.get('/admin').get_data(as_text=True)
    rows = html.count('id="user-row-')
    check('all 80 users are rendered, not the 50 newest', rows == 80, rows)
    check('the oldest user is on the page, so a filter can find them',
          'u78@example.com' in html)
    check('the header reports the real count', 'Users (80)' in html,
          re.search(r'Users \([^)]*\)', html).group(0) if re.search(r'Users \([^)]*\)', html) else '')
    check('no truncation notice when everyone fits',
          'Filters search the' not in html)
    check('the table opts in to filtering', 'id="usersTable" data-filter="on"' in html)
    check('the filter script ships on the page', 'js/admin_table_filter.js' in html)

    with app.app_context():
        from app.models.platform_settings import PlatformSetting
        PlatformSetting.set('admin_users_render_limit', '60')
        db.session.commit()
    html = client.get('/admin').get_data(as_text=True)
    check('the cap is admin-configurable', html.count('id="user-row-') == 60,
          html.count('id="user-row-'))
    check('a partial list says it is partial',
          'Users (60 newest of 80)' in html and 'Filters search the 60 newest' in html)
    check('and names the setting that fixes it', 'admin_users_render_limit' in html)

    with app.app_context():
        from app.models.platform_settings import PlatformSetting
        PlatformSetting.set('admin_users_render_limit', '3')
        db.session.commit()
    html = client.get('/admin').get_data(as_text=True)
    check('the cap cannot be set below the old 50',
          html.count('id="user-row-') == 50, html.count('id="user-row-'))

    with app.app_context():
        from app.models.platform_settings import PlatformSetting
        PlatformSetting.set('admin_users_render_limit', 'lots')
        db.session.commit()
    html = client.get('/admin').get_data(as_text=True)
    check('a junk setting falls back to the default instead of erroring',
          html.count('id="user-row-') == 80, html.count('id="user-row-'))

    section('The script ships everywhere an admin-table lives')
    for page in ('admin/partners.html', 'admin/corporate.html', 'admin/index.html'):
        src = open(os.path.join(HERE, 'app/templates', page), encoding='utf8').read()
        check('%s includes the filter' % page,
              src.count("filename='js/admin_table_filter.js'") == 1)

    js = open(os.path.join(HERE, 'app/static/js/admin_table_filter.js'), encoding='utf8').read()
    # An assignment, not a mention: the module's own comments explain why it
    # leaves style.display to the paginator, and must be free to say so.
    check('the filter hides by class, never by inline style',
          'atf-hidden' in js and not re.search(r'\.style\.display\s*=', js))
    # Pinned statically because jsdom cannot evaluate it: per the CSS cascade
    # an !important stylesheet rule beats a normal inline declaration, which is
    # what keeps a filtered row hidden if any other script ever shows it with an
    # explicit `style.display = 'table-row'`. jsdom's partial cascade reports
    # the inline value instead, so a browser-behaviour check there would fail
    # with or without the !important and discriminate nothing.
    check('the hidden class is !important, so no inline style can override it',
          bool(re.search(r"\.atf-hidden\s*\{\s*display:\s*none\s*!important", js)))

    run_browser_checks()

    print('\n{} passed, {} failed{}'.format(
        len(PASS), len(FAIL), (', %d skipped' % len(SKIP)) if SKIP else ''))
    if FAIL:
        for label in FAIL:
            print('  FAILED: ' + label)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
