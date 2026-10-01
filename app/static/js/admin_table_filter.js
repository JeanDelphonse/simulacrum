/*
 * Column filters for every table.admin-table.
 *
 * Adds a second header row with one filter box per column. Filters combine with
 * AND, so "acme" under Email and ">0" under Simulations means both at once.
 *
 *   text      cell contains "text" (case-insensitive)
 *   !text     cell does not contain "text"
 *   >5  >=5   numeric comparison against the first number in the cell, with
 *   <5  <=5   "$", "," and "%" ignored — so ">100" works on a "$1,250.00" cell
 *   =5        numeric equality
 *
 * Works on tables that exist at load and on tables injected later (several
 * admin tables are built with innerHTML after a fetch), via a MutationObserver.
 *
 * Hiding is done with a class, never with the row's inline style. Paginated
 * tables already drive row.style.display themselves, and two scripts writing
 * the same property would undo each other on every page turn. Instead this
 * script owns `atf-hidden`, pagination owns inline display, and a filtered-out
 * row stays hidden whatever page it would have landed on. Each change fires
 * `admin-table:filtered` on the table so a paginator can re-count.
 *
 * Opt out with data-filter="off" on a <table> or a <th>. Small tables (fewer
 * than MIN_ROWS body rows) are left alone unless they opt in with
 * data-filter="on" — a filter row on a three-row table is clutter.
 */
(function () {
  'use strict';

  var MIN_ROWS = 6;
  var DEBOUNCE_MS = 120;
  var SKIP_HEADERS = /^(actions?|)$/i;

  // ── Styles, injected once so the module is a single file to include ────────
  // The inputs are transparent and inherit their text colour, so the same box
  // reads correctly on the light console tables and inside the dark modals.
  function injectStyles() {
    if (document.getElementById('atf-styles')) return;
    var css = [
      '.atf-hidden { display: none !important; }',
      '.admin-table thead tr.atf-row th { padding: .3rem .5rem .55rem; ',
      '  text-transform: none; letter-spacing: 0; font-weight: 400; }',
      '.atf-input { width: 100%; min-width: 4.5rem; box-sizing: border-box;',
      '  padding: .28rem .45rem; font: inherit; font-size: .75rem;',
      '  color: inherit; background: transparent;',
      '  border: 1px solid rgba(127,127,127,.35); border-radius: 5px; }',
      '.atf-input:focus { outline: 2px solid #0f7b72; outline-offset: 0;',
      '  border-color: transparent; }',
      '.atf-input.atf-active { border-color: #0f7b72;',
      '  background: rgba(15,123,114,.07); }',
      '.atf-status { display: none; align-items: center; gap: .6rem;',
      '  flex-wrap: wrap; font-size: .78rem; margin: .4rem 0 .6rem;',
      '  opacity: .8; }',
      '.atf-status.atf-show { display: flex; }',
      '.atf-clear { background: none; border: 1px solid rgba(127,127,127,.4);',
      '  color: inherit; border-radius: 5px; padding: .15rem .5rem;',
      '  font: inherit; font-size: .74rem; cursor: pointer; }',
      '.atf-clear:hover { border-color: #0f7b72; color: #0f7b72; }',
      '.atf-empty td { text-align: center; font-style: italic; opacity: .6;',
      '  padding: 1rem !important; }'
    ].join('\n');
    var style = document.createElement('style');
    style.id = 'atf-styles';
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ── Parsing a filter expression ────────────────────────────────────────────

  var NUMERIC_OP = /^(>=|<=|>|<|=)\s*(-?[\d,]*\.?\d+)$/;

  function firstNumber(text) {
    var cleaned = String(text).replace(/[$,%£€]/g, '');
    var match = cleaned.match(/-?\d+(?:\.\d+)?/);
    return match ? parseFloat(match[0]) : null;
  }

  /** Compile one filter box's text into a predicate over a cell's text. */
  function compile(raw) {
    var expr = String(raw || '').trim();
    if (!expr) return null;

    var numeric = expr.match(NUMERIC_OP);
    if (numeric) {
      var op = numeric[1];
      var target = parseFloat(numeric[2].replace(/,/g, ''));
      return function (cellText) {
        var value = firstNumber(cellText);
        if (value === null) return false;
        if (op === '>') return value > target;
        if (op === '>=') return value >= target;
        if (op === '<') return value < target;
        if (op === '<=') return value <= target;
        return value === target;
      };
    }

    if (expr.charAt(0) === '!' && expr.length > 1) {
      var excluded = expr.slice(1).toLowerCase();
      return function (cellText) { return cellText.indexOf(excluded) === -1; };
    }

    var needle = expr.toLowerCase();
    return function (cellText) { return cellText.indexOf(needle) !== -1; };
  }

  function cellText(cell) {
    return cell ? (cell.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase() : '';
  }

  // ── Attaching to one table ─────────────────────────────────────────────────

  function headerCells(table) {
    var head = table.tHead;
    if (!head || !head.rows.length) return [];
    return Array.prototype.slice.call(head.rows[0].cells);
  }

  function bodyRows(table) {
    var rows = [];
    for (var b = 0; b < table.tBodies.length; b++) {
      var tbody = table.tBodies[b];
      for (var r = 0; r < tbody.rows.length; r++) {
        if (!tbody.rows[r].classList.contains('atf-empty')) rows.push(tbody.rows[r]);
      }
    }
    return rows;
  }

  function shouldAttach(table) {
    if (table.getAttribute('data-filter') === 'off') return false;
    if (table.__atf) return false;
    var headers = headerCells(table);
    if (!headers.length) return false;
    if (table.getAttribute('data-filter') === 'on') return true;
    return bodyRows(table).length >= MIN_ROWS;
  }

  function attach(table) {
    if (!shouldAttach(table)) return;

    var headers = headerCells(table);
    var state = { filters: [], timer: null };
    table.__atf = state;

    var filterRow = document.createElement('tr');
    filterRow.className = 'atf-row';

    headers.forEach(function (th, index) {
      var label = (th.textContent || '').trim();
      var cell = document.createElement('th');
      var span = parseInt(th.getAttribute('colspan') || '1', 10);
      if (span > 1) cell.setAttribute('colspan', span);

      var skip = th.getAttribute('data-filter') === 'off' || SKIP_HEADERS.test(label);
      if (!skip) {
        var input = document.createElement('input');
        input.type = 'search';
        input.className = 'atf-input';
        input.placeholder = 'Filter…';
        input.setAttribute('aria-label', 'Filter ' + (label || 'column ' + (index + 1)));
        input.title = 'Contains text. !text excludes. >5, >=5, <5, <=5, =5 compare numbers.';
        input.setAttribute('data-col', String(index));
        input.addEventListener('input', function () { schedule(table); });
        input.addEventListener('keydown', function (ev) {
          if (ev.key === 'Escape' && input.value) {
            ev.preventDefault();
            input.value = '';
            apply(table);
          }
        });
        cell.appendChild(input);
      }
      filterRow.appendChild(cell);
    });

    table.tHead.appendChild(filterRow);

    // Status line: what is showing, and a way back to everything.
    var status = document.createElement('div');
    status.className = 'atf-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    var text = document.createElement('span');
    var clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'atf-clear';
    clear.textContent = 'Clear filters';
    clear.addEventListener('click', function () { clearAll(table); });
    status.appendChild(text);
    status.appendChild(clear);
    table.parentNode.insertBefore(status, table);

    state.status = status;
    state.statusText = text;
  }

  function schedule(table) {
    var state = table.__atf;
    clearTimeout(state.timer);
    state.timer = setTimeout(function () { apply(table); }, DEBOUNCE_MS);
  }

  function apply(table) {
    var state = table.__atf;
    if (!state) return;

    var inputs = table.querySelectorAll('tr.atf-row .atf-input');
    var active = [];
    Array.prototype.forEach.call(inputs, function (input) {
      var predicate = compile(input.value);
      input.classList.toggle('atf-active', !!predicate);
      if (predicate) {
        active.push({ col: parseInt(input.getAttribute('data-col'), 10), test: predicate });
      }
    });

    var rows = bodyRows(table);
    var visible = 0;
    rows.forEach(function (row) {
      var keep = true;
      for (var i = 0; i < active.length && keep; i++) {
        keep = active[i].test(cellText(row.cells[active[i].col]));
      }
      row.classList.toggle('atf-hidden', !keep);
      if (keep) visible++;
    });

    renderEmpty(table, active.length > 0 && visible === 0, headerCells(table).length);

    if (active.length) {
      state.statusText.textContent = 'Showing ' + visible + ' of ' + rows.length
        + ' row' + (rows.length === 1 ? '' : 's') + ' matching '
        + active.length + ' filter' + (active.length === 1 ? '' : 's') + '.';
      state.status.classList.add('atf-show');
    } else {
      state.status.classList.remove('atf-show');
    }

    var event;
    try {
      event = new CustomEvent('admin-table:filtered', {
        detail: { visible: visible, total: rows.length, active: active.length }
      });
    } catch (e) {
      event = document.createEvent('CustomEvent');
      event.initCustomEvent('admin-table:filtered', false, false,
        { visible: visible, total: rows.length, active: active.length });
    }
    table.dispatchEvent(event);
  }

  // A filtered-to-nothing table should say so rather than look broken.
  function renderEmpty(table, show, columnCount) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var existing = tbody.querySelector('tr.atf-empty');
    if (show && !existing) {
      var row = document.createElement('tr');
      row.className = 'atf-empty';
      var cell = document.createElement('td');
      cell.setAttribute('colspan', String(columnCount || 1));
      cell.textContent = 'No rows match these filters.';
      row.appendChild(cell);
      tbody.appendChild(row);
    } else if (!show && existing) {
      existing.parentNode.removeChild(existing);
    }
  }

  function clearAll(table) {
    var inputs = table.querySelectorAll('tr.atf-row .atf-input');
    Array.prototype.forEach.call(inputs, function (input) { input.value = ''; });
    apply(table);
  }

  // ── Discovery ──────────────────────────────────────────────────────────────

  function scan(root) {
    var tables = (root || document).querySelectorAll
      ? (root || document).querySelectorAll('table.admin-table')
      : [];
    Array.prototype.forEach.call(tables, attach);
    if (root && root.matches && root.matches('table.admin-table')) attach(root);
  }

  function observe() {
    if (!window.MutationObserver) return;
    var observer = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var added = mutations[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          if (added[j].nodeType === 1) scan(added[j]);
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function init() {
    injectStyles();
    scan(document);
    observe();
  }

  // Public surface: attach on demand, read the current matching rows, and reset.
  window.AdminTableFilter = {
    attach: function (table) { injectStyles(); attach(table); },
    clear: clearAll,
    isHidden: function (row) { return row.classList.contains('atf-hidden'); },
    compile: compile
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
