/*
 * PRD-SIM-WORLD-001 — the Wealth Engine World.
 *
 * Renders a simulation's agents as an interactive five-layer world inside the
 * network tab. Clicking an object opens that agent's Layer Actions form, and it
 * saves through the same endpoints the list view uses — there is no second
 * store here, only a second way to look at the one that exists.
 *
 * SVG + requestAnimationFrame, no dependencies, no images.
 */
(function () {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';

  // Deterministic layout constants (PRD §5.1 — no manual placement in v1).
  var NODE_W = 132, NODE_H = 52, GAP_X = 18;
  var BAND_H = 96, BAND_TOP = 40, CANVAS_MIN_W = 1040, GUTTER_L = 118, GUTTER_R = 28;

  var state = {
    simId: null,
    root: '',
    model: null,
    nodes: {},          // action_type -> node
    order: [],          // action_types in stable render order
    edges: [],
    scope: null,        // configured | selected | all — from the admin default on load
    scopeFellBack: false,  // configured was asked for but nothing is configured yet
    layerFocus: null,   // null = every layer
    autoFocused: false, // layer focus has been auto-applied once
    selected: null,     // action_type of the open drawer
    zoneKey: null,
    zonePacks: {},
    income: 0,
    wealth: 0,
    cycle: 0,
    autoTimer: null,
    reduced: false,
    layout: {},         // action_type -> {x, y}
    bands: {},          // layer -> y
    viewW: CANVAS_MIN_W,
    viewH: 640,
    loading: false
  };

  // ── Small DOM helpers ──────────────────────────────────────────────────────

  function el(tag, attrs, parent) {
    var node = document.createElementNS(NS, tag);
    for (var k in attrs) { node.setAttribute(k, attrs[k]); }
    if (parent) parent.appendChild(node);
    return node;
  }

  function $(id) { return document.getElementById(id); }

  function money(value) {
    var n = Math.round(Number(value) || 0);
    return '$' + n.toLocaleString();
  }

  function esc(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function announce(message) {
    var live = $('wewAnnounce');
    if (live) live.textContent = message;
  }

  // ── Data ───────────────────────────────────────────────────────────────────

  function api(path, options) {
    return fetch(state.root + '/api/simulations/' + state.simId + path, options)
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok) throw new Error(body && body.error ? body.error : 'Request failed');
          return body;
        });
      });
  }

  function load() {
    state.loading = true;
    setStatus('Loading your engine…');
    return api('/world', {})
      .then(function (model) {
        state.loading = false;
        adopt(model);
        applyView();
        build();
        renderChrome();
        setStatus('');
      })
      .catch(function (err) {
        state.loading = false;
        setStatus('Could not load the world: ' + err.message
          + ' — the list view below still works.');
      });
  }

  function adopt(model) {
    state.model = model;
    state.nodes = {};
    state.order = [];
    model.nodes.forEach(function (n) {
      state.nodes[n.action_type] = n;
      state.order.push(n.action_type);
    });
    state.edges = model.edges;
    state.cycle = model.totals.cycle_count;
    state.income = model.totals.monthly_income;

    if (state.zoneKey === null) state.zoneKey = model.zone.key;
    state.zonePacks = {};
    (model.zone_options || []).forEach(function (z) { state.zonePacks[z.key] = z; });

    if (state.scope === null) state.scope = model.view.default_scope;
  }

  // Merge a write response back in without a full reload.
  function absorb(patch) {
    if (patch.nodes) {
      patch.nodes.forEach(function (n) { state.nodes[n.action_type] = n; });
    }
    if (patch.edges) state.edges = patch.edges;
    if (patch.totals) state.income = patch.totals.monthly_income;
    if (patch.suggestion !== undefined) state.model.suggestion = patch.suggestion;
  }

  // ── Which agents are on the canvas ────────────────────────────────────────

  function visibleTypes() {
    var out = [];
    for (var i = 0; i < state.order.length; i++) {
      var n = state.nodes[state.order[i]];
      // 'configured' is the default: the engine you have actually set up. A
      // configured agent you switched off still shows, dimmed — that it is off
      // is the thing worth seeing.
      if (state.scope === 'configured' && !n.configured) continue;
      if (state.scope === 'selected' && !n.active) continue;
      if (state.layerFocus && n.layer !== state.layerFocus) continue;
      out.push(n.action_type);
    }
    return out;
  }

  function configuredCount() {
    var total = 0;
    for (var at in state.nodes) { if (state.nodes[at].configured) total++; }
    return total;
  }

  // Above the configured node count the canvas stops being legible, so it opens
  // focused on the foundation layer instead (PRD §11, scale to 49).
  function applyView() {
    // Nothing configured yet means the configured scope would render an empty
    // world — and an empty world is one you cannot configure by clicking, which
    // is the whole point. Fall back to the selection and say so in the hint bar.
    state.scopeFellBack = false;
    if (state.scope === 'configured' && configuredCount() === 0) {
      state.scope = 'selected';
      state.scopeFellBack = true;
    }
    if (state.autoFocused || state.layerFocus !== null) return;
    state.autoFocused = true;
    var threshold = (state.model.view && state.model.view.layer_focus_threshold) || 26;
    if (visibleTypes().length > threshold) state.layerFocus = 1;
  }

  // ── Deterministic layout ──────────────────────────────────────────────────

  function computeLayout() {
    var visible = visibleTypes();
    var byLayer = { 1: [], 2: [], 3: [], 4: [], 5: [] };
    visible.forEach(function (at) { byLayer[state.nodes[at].layer].push(at); });

    var layers = state.layerFocus ? [state.layerFocus] : [5, 4, 3, 2, 1];
    var widest = 1;
    layers.forEach(function (l) { widest = Math.max(widest, byLayer[l].length); });

    state.viewW = Math.max(
      CANVAS_MIN_W,
      GUTTER_L + GUTTER_R + widest * NODE_W + (widest - 1) * GAP_X
    );
    state.viewH = BAND_TOP + layers.length * BAND_H + 78;  // room for the orchestrator

    state.bands = {};
    state.layout = {};
    layers.forEach(function (layer, row) {
      var y = BAND_TOP + row * BAND_H + BAND_H / 2;
      state.bands[layer] = y;
      var members = byLayer[layer];
      var span = state.viewW - GUTTER_L - GUTTER_R;
      members.forEach(function (at, i) {
        // Evenly spaced across the band, centred — stable across loads because
        // the order comes from the registry, never from the browser.
        var step = span / members.length;
        state.layout[at] = { x: GUTTER_L + step * (i + 0.5), y: y };
      });
    });
    return { byLayer: byLayer, layers: layers };
  }

  function edgePath(a, b) {
    var A = state.layout[a], B = state.layout[b];
    var mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2 - 38;
    return 'M ' + A.x + ' ' + A.y + ' Q ' + mx + ' ' + my + ' ' + B.x + ' ' + B.y;
  }

  function edgeId(a, b) { return 'wewE_' + a + '__' + b; }

  function visibleEdges() {
    return state.edges.filter(function (e) {
      return state.layout[e.source] && state.layout[e.target];
    });
  }

  // ── Build the canvas ──────────────────────────────────────────────────────

  function build() {
    var svg = $('wewCanvas');
    if (!svg) return;
    var plan = computeLayout();
    svg.setAttribute('viewBox', '0 0 ' + state.viewW + ' ' + state.viewH);
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var layerMeta = {};
    state.model.layers.forEach(function (l) { layerMeta[l.layer] = l; });

    // Layer bands
    plan.layers.forEach(function (layer) {
      var meta = layerMeta[layer];
      var y = state.bands[layer];
      el('rect', {
        x: GUTTER_L - 8, y: y - BAND_H / 2 + 4, width: state.viewW - GUTTER_L - 12,
        height: BAND_H - 8, rx: 14, fill: meta.color, 'fill-opacity': 0.07, class: 'wew-band'
      }, svg);
      el('rect', {
        x: GUTTER_L - 8, y: y - BAND_H / 2 + 4, width: 6, height: BAND_H - 8,
        rx: 3, fill: meta.color
      }, svg);
      var tag = el('text', {
        x: 20, y: y - 6, fill: meta.color, 'font-size': 15, class: 'wew-band-tag'
      }, svg);
      tag.textContent = 'L' + layer;
      var name = el('text', { x: 20, y: y + 11, fill: '#8794a8', 'font-size': 9.5 }, svg);
      name.textContent = meta.name;
      if (!meta.unlocked) {
        var lock = el('text', { x: 20, y: y + 25, fill: '#6B7B8D', 'font-size': 8.5 }, svg);
        lock.textContent = '🔒 ' + (meta.lock_condition || 'Locked');
      }
    });

    var edgeGroup = el('g', { id: 'wewEdges' }, svg);
    visibleEdges().forEach(function (e) {
      el('path', {
        d: edgePath(e.source, e.target), class: 'wew-edge', id: edgeId(e.source, e.target),
        fill: 'none'
      }, edgeGroup);
    });

    el('g', { id: 'wewTokens' }, svg);
    el('g', { id: 'wewFloats' }, svg);

    var nodeGroup = el('g', { id: 'wewNodes' }, svg);
    visibleTypes().forEach(function (at) { drawNode(nodeGroup, at); });

    buildOrchestrator(svg);
    refresh();
  }

  function drawNode(parent, actionType) {
    var n = state.nodes[actionType];
    var pos = state.layout[actionType];
    var meta = {};
    state.model.layers.forEach(function (l) { if (l.layer === n.layer) meta = l; });

    var g = el('g', {
      class: 'wew-node', id: 'wewN_' + actionType,
      transform: 'translate(' + pos.x + ',' + pos.y + ')',
      tabindex: '0', role: 'button', 'aria-label': ariaLabel(n)
    }, parent);

    g.addEventListener('click', function () { selectNode(actionType); });
    g.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') {
        ev.preventDefault();
        selectNode(actionType);
      }
    });

    el('rect', {
      class: 'wew-ring', x: -NODE_W / 2 - 5, y: -NODE_H / 2 - 5,
      width: NODE_W + 10, height: NODE_H + 10, rx: 14, fill: 'none'
    }, g);
    el('rect', {
      class: 'wew-box', x: -NODE_W / 2, y: -NODE_H / 2, width: NODE_W, height: NODE_H,
      rx: 11, 'stroke-width': 2, stroke: meta.color || '#0F7B72', fill: '#0f1f42'
    }, g);

    var icon = el('text', {
      class: 'wew-ic', x: -NODE_W / 2 + 19, y: 5, 'font-size': 17, 'text-anchor': 'middle'
    }, g);
    icon.textContent = n.icon || '◆';

    var label = el('text', { class: 'wew-lbl', x: -NODE_W / 2 + 36, y: -3 }, g);
    label.textContent = truncate(displayLabel(n), 17);

    var value = el('text', { class: 'wew-val', x: -NODE_W / 2 + 36, y: 13 }, g);
    value.textContent = valueCaption(n);

    // State is carried by a glyph as well as colour (PRD §11).
    var mark = el('text', { class: 'wew-mark', x: NODE_W / 2 - 11, y: -NODE_H / 2 + 15,
      'text-anchor': 'middle', 'font-size': 10 }, g);
    mark.textContent = stateGlyph(n);

    var title = el('title', {}, g);
    title.textContent = ariaLabel(n);
  }

  function truncate(text, max) {
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  }

  function displayLabel(n) {
    var pack = state.zonePacks[state.zoneKey];
    if (pack && pack.labels && pack.labels[n.action_type]) return pack.labels[n.action_type];
    return n.base_label || n.label;
  }

  function valueCaption(n) {
    if (n.role === 'flow' && n.value > 0) return money(n.value) + '/cy';
    if (n.role === 'multiplier') return '×' + (n.multiplier || 1) + ' income';
    if (n.role === 'router') return 'routes to wealth';
    if (n.role === 'compound') return 'compounds wealth';
    return 'enabler';
  }

  function stateGlyph(n) {
    if (n.layer_locked) return '🔒';
    if (!n.active) return '○';
    if (n.configured) return '●';
    return '◐';   // active but not yet configured
  }

  function ariaLabel(n) {
    var bits = [displayLabel(n), 'Layer ' + n.layer, n.active ? 'active' : 'inactive'];
    if (n.layer_locked) bits.push('layer locked');
    else bits.push(n.configured ? 'configured' : 'not configured');
    if (n.role === 'flow' && n.value > 0) bits.push(money(n.value) + ' per cycle');
    else if (n.role === 'multiplier') bits.push('income multiplier ' + n.multiplier);
    else if (n.role === 'router') bits.push('routes income to wealth');
    else if (n.role === 'compound') bits.push('compounds wealth');
    return bits.join(', ');
  }

  function buildOrchestrator(svg) {
    var g = el('g', { id: 'wewOrc', transform: 'translate(150,' + (state.viewH - 40) + ')' }, svg);
    var body = el('g', { class: 'wew-bob' }, g);
    el('rect', { x: -13, y: -2, width: 26, height: 22, rx: 6, fill: '#0f1f42',
      stroke: '#C9952A', 'stroke-width': 1.4 }, body);
    el('rect', { x: -7, y: 5, width: 3, height: 9, rx: 1.5, fill: '#0F7B72' }, body);
    el('rect', { x: -2.5, y: 2, width: 3, height: 12, rx: 1.5, fill: '#13A89E' }, body);
    el('rect', { x: 2, y: 6, width: 3, height: 8, rx: 1.5, fill: '#C9952A' }, body);
    el('rect', { x: -11, y: -20, width: 22, height: 16, rx: 5, fill: '#122347',
      stroke: '#C9952A', 'stroke-width': 1.4 }, body);
    el('circle', { cx: -4, cy: -12, r: 2.1, fill: '#13A89E' }, body);
    el('circle', { cx: 4, cy: -12, r: 2.1, fill: '#13A89E' }, body);
    el('line', { x1: 0, y1: -20, x2: 0, y2: -25, stroke: '#C9952A', 'stroke-width': 1.4 }, body);
    el('circle', { cx: 0, cy: -27, r: 2, fill: '#E4B855' }, body);
    el('text', { id: 'wewOrcLabel', x: 0, y: 34, 'text-anchor': 'middle', 'font-size': 9,
      fill: '#E4B855', 'font-weight': '700' }, g);
  }

  // ── Render pass ───────────────────────────────────────────────────────────

  function refresh() {
    visibleTypes().forEach(function (at) {
      var n = state.nodes[at];
      var g = $('wewN_' + at);
      if (!g) return;
      g.classList.toggle('on', !!n.active);
      g.classList.toggle('dim', !n.active);
      g.classList.toggle('sel', state.selected === at);
      g.classList.toggle('locked', !!n.layer_locked);
      g.setAttribute('aria-label', ariaLabel(n));
      var lbl = g.querySelector('.wew-lbl');
      if (lbl) lbl.textContent = truncate(displayLabel(n), 17);
      var val = g.querySelector('.wew-val');
      if (val) val.textContent = valueCaption(n);
      var mark = g.querySelector('.wew-mark');
      if (mark) mark.textContent = stateGlyph(n);
    });

    visibleEdges().forEach(function (e) {
      var path = $(edgeId(e.source, e.target));
      if (!path) return;
      var live = state.nodes[e.source].active && state.nodes[e.target].active;
      path.classList.toggle('live', live);
    });

    computeIncome();
    setText('wewIncome', money(state.income));
    setText('wewCycle', String(state.cycle));
    renderSuggestion();
  }

  function setText(id, text) { var node = $(id); if (node) node.textContent = text; }

  function computeIncome() {
    var sum = 0, root = null;
    for (var at in state.nodes) {
      var n = state.nodes[at];
      if (!n.active) continue;
      if (n.role === 'flow') sum += n.value;
      if (n.role === 'multiplier') root = n;
    }
    if (root && root.multiplier) sum *= root.multiplier;
    state.income = sum;
  }

  function renderSuggestion() {
    document.querySelectorAll('.wew-node').forEach(function (n) { n.classList.remove('suggest'); });
    var orc = $('wewOrc'), label = $('wewOrcLabel');
    var s = state.model.suggestion;

    if (!s) {
      if (orc) orc.setAttribute('transform', 'translate(150,' + (state.viewH - 40) + ')');
      if (label) label.textContent = 'all live';
      renderHint();
      return;
    }

    var target = $('wewN_' + s.action_type);
    if (target && state.layout[s.action_type]) {
      var pos = state.layout[s.action_type];
      target.classList.add('suggest');
      if (orc) orc.setAttribute('transform',
        'translate(' + (pos.x - NODE_W / 2 - 34) + ',' + pos.y + ')');
      if (label) label.textContent = 'activate?';
    } else if (orc) {
      // Suggested agent is off-canvas in this scope or layer focus.
      orc.setAttribute('transform', 'translate(150,' + (state.viewH - 40) + ')');
      if (label) label.textContent = 'look here';
    }
    renderHint();
  }

  // The hint bar carries two things: why the canvas looks the way it does, and
  // what the orchestrator wants next. The first only appears when it applies.
  function renderHint() {
    var hint = $('wewHint');
    if (!hint) return;
    var s = state.model.suggestion;
    var parts = [];

    var shown = visibleTypes().length;
    var focusNote = state.layerFocus ? ' on layer ' + state.layerFocus : '';

    if (state.scopeFellBack) {
      parts.push('No agent is configured yet, so this is your <b>active agents</b> — '
        + 'open one and fill in its parameters, and the world narrows to what you have set up.');
    } else if (state.scope === 'selected') {
      parts.push('Showing the <b>' + shown + ' active agent' + (shown === 1 ? '' : 's')
        + '</b>' + focusNote + ' for this simulation, from '
        + '<a class="wew-link" href="' + state.root + '/simulations/' + state.simId
        + '/setup">Manage Agents</a>.');
    } else if (state.scope === 'configured') {
      parts.push('Showing the <b>' + shown + ' configured agent' + (shown === 1 ? '' : 's')
        + '</b>' + focusNote + ' — the ones whose parameters are filled in.');
    } else {
      parts.push('Showing <b>all ' + shown + ' agents</b>' + focusNote
        + '. Dim ones are not active for this simulation.');
    }

    if (s) {
      parts.push('Orchestrator suggests <b>' + esc(s.label) + '</b> (Layer ' + s.layer
        + ') — ' + esc(s.reason) + '. '
        + '<button type="button" class="wew-link" id="wewGoSuggest">Open it</button>');
    } else if (!parts.length) {
      parts.push('Every agent in view is live. Press <b>Run cycle</b> to watch the whole '
        + 'engine compound.');
    }

    hint.innerHTML = parts.join(' ');

    var go = $('wewGoSuggest');
    if (go && s) go.addEventListener('click', function () {
      // The suggestion is by definition an agent you have not switched on, so it
      // is usually outside the configured scope — widen just far enough.
      var node = state.nodes[s.action_type];
      if (state.scope === 'configured' && !node.configured) {
        state.scope = node.active ? 'selected' : 'all';
      } else if (state.scope === 'selected' && !node.active) {
        state.scope = 'all';
      }
      if (state.layerFocus && state.layerFocus !== s.layer) state.layerFocus = s.layer;
      state.scopeFellBack = false;
      build();
      renderChrome();
      selectNode(s.action_type);
    });
  }

  // ── Config drawer — the merge (PRD §5.2) ──────────────────────────────────

  function selectNode(actionType) {
    state.selected = actionType;
    refresh();
    openDrawer(actionType);
  }

  function openDrawer(actionType) {
    var n = state.nodes[actionType];
    var drawer = $('wewDrawer');
    if (!n || !drawer) return;

    var layerMeta = {};
    state.model.layers.forEach(function (l) { if (l.layer === n.layer) layerMeta = l; });

    var html = '';
    html += '<div class="wew-dr-head">';
    html += '<div><div class="wew-dr-layer">Layer ' + n.layer + ' · ' + esc(layerMeta.name || '')
      + '</div><h3>' + esc(displayLabel(n)) + '</h3></div>';
    html += '<button type="button" class="wew-dr-close" id="wewDrawerClose" '
      + 'aria-label="Close configuration">✕</button>';
    html += '</div>';

    if (n.description) html += '<p class="wew-dr-desc">' + esc(n.description) + '</p>';

    html += '<div class="wew-kv"><span>Status</span><b class="' + (n.active ? 'on' : 'off') + '">'
      + (n.active ? 'Active' : 'Inactive')
      + (n.configured ? ' · configured' : ' · not configured') + '</b></div>';
    html += '<div class="wew-kv"><span>Per-cycle value</span><b>' + esc(valueDetail(n)) + '</b></div>';

    if (n.value_source === 'calibration') {
      html += '<div class="wew-src">' + esc(n.value_source_label || 'Calibrated')
        + (n.value_tier ? ' · ' + esc(n.value_tier) + ' confidence' : '')
        + (n.value_low && n.value_high
          ? ' · ' + money(n.value_low) + '–' + money(n.value_high) : '')
        + '</div>';
    } else if (n.value_source === 'income_streams') {
      html += '<div class="wew-src">' + esc(n.value_source_label) + '</div>';
    }
    if (n.role === 'multiplier' && n.multiplier_basis) {
      html += '<div class="wew-src">' + esc(n.multiplier_basis) + '</div>';
    } else if (n.role === 'multiplier' && n.multiplier_measured === false) {
      html += '<div class="wew-src">Default multiplier — run the rate card to measure '
        + 'your own rate against the market.</div>';
    }

    if (n.layer_locked) {
      html += '<div class="wew-lock">🔒 ' + esc(n.lock_condition || 'This layer is not open yet')
        + '</div>';
    }
    if (n.prerequisites && n.prerequisites.length && !n.prerequisites_active) {
      var missing = n.prerequisites.filter(function (p) {
        return state.nodes[p] && !state.nodes[p].active;
      }).map(function (p) { return displayLabel(state.nodes[p]); });
      if (missing.length) {
        html += '<div class="wew-lock">Depends on ' + esc(missing.join(', ')) + '</div>';
      }
    }

    // The Layer Actions form — same fields, same validation, same write path.
    html += '<form class="wew-form" id="wewForm">';
    if (!n.params.length) {
      html += '<p class="wew-dr-desc">This agent takes no parameters.</p>';
    }
    n.params.forEach(function (p) {
      var fid = 'wewF_' + p.key;
      html += '<label class="wew-field" for="' + fid + '">'
        + '<span>' + esc(p.label) + (p.required ? ' <i>required</i>' : '') + '</span>';
      if (p.type === 'textarea') {
        html += '<textarea id="' + fid + '" name="' + esc(p.key) + '" rows="3"'
          + (p.required ? ' required' : '') + '>' + esc(p.value) + '</textarea>';
      } else if (p.type === 'select') {
        html += '<select id="' + fid + '" name="' + esc(p.key) + '"'
          + (p.required ? ' required' : '') + '><option value="">Choose…</option>';
        (p.options || []).forEach(function (opt) {
          html += '<option value="' + esc(opt) + '"'
            + (String(p.value) === String(opt) ? ' selected' : '') + '>' + esc(opt) + '</option>';
        });
        html += '</select>';
      } else {
        html += '<input id="' + fid + '" name="' + esc(p.key) + '" type="text" value="'
          + esc(p.value) + '"' + (p.required ? ' required' : '') + '>';
      }
      html += '</label>';
    });
    html += '<div class="wew-dr-actions">';
    if (n.params.length) {
      html += '<button type="submit" class="wew-btn gold" id="wewSave">Save parameters</button>';
    }
    var canToggle = !n.is_root && !n.is_triggered;
    if (canToggle) {
      html += '<button type="button" class="wew-btn ' + (n.active ? 'ghost' : 'gold')
        + '" id="wewToggle">' + (n.active ? 'Deactivate' : 'Activate') + '</button>';
    } else {
      html += '<span class="wew-pin">'
        + (n.is_root ? 'Root agent — always on' : 'Triggered by events') + '</span>';
    }
    html += '</div><div class="wew-dr-msg" id="wewDrawerMsg" role="status"></div>';
    html += '</form>';

    drawer.innerHTML = html;
    drawer.classList.add('show');

    $('wewDrawerClose').addEventListener('click', closeDrawer);
    $('wewForm').addEventListener('submit', function (ev) {
      ev.preventDefault();
      saveParams(actionType);
    });
    var toggle = $('wewToggle');
    if (toggle) toggle.addEventListener('click', function () { toggleActive(actionType); });

    var url = new URL(window.location);
    url.searchParams.set('agent', actionType);
    history.replaceState(null, '', url);
  }

  function valueDetail(n) {
    if (n.role === 'flow' && n.value > 0) return money(n.value) + ' / cycle';
    if (n.role === 'multiplier') return '×' + (n.multiplier || 1) + ' on engine income';
    if (n.role === 'router') return 'Routes income into wealth';
    if (n.role === 'compound') return 'Compounds accumulated wealth';
    return 'Enabler — makes other agents possible';
  }

  function closeDrawer() {
    var drawer = $('wewDrawer');
    if (drawer) { drawer.classList.remove('show'); drawer.innerHTML = ''; }
    state.selected = null;
    refresh();
    var url = new URL(window.location);
    url.searchParams.delete('agent');
    history.replaceState(null, '', url);
  }

  function drawerMsg(text, kind) {
    var box = $('wewDrawerMsg');
    if (!box) return;
    box.textContent = text;
    box.className = 'wew-dr-msg' + (kind ? ' ' + kind : '');
  }

  function saveParams(actionType) {
    var form = $('wewForm');
    var save = $('wewSave');
    if (!form) return;
    var inputs = {};
    Array.prototype.forEach.call(form.elements, function (field) {
      if (field.name) inputs[field.name] = field.value;
    });

    if (save) { save.disabled = true; save.textContent = 'Saving…'; }
    drawerMsg('');
    api('/agents/' + encodeURIComponent(actionType) + '/params', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ inputs: inputs })
    }).then(function (res) {
      var node = state.nodes[actionType];
      node.params.forEach(function (p) {
        if (res.saved && Object.prototype.hasOwnProperty.call(res.saved, p.key)) {
          p.value = res.saved[p.key] || '';
        }
      });
      var required = node.params.filter(function (p) { return p.required; });
      node.configured = required.length
        ? required.every(function (p) { return !!p.value; })
        : node.params.some(function (p) { return !!p.value; });
      refresh();
      drawerMsg('Saved. These values are what the orchestrator will run with.', 'ok');
      announce(displayLabel(node) + ' parameters saved.');
    }).catch(function (err) {
      drawerMsg('Could not save: ' + err.message, 'bad');
    }).then(function () {
      if (save) { save.disabled = false; save.textContent = 'Save parameters'; }
    });
  }

  function toggleActive(actionType) {
    var button = $('wewToggle');
    var node = state.nodes[actionType];
    var want = !node.active;
    if (button) { button.disabled = true; button.textContent = want ? 'Activating…' : 'Deactivating…'; }
    drawerMsg('');

    api('/agents/' + encodeURIComponent(actionType) + '/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active: want })
    }).then(function (res) {
      absorb(res);
      // Scope 'selected' hides whatever was just switched off, so the canvas has
      // to be rebuilt rather than merely repainted.
      build();
      openDrawer(actionType);
      announce(displayLabel(state.nodes[actionType]) + (want ? ' activated.' : ' deactivated.')
        + ' Monthly income now ' + money(state.income) + '.');
    }).catch(function (err) {
      if (button) { button.disabled = false; button.textContent = want ? 'Activate' : 'Deactivate'; }
      drawerMsg(err.message, 'bad');
    });
  }

  // ── Run the engine (PRD §5.3) ─────────────────────────────────────────────

  function animateToken(source, target, index, total) {
    var path = $(edgeId(source, target));
    if (!path) return;
    var tokens = $('wewTokens');
    if (!tokens) return;
    var length = path.getTotalLength();
    var duration = state.model.motion.token_duration_ms;
    var dot = el('circle', {
      r: 4.2, fill: '#E4B855', filter: 'drop-shadow(0 0 4px #C9952A)'
    }, tokens);
    var start = performance.now() + (index * duration) / (total * 2);

    function step(now) {
      if (now < start) { requestAnimationFrame(step); return; }
      var k = Math.min((now - start) / duration, 1);
      var point = path.getPointAtLength(k * length);
      dot.setAttribute('cx', point.x);
      dot.setAttribute('cy', point.y);
      if (k < 1) requestAnimationFrame(step);
      else { dot.remove(); pulse(target); }
    }
    requestAnimationFrame(step);
  }

  function pulse(actionType) {
    var g = $('wewN_' + actionType);
    if (!g) return;
    g.classList.remove('pulse');
    void g.getBoundingClientRect();
    g.classList.add('pulse');
    setTimeout(function () { g.classList.remove('pulse'); }, 520);
  }

  function floatText(actionType, text) {
    var pos = state.layout[actionType];
    var floats = $('wewFloats');
    if (!pos || !floats) return;
    var node = el('text', {
      class: 'wew-float', x: pos.x, y: pos.y - 32, 'text-anchor': 'middle'
    }, floats);
    node.textContent = text;
    if (state.reduced) { setTimeout(function () { node.remove(); }, 1400); return; }
    var t0 = performance.now();
    function step(now) {
      var k = Math.min((now - t0) / 1100, 1);
      node.setAttribute('y', pos.y - 32 - k * 26);
      node.setAttribute('opacity', 1 - k);
      if (k < 1) requestAnimationFrame(step); else node.remove();
    }
    requestAnimationFrame(step);
  }

  // Token count tracks the source agent's real value, so a bigger rate card
  // visibly moves more (PRD §5.3) — capped so the canvas stays readable.
  function tokensFor(node) {
    var motion = state.model.motion;
    if (node.role !== 'flow' || node.value <= 0) return 1;
    var extra = Math.floor(node.value / Math.max(1, motion.token_value_step));
    return Math.max(1, Math.min(motion.tokens_max, 1 + extra));
  }

  function runCycle() {
    if (!state.model) return;
    state.cycle += 1;
    setText('wewCycle', String(state.cycle));

    var live = visibleEdges().filter(function (e) {
      return state.nodes[e.source].active && state.nodes[e.target].active;
    });

    if (state.reduced) {
      // Reduced motion: the same information as a static highlight (PRD §11).
      live.forEach(function (e) {
        var path = $(edgeId(e.source, e.target));
        if (path) {
          path.classList.add('flowed');
          setTimeout(function () { path.classList.remove('flowed'); }, 900);
        }
      });
    } else {
      live.forEach(function (e) {
        var count = tokensFor(state.nodes[e.source]);
        for (var i = 0; i < count; i++) animateToken(e.source, e.target, i, count);
      });
    }

    computeIncome();
    var motion = state.model.motion;

    var router = null, compounder = null;
    for (var at in state.nodes) {
      var n = state.nodes[at];
      if (!n.active) continue;
      if (n.role === 'router' && !router) router = n;
      if (n.role === 'compound' && !compounder) compounder = n;
    }
    if (router) {
      var moved = state.income * (motion.allocation_pct / 100);
      state.wealth += moved;
      floatText(router.action_type, '+' + money(moved));
    }
    if (compounder) state.wealth *= (1 + motion.compound_pct / 100);

    setText('wewIncome', money(state.income));
    setTimeout(function () { setText('wewWealth', money(state.wealth)); },
      state.reduced ? 0 : 500);
    announce('Cycle ' + state.cycle + '. Monthly income ' + money(state.income)
      + ', wealth ' + money(state.wealth) + '.');
  }

  function toggleAuto() {
    var button = $('wewAuto');
    if (state.autoTimer) {
      clearInterval(state.autoTimer);
      state.autoTimer = null;
      if (button) { button.textContent = 'Auto ▶'; button.classList.remove('gold');
        button.classList.add('ghost'); button.setAttribute('aria-pressed', 'false'); }
      return;
    }
    runCycle();
    state.autoTimer = setInterval(runCycle, state.model.motion.auto_interval_ms);
    if (button) { button.textContent = 'Auto ⏸'; button.classList.remove('ghost');
      button.classList.add('gold'); button.setAttribute('aria-pressed', 'true'); }
  }

  // Reset returns to the simulation's saved state — it re-reads the server, it
  // does not invent a starting position.
  function resetWorld() {
    if (state.autoTimer) toggleAuto();
    state.wealth = 0;
    state.cycle = 0;
    state.selected = null;
    closeDrawer();
    setText('wewWealth', money(0));
    load().then(function () { announce('World reset to the saved simulation state.'); });
  }

  // ── Chrome (top bar controls) ─────────────────────────────────────────────

  function renderChrome() {
    var model = state.model;
    setText('wewZoneName', model.zone.expertise_zone || model.zone.name);
    setText('wewIncome', money(state.income));
    setText('wewWealth', money(state.wealth));
    setText('wewCycle', String(state.cycle));
    setText('wewCount', model.totals.active_count + ' active · '
      + configuredCount() + ' configured');

    var zoneSel = $('wewZoneSel');
    if (zoneSel && !zoneSel.dataset.ready) {
      (model.zone_options || []).forEach(function (z) {
        var option = document.createElement('option');
        option.value = z.key;
        option.textContent = z.name;
        zoneSel.appendChild(option);
      });
      zoneSel.value = state.zoneKey;
      zoneSel.addEventListener('change', function () { setZone(this.value); });
      zoneSel.dataset.ready = '1';
    }

    var layerSel = $('wewLayerSel');
    if (layerSel && !layerSel.dataset.ready) {
      layerSel.addEventListener('change', function () {
        setLayerFocus(this.value === 'all' ? null : parseInt(this.value, 10));
      });
      layerSel.dataset.ready = '1';
    }
    if (layerSel) layerSel.value = state.layerFocus ? String(state.layerFocus) : 'all';

    var scopeSel = $('wewScopeSel');
    if (scopeSel && !scopeSel.dataset.ready) {
      scopeSel.addEventListener('change', function () { setScope(this.value); });
      scopeSel.dataset.ready = '1';
    }
    if (scopeSel) {
      var sel = scopeSel.querySelector('option[value="selected"]');
      if (sel) sel.textContent = 'Active agents (' + model.totals.active_count + ')';
      var cfg = scopeSel.querySelector('option[value="configured"]');
      if (cfg) cfg.textContent = 'Configured only (' + configuredCount() + ')';
      var all = scopeSel.querySelector('option[value="all"]');
      if (all) all.textContent = 'All ' + model.totals.total_count;
      scopeSel.value = state.scope;
    }
    applyAccent();
  }

  function applyAccent() {
    var pack = state.zonePacks[state.zoneKey];
    var host = $('wewRoot');
    if (host && pack) host.style.setProperty('--wew-accent', pack.accent);
  }

  function setZone(key) {
    state.zoneKey = key;
    applyAccent();
    refresh();
    if (state.selected) openDrawer(state.selected);
    var pack = state.zonePacks[key];
    setText('wewZoneName', pack ? pack.name : '');
  }

  function setLayerFocus(layer) {
    state.layerFocus = layer;
    build();
    renderChrome();
    var url = new URL(window.location);
    if (layer) url.searchParams.set('layer', 'L' + layer);
    else url.searchParams.delete('layer');
    history.replaceState(null, '', url);
    announce(layer ? 'Focused on layer ' + layer : 'Showing every layer');
  }

  var SCOPE_WORDS = {
    selected: 'the active agents for this simulation',
    configured: 'configured agents only',
    all: 'all agents'
  };

  function setScope(scope) {
    state.scope = scope;
    state.scopeFellBack = false;   // an explicit choice is not a fallback
    build();
    renderChrome();
    announce('Showing ' + (SCOPE_WORDS[scope] || scope));
  }

  function setStatus(text) {
    var box = $('wewStatus');
    if (!box) return;
    box.textContent = text;
    box.style.display = text ? 'block' : 'none';
  }

  // ── Boot ──────────────────────────────────────────────────────────────────

  window.initWealthWorld = function (simId, root, opts) {
    opts = opts || {};
    if (state.simId) return;    // already initialised
    state.simId = simId;
    state.root = root || '';
    state.reduced = window.matchMedia
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    var params = new URLSearchParams(window.location.search);
    var layerParam = params.get('layer');
    if (layerParam) {
      var parsed = parseInt(String(layerParam).replace(/^L/i, ''), 10);
      if (parsed >= 1 && parsed <= 5) { state.layerFocus = parsed; state.autoFocused = true; }
    }
    if (opts.scope) state.scope = opts.scope;

    var run = $('wewRun');
    if (run) run.addEventListener('click', runCycle);
    var auto = $('wewAuto');
    if (auto) auto.addEventListener('click', toggleAuto);
    var reset = $('wewReset');
    if (reset) reset.addEventListener('click', resetWorld);
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && state.selected) closeDrawer();
    });

    return load().then(function () {
      var deep = params.get('agent');
      if (deep && state.nodes[deep]) {
        if (!state.layout[deep]) {
          // A deep link names one agent, so widen only as far as reaching it needs.
          if (!state.nodes[deep].active) state.scope = 'all';
          else if (!state.nodes[deep].configured) state.scope = 'selected';
          state.layerFocus = state.nodes[deep].layer;
          state.scopeFellBack = false;
          build();
          renderChrome();
        }
        selectNode(deep);
      }
    });
  };

  // Stop the auto timer when the tab is hidden — no point animating offscreen.
  document.addEventListener('visibilitychange', function () {
    if (document.hidden && state.autoTimer) toggleAuto();
  });
})();
