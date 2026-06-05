/**
 * agent_network.js — Simulacrum Agent Network Visualization
 * Full 26-node 7-tier force-directed D3.js diagram with inspector panels,
 * SSE particle animation, breadcrumb trail, and diff mode.
 * PRD: SIM-PRD-VIZ-002
 */
(function () {
  'use strict';

  // ── Constants ────────────────────────────────────────────────────────────
  // Brain-layout: dx/dy offsets from canvas centre per tier
  // T6=frontal top · T1/T2=upper parietal · T3/T4=temporal sides · T5/T7=occipital · ORC=corpus-callosum centre
  const BRAIN_DX = { 1: -170, 2: 170, 3: -240, 4: 240, 5: -160, 6: 0, 7: 160 };
  const BRAIN_DY = { 1: -130, 2: -130, 3: 10, 4: 10, 5: 150, 6: -200, 7: 150 };
  const NODE_RADIUS = { hub: 36, normal: 24, sub: 16 };
  const STEP_COLORS = { harvest: '#0d9488', score: '#d97706', schedule: '#1d4ed8', report: '#0d9488' };
  const STATE_RING_COLORS = { running: '#ffffff', escalated: '#f59e0b', error: '#ef4444' };

  // ── State ────────────────────────────────────────────────────────────────
  let _simId = null;
  let _apiBase = null;
  let _readOnly = false;
  let _svg = null;
  let _g = null;
  let _zoom = null;
  let _simulation = null;
  let _nodePositionCache = {};
  let _currentCycleId = null;
  let _networkData = null;
  let _sseSource = null;
  let _activeInspector = null;
  let _breadcrumb = [];
  let _hiddenTiers = new Set();
  let _particles = [];
  let _animFrame = null;
  let _retryTimers = {};
  let _diffMode = false;
  let _diffCycleA = null;
  let _diffCycleB = null;
  let _activeView = 'network';  // 'network' | 'swimlane'
  let _slInitialized = false;
  let _nedCache = {};  // session cache: action_type → unsaved field values
  let _nedActionId = null;  // current action_id in editor
  let _nedRunTimer = null;

  // ── Entry point ──────────────────────────────────────────────────────────
  window.initAgentNetwork = function (simId, apiBase, opts) {
    opts = opts || {};
    _simId = simId;
    _apiBase = apiBase;
    _readOnly = !!opts.readOnly;

    _bindModalControls();
    _bindKeyboard();
    if (!_readOnly) {
      _buildTierFilter();
      _buildCycleSelector();
      _initSSE();
    } else {
      // Hide controls irrelevant in read-only mode
      ['anv-run-now','anv-share','anv-diff-btn','anv-tier-filter','anv-modal-close-btn'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
      });
    }
    loadNetwork(null);
    _switchView('swimlane');
  };

  window.closeAgentNetwork = function () {
    if (_sseSource) { _sseSource.close(); _sseSource = null; }
    const modal = document.getElementById('anv-modal');
    if (modal) modal.classList.remove('open');
    _closeInspector();
    _closeNodeEditor();
    if (_activeView === 'swimlane') { _switchView('network'); }
    // Reset swimlane so it re-fetches dag fresh on next open
    _slInitialized = false;
    if (window.SL) { window.SL.destroy(); }
  };

  // ── API helpers ──────────────────────────────────────────────────────────
  async function api(path, opts) {
    opts = opts || {};
    const res = await fetch(_apiBase + path, {
      headers: { 'Content-Type': 'application/json' },
      method: opts.method || 'GET',
      body: opts.body || undefined,
    });
    if (!res.ok) {
      const t = await res.text().catch(() => '');
      let msg = res.statusText || ('HTTP ' + res.status);
      try { msg = JSON.parse(t).error || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res.json();
  }

  function esc(s) {
    return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── Network load ─────────────────────────────────────────────────────────
  async function loadNetwork(cycleId) {
    const url = '/network' + (cycleId ? '?cycle_id=' + cycleId : '');
    try {
      _networkData = await api(url);
      _currentCycleId = (_networkData.cycle || {}).id || null;
      _renderDiagram(_networkData.nodes, _networkData.edges);
      const _cyc = _networkData.cycle || {};
      _updatePhaseBadge(_cyc.phase, _cyc.transition_countdown);
      _updateLiveBanner(false);
    } catch (e) {
      console.error('loadNetwork:', e);
    }
  }

  // ── D3 force diagram ─────────────────────────────────────────────────────
  function _renderDiagram(nodes, edges) {
    if (typeof d3 === 'undefined') { _renderFallback(nodes); return; }

    const container = document.getElementById('anv-canvas');
    if (!container) return;
    const W = container.clientWidth || 900;
    const H = container.clientHeight || 600;
    const cx = W / 2, cy = H / 2;

    // Build visible sets (respect tier filter)
    const visibleIds = new Set(nodes.filter(n => !_hiddenTiers.has(n.tier)).map(n => n.id));

    const visNodes = nodes
      .filter(n => visibleIds.has(n.id))
      .map(n => {
        const cached = _nodePositionCache[n.id];
        return Object.assign({}, n, cached ? { x: cached.x, y: cached.y, fx: null, fy: null } : {});
      });

    const visEdges = edges.filter(e => visibleIds.has(e.source) && visibleIds.has(e.target))
      .map(e => Object.assign({}, e));

    if (_simulation) _simulation.stop();
    if (_svg) { d3.select('#anv-canvas svg').remove(); }

    _svg = d3.select(container).append('svg')
      .attr('width', W).attr('height', H)
      .attr('role', 'img')
      .attr('aria-label', 'Agent Network Visualization');

    // Defs
    const defs = _svg.append('defs');
    _buildArrowheadDefs(defs, nodes);
    _buildGlowFilter(defs);

    // Zoom/pan
    _zoom = d3.zoom().scaleExtent([0.3, 2.5])
      .on('zoom', ev => _g.attr('transform', ev.transform));
    _svg.call(_zoom);

    _g = _svg.append('g').attr('class', 'anv-root');

    // ── Brain outline (decorative) ───────────────────────────────────────────
    const _brainG = _g.append('g').attr('class', 'anv-brain-bg');
    const _tc = 'rgba(15,123,114,'; // teal base
    // Left hemisphere ellipse
    _brainG.append('ellipse')
      .attr('cx', cx - 110).attr('cy', cy + 10).attr('rx', 230).attr('ry', 200)
      .attr('fill', _tc + '0.04)').attr('stroke', _tc + '0.20)').attr('stroke-width', 1.5);
    // Right hemisphere ellipse
    _brainG.append('ellipse')
      .attr('cx', cx + 110).attr('cy', cy + 10).attr('rx', 230).attr('ry', 200)
      .attr('fill', _tc + '0.04)').attr('stroke', _tc + '0.20)').attr('stroke-width', 1.5);
    // Interhemispheric fissure
    _brainG.append('line')
      .attr('x1', cx).attr('y1', cy - 192).attr('x2', cx).attr('y2', cy + 192)
      .attr('stroke', _tc + '0.18)').attr('stroke-width', 1).attr('stroke-dasharray', '5,4');
    // Gyri — subtle curved ridges, left then right
    [
      `M ${cx-230},${cy-55} C ${cx-268},${cy-138} ${cx-148},${cy-172} ${cx-62},${cy-130}`,
      `M ${cx-62},${cy-130} C ${cx-18},${cy-114} ${cx-28},${cy-58} ${cx-82},${cy-18}`,
      `M ${cx-242},${cy+62} C ${cx-252},${cy+8} ${cx-178},${cy-12} ${cx-118},${cy+22}`,
      `M ${cx+230},${cy-55} C ${cx+268},${cy-138} ${cx+148},${cy-172} ${cx+62},${cy-130}`,
      `M ${cx+62},${cy-130} C ${cx+18},${cy-114} ${cx+28},${cy-58} ${cx+82},${cy-18}`,
      `M ${cx+242},${cy+62} C ${cx+252},${cy+8} ${cx+178},${cy-12} ${cx+118},${cy+22}`,
    ].forEach(d => _brainG.append('path').attr('d', d)
      .attr('fill', 'none').attr('stroke', _tc + '0.12)').attr('stroke-width', 1.5).attr('stroke-linecap', 'round'));

    // Force simulation — run headless 300 ticks then cache
    _simulation = d3.forceSimulation(visNodes)
      .force('link', d3.forceLink(visEdges)
        .id(d => d.id)
        .distance(d => _edgeDistance(d))
        .strength(d => _edgeStrength(d)))
      .force('charge', d3.forceManyBody()
        .strength(d => d.id === 'ORC' ? -700 : (d.sub ? -80 : -200)))
      .force('center', d3.forceCenter(cx, cy))
      .force('brainX', d3.forceX(d => cx + (d.id === 'ORC' ? 0 : (BRAIN_DX[d.tier] || 0))).strength(0.42))
      .force('brainY', d3.forceY(d => cy + (d.id === 'ORC' ? 0 : (BRAIN_DY[d.tier] || 0))).strength(0.42))
      .force('collision', d3.forceCollide().radius(d => _nodeR(d) + 22))
      .stop();

    // Restore cached positions before ticking
    visNodes.forEach(n => {
      if (_nodePositionCache[n.id]) {
        n.x = _nodePositionCache[n.id].x;
        n.y = _nodePositionCache[n.id].y;
      }
    });

    // Headless ticks
    for (let i = 0; i < 300; i++) _simulation.tick();
    visNodes.forEach(n => { _nodePositionCache[n.id] = { x: n.x, y: n.y }; });

    // Render edges
    const edgeG = _g.append('g').attr('class', 'anv-edges');
    const nodeMap = {};
    visNodes.forEach(n => { nodeMap[n.id] = n; });

    // Separate bidirectional pairs
    const bidiPairs = new Set();
    visEdges.forEach(e => {
      const src = typeof e.source === 'object' ? e.source.id : e.source;
      const tgt = typeof e.target === 'object' ? e.target.id : e.target;
      const rev = tgt + '-' + src;
      if (bidiPairs.has(src + '-' + tgt) || bidiPairs.has(rev)) return;
      bidiPairs.add(src + '-' + tgt);
    });

    visEdges.forEach(e => {
      const srcId = typeof e.source === 'object' ? e.source.id : e.source;
      const tgtId = typeof e.target === 'object' ? e.target.id : e.target;
      const src = nodeMap[srcId], tgt = nodeMap[tgtId];
      if (!src || !tgt) return;

      const revId = tgtId + '-' + srcId;
      const hasSibling = visEdges.some(f => {
        const fs = typeof f.source === 'object' ? f.source.id : f.source;
        const ft = typeof f.target === 'object' ? f.target.id : f.target;
        return fs === tgtId && ft === srcId;
      });

      const color = e.active ? _edgeColor(e, nodes) : '#334155';
      const strokeW = e.active ? 2 : 1;
      const dashArr = e.conditional ? '6,4' : 'none';

      const path = edgeG.append('path')
        .attr('class', 'anv-edge')
        .attr('data-edge-id', e.id)
        .attr('d', _edgePath(src, tgt, hasSibling, false))
        .attr('stroke', color)
        .attr('stroke-width', strokeW)
        .attr('stroke-dasharray', dashArr)
        .attr('fill', 'none')
        .attr('marker-end', 'url(#anv-arrow-' + _tierOf(srcId, nodes) + ')')
        .attr('opacity', e.active ? 1 : 0.35)
        .style('cursor', 'pointer');

      path.on('mouseenter', function (ev) { _onEdgeHover(ev, e, src, tgt, true); })
          .on('mouseleave', function (ev) { _onEdgeHover(ev, e, src, tgt, false); })
          .on('click', function (ev) { ev.stopPropagation(); _openEdgeInspector(e.id); });

      // Store path element on edge for particle animation
      e._pathEl = path.node();
    });

    // Render nodes
    const nodeG = _g.append('g').attr('class', 'anv-nodes');

    visNodes.forEach(n => {
      const g = nodeG.append('g')
        .datum(n)
        .attr('class', 'anv-node-group')
        .attr('data-node-id', n.id)
        .attr('data-tier', n.tier)
        .attr('transform', `translate(${n.x},${n.y})`)
        .attr('tabindex', '0')
        .attr('role', 'button')
        .attr('aria-label', n.label)
        .style('cursor', 'pointer');

      const r = _nodeR(n);
      const fillColor = n.id === 'ORC' ? '#0d1b3e' : n.color;
      const strokeColor = n.id === 'ORC' ? '#ffffff' : (n.status === 'selected' ? '#ffffff' : 'rgba(255,255,255,0.25)');
      const strokeW = n.id === 'ORC' ? 3 : (n.hub ? 3 : 1.5);
      const opacity = _nodeOpacity(n);
      const dashArr = (n.conditional && n.locked) ? '5,3' : 'none';

      // Pulse ring (running state)
      g.append('circle').attr('class', 'anv-pulse-ring').attr('r', r)
        .attr('fill', 'none').attr('stroke', STATE_RING_COLORS.running || '#fff')
        .attr('stroke-width', 2).attr('opacity', 0);

      // Main circle
      g.append('circle').attr('class', 'anv-node-circle').attr('r', r)
        .attr('fill', fillColor)
        .attr('stroke', (n.status === 'error') ? '#ef4444' : strokeColor)
        .attr('stroke-width', (n.status === 'error') ? 3 : strokeW)
        .attr('stroke-dasharray', dashArr)
        .attr('opacity', opacity);

      // Label
      if (!n.sub) {
        g.append('text').attr('class', 'anv-node-label')
          .attr('dy', r + 14)
          .attr('text-anchor', 'middle')
          .attr('fill', '#94a3b8')
          .attr('font-size', '10px')
          .attr('font-family', 'system-ui,sans-serif')
          .attr('pointer-events', 'none')
          .text(n.id);
      }

      // Sub-node: label inside
      if (n.sub) {
        g.append('text').attr('class', 'anv-node-label-inside')
          .attr('dy', '0.35em')
          .attr('text-anchor', 'middle')
          .attr('fill', '#ffffff')
          .attr('font-size', '7px')
          .attr('font-family', 'system-ui,sans-serif')
          .attr('pointer-events', 'none')
          .text(n.id);
      }

      // Status badges
      if (n.status === 'complete' || n.status === 'error') {
        const bc = n.status === 'error' ? '#ef4444' : '#22c55e';
        const bl = n.status === 'error' ? '✗' : '✓';
        g.append('circle').attr('class', 'anv-badge-circle')
          .attr('cx', r * 0.7).attr('cy', -r * 0.7).attr('r', 7)
          .attr('fill', bc);
        g.append('text').attr('class', 'anv-badge-text')
          .attr('x', r * 0.7).attr('y', -r * 0.7)
          .attr('dy', '0.35em').attr('text-anchor', 'middle')
          .attr('fill', '#fff').attr('font-size', '8px').attr('font-weight', '700')
          .attr('pointer-events', 'none')
          .text(bl);
      }

      if (n.status === 'escalated' && n.badge_count > 0) {
        g.append('circle').attr('class', 'anv-badge-circle')
          .attr('cx', r * 0.7).attr('cy', -r * 0.7).attr('r', 8)
          .attr('fill', '#f59e0b');
        g.append('text').attr('class', 'anv-badge-text')
          .attr('x', r * 0.7).attr('y', -r * 0.7)
          .attr('dy', '0.35em').attr('text-anchor', 'middle')
          .attr('fill', '#fff').attr('font-size', '8px').attr('font-weight', '700')
          .attr('pointer-events', 'none')
          .text(n.badge_count > 9 ? '9+' : String(n.badge_count));
      }

      // Locked overlay for FIN when fintech off
      if (n.locked) {
        g.append('text').attr('dy', '0.35em').attr('text-anchor', 'middle')
          .attr('fill', 'rgba(255,255,255,0.5)').attr('font-size', String(r * 0.6) + 'px')
          .attr('pointer-events', 'none').text('🔒');
      }

      // Pulse animation for running state
      if (n.status === 'running') {
        const ring = g.select('.anv-pulse-ring');
        _startPulse(ring, r);
      }

      // Drag
      g.call(d3.drag()
        .on('start', (ev, d) => {
          if (!ev.active) _simulation.alphaTarget(0.1).restart();
          d.fx = d.x; d.fy = d.y;
        })
        .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
        .on('end', (ev, d) => {
          if (!ev.active) _simulation.alphaTarget(0);
          _nodePositionCache[d.id] = { x: ev.x, y: ev.y };
          d.fx = null; d.fy = null;
        }));

      // Click → node inspector
      g.on('click', (ev) => { ev.stopPropagation(); _openNodeInspector(n.id); });
      g.on('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); _openNodeInspector(n.id); } });

      // Hover highlight
      g.on('mouseenter', (ev) => _onNodeHover(n.id, true, visEdges, nodeMap));
      g.on('mouseleave', (ev) => _onNodeHover(n.id, false, visEdges, nodeMap));
    });

    // Restart simulation for live update
    _simulation.nodes(visNodes).on('tick', () => {
      _g.selectAll('.anv-node-group').attr('transform', d => `translate(${d.x},${d.y})`);
      _g.selectAll('.anv-edge').attr('d', function () {
        const eid = this.getAttribute('data-edge-id');
        const e = visEdges.find(x => x.id === eid);
        if (!e) return '';
        const src = typeof e.source === 'object' ? e.source : nodeMap[e.source];
        const tgt = typeof e.target === 'object' ? e.target : nodeMap[e.target];
        if (!src || !tgt) return '';
        const hasSib = visEdges.some(f => {
          const fs = typeof f.source === 'object' ? f.source.id : f.source;
          const ft = typeof f.target === 'object' ? f.target.id : f.target;
          return fs === (typeof tgt === 'object' ? tgt.id : tgt) && ft === (typeof src === 'object' ? src.id : src);
        });
        return _edgePath(src, tgt, hasSib, false);
      });
    });
    _simulation.alpha(0.05).restart();
  }

  function _renderFallback(nodes) {
    const c = document.getElementById('anv-canvas');
    if (!c) return;
    c.innerHTML = '<div style="padding:2rem;color:#64748b;font-size:.85rem">' +
      'D3.js required for Agent Network Visualization. Loading from CDN…</div>';
  }

  // ── Geometry helpers ─────────────────────────────────────────────────────
  function _nodeR(n) {
    if (n.hub) return NODE_RADIUS.hub;
    if (n.sub) return NODE_RADIUS.sub;
    return NODE_RADIUS.normal;
  }

  function _tierOf(nodeId, nodes) {
    const n = (nodes || []).find(x => x.id === nodeId);
    return n ? n.tier : 1;
  }

  function _edgeDistance(e) {
    const sid = typeof e.source === 'object' ? e.source.id : e.source;
    const tid = typeof e.target === 'object' ? e.target.id : e.target;
    if (sid === 'ORC' || tid === 'ORC') return 120;
    return 80;
  }

  function _edgeStrength(e) {
    const sid = typeof e.source === 'object' ? e.source.id : e.source;
    const tid = typeof e.target === 'object' ? e.target.id : e.target;
    if (sid === 'ORC' || tid === 'ORC') return 0.4;
    return 0.6;
  }

  function _edgePath(src, tgt, curved, reverse) {
    const sx = src.x, sy = src.y, tx = tgt.x, ty = tgt.y;
    if (!curved) return `M${sx},${sy}L${tx},${ty}`;
    const dx = tx - sx, dy = ty - sy;
    const dr = Math.sqrt(dx * dx + dy * dy) * 0.6;
    const sweep = reverse ? 0 : 1;
    return `M${sx},${sy}A${dr},${dr} 0 0,${sweep} ${tx},${ty}`;
  }

  function _edgeColor(e, nodes) {
    const tierColors = {1:'#1B3A6B',2:'#334155',3:'#BA7517',4:'#13A89E',5:'#888888',6:'#1B3A6B',7:'#444444'};
    const srcId = typeof e.source === 'object' ? e.source.id : e.source;
    const tier = _tierOf(srcId, nodes);
    return tierColors[tier] || '#64748b';
  }

  function _nodeOpacity(n) {
    if (n.status === 'idle') return 0.35;
    return 1;
  }

  // ── Arrow defs ───────────────────────────────────────────────────────────
  function _buildArrowheadDefs(defs, nodes) {
    const tierColors = {1:'#1B3A6B',2:'#334155',3:'#BA7517',4:'#13A89E',5:'#888888',6:'#1B3A6B',7:'#444444'};
    [1,2,3,4,5,6,7].forEach(t => {
      defs.append('marker').attr('id', 'anv-arrow-' + t)
        .attr('viewBox','0 0 8 8').attr('refX',14).attr('refY',3)
        .attr('markerWidth',6).attr('markerHeight',6).attr('orient','auto')
        .append('path').attr('d','M0,0 L0,6 L8,3 z').attr('fill', tierColors[t] || '#64748b');
    });
  }

  function _buildGlowFilter(defs) {
    const f = defs.append('filter').attr('id','anv-glow').attr('x','-50%').attr('y','-50%').attr('width','200%').attr('height','200%');
    f.append('feGaussianBlur').attr('stdDeviation','3').attr('result','coloredBlur');
    const merge = f.append('feMerge');
    merge.append('feMergeNode').attr('in','coloredBlur');
    merge.append('feMergeNode').attr('in','SourceGraphic');
  }

  // ── Pulse animation ──────────────────────────────────────────────────────
  function _startPulse(ring, baseR) {
    if (!ring || ring.empty()) return;
    function tick() {
      ring.attr('opacity', 0.7).attr('r', baseR)
        .transition().duration(1200).ease(d3.easeLinear)
        .attr('r', baseR + 18).attr('opacity', 0)
        .on('end', tick);
    }
    tick();
  }

  // ── Hover effects ────────────────────────────────────────────────────────
  function _onNodeHover(nodeId, entering, edges, nodeMap) {
    if (!_g) return;
    if (entering) {
      const connected = new Set([nodeId]);
      edges.forEach(e => {
        const sid = typeof e.source === 'object' ? e.source.id : e.source;
        const tid = typeof e.target === 'object' ? e.target.id : e.target;
        if (sid === nodeId) connected.add(tid);
        if (tid === nodeId) connected.add(sid);
      });
      _g.selectAll('.anv-node-group').attr('opacity', d => connected.has(d.id) ? 1 : 0.2);
      _g.selectAll('.anv-edge').attr('opacity', function () {
        const eid = this.getAttribute('data-edge-id');
        const e = edges.find(x => x.id === eid);
        if (!e) return 0.2;
        const sid = typeof e.source === 'object' ? e.source.id : e.source;
        const tid = typeof e.target === 'object' ? e.target.id : e.target;
        return (sid === nodeId || tid === nodeId) ? 1 : 0.1;
      });
    } else {
      _g.selectAll('.anv-node-group').attr('opacity', 1);
      _g.selectAll('.anv-edge').attr('opacity', function () {
        const eid = this.getAttribute('data-edge-id');
        const e = edges.find(x => x.id === eid);
        return e && e.active ? 1 : 0.35;
      });
    }
  }

  function _onEdgeHover(ev, edge, src, tgt, entering) {
    const el = ev.currentTarget;
    if (entering) {
      d3.select(el).attr('stroke-width', 4).attr('opacity', 1);
      _showEdgeTooltip(ev, edge, src, tgt);
    } else {
      d3.select(el).attr('stroke-width', edge.active ? 2 : 1).attr('opacity', edge.active ? 1 : 0.35);
      _hideEdgeTooltip();
    }
  }

  // ── Edge tooltip ─────────────────────────────────────────────────────────
  function _showEdgeTooltip(ev, edge, src, tgt) {
    let tip = document.getElementById('anv-edge-tooltip');
    if (!tip) {
      tip = document.createElement('div');
      tip.id = 'anv-edge-tooltip';
      tip.style.cssText = 'position:fixed;background:#1e293b;color:#f1f5f9;font-size:.75rem;padding:.35rem .65rem;border-radius:5px;pointer-events:none;z-index:9999;max-width:220px;line-height:1.4';
      document.body.appendChild(tip);
    }
    const srcId = typeof src === 'object' ? src.id : src;
    const tgtId = typeof tgt === 'object' ? tgt.id : tgt;
    tip.innerHTML = `<strong>${esc(srcId)} → ${esc(tgtId)}</strong><br>
      Step: ${esc(edge.step || '—')} &nbsp;·&nbsp; ${edge.active ? '<span style="color:#4ade80">Active</span>' : '<span style="color:#94a3b8">Idle</span>'}<br>
      <span style="color:#94a3b8">Click to inspect payload</span>`;
    tip.style.left = (ev.clientX + 12) + 'px';
    tip.style.top = (ev.clientY - 8) + 'px';
    tip.style.display = 'block';
  }

  function _hideEdgeTooltip() {
    const tip = document.getElementById('anv-edge-tooltip');
    if (tip) tip.style.display = 'none';
  }

  // ── Inspector panel ──────────────────────────────────────────────────────
  function _openNodeInspector(nodeId) {
    _breadcrumb.push({ type: 'node', id: nodeId, label: nodeId });
    if (_breadcrumb.length > 8) _breadcrumb.shift();
    _renderBreadcrumb();
    _fetchAndShowNodeInspector(nodeId);
  }

  function _openEdgeInspector(edgeId) {
    _breadcrumb.push({ type: 'edge', id: edgeId, label: edgeId });
    if (_breadcrumb.length > 8) _breadcrumb.shift();
    _renderBreadcrumb();
    _fetchAndShowEdgeInspector(edgeId);
  }

  async function _fetchAndShowNodeInspector(nodeId) {
    const panel = document.getElementById('anv-inspector');
    if (!panel) return;
    panel.classList.add('open');
    _activeInspector = { type: 'node', id: nodeId };
    document.getElementById('anv-inspector-content').innerHTML =
      '<div style="padding:1.5rem;color:#64748b">Loading…</div>';

    try {
      const url = '/network/node/' + nodeId + (_currentCycleId ? '?cycle_id=' + _currentCycleId : '');
      const data = await api(url);
      _renderNodeInspector(data);
    } catch (e) {
      document.getElementById('anv-inspector-content').innerHTML =
        '<div style="padding:1rem;color:#ef4444">Error: ' + esc(e.message) + '</div>';
    }
  }

  function _renderNodeInspector(data) {
    const node = data.node || {};
    const ov = data.overview || {};
    const inputs = data.inputs || [];
    const outputs = data.outputs || [];
    const history = data.history || [];

    const statusColor = { complete:'#22c55e', error:'#ef4444', escalated:'#f59e0b', running:'#60a5fa', idle:'#475569', active:'#818cf8' };

    document.getElementById('anv-inspector-content').innerHTML = `
      <div class="insp-node-header">
        <div class="insp-node-id">${esc(node.id)}</div>
        <div class="insp-node-label">${esc(node.label)}</div>
        <div class="insp-status-badge" style="color:${statusColor[ov.status]||'#94a3b8'}">● ${esc(ov.status || 'idle')}</div>
        <div class="insp-tier-label">Tier ${esc(String(node.tier || ''))}</div>
      </div>
      <div class="insp-tabs">
        <button class="insp-tab active" data-tab="overview">Overview</button>
        <button class="insp-tab" data-tab="inputs">Inputs <span class="insp-tab-count">${inputs.length}</span></button>
        <button class="insp-tab" data-tab="outputs">Outputs <span class="insp-tab-count">${outputs.length}</span></button>
        <button class="insp-tab" data-tab="history">History</button>
      </div>
      <div class="insp-tab-content" id="insp-tab-overview">
        ${_renderOverviewTab(ov)}
      </div>
      <div class="insp-tab-content hidden" id="insp-tab-inputs">
        ${_renderPayloadTable(inputs, 'from_node', 'From', node.id)}
        <button class="insp-copy-btn" onclick="window._anvCopyJson(${esc(JSON.stringify(inputs))})">Copy as JSON</button>
      </div>
      <div class="insp-tab-content hidden" id="insp-tab-outputs">
        ${_renderPayloadTable(outputs, 'to_node', 'To', node.id)}
        <button class="insp-copy-btn" onclick="window._anvCopyJson(${esc(JSON.stringify(outputs))})">Copy as JSON</button>
      </div>
      <div class="insp-tab-content hidden" id="insp-tab-history">
        ${_renderHistoryTab(history)}
      </div>`;

    document.querySelectorAll('#anv-inspector .insp-tab').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('#anv-inspector .insp-tab').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('#anv-inspector .insp-tab-content').forEach(c => c.classList.add('hidden'));
        btn.classList.add('active');
        const tabEl = document.getElementById('insp-tab-' + btn.dataset.tab);
        if (tabEl) tabEl.classList.remove('hidden');
      });
    });
  }

  function _renderOverviewTab(ov) {
    const rows = [
      ['Status', ov.status || '—'],
      ['Cycle', ov.cycle_number != null ? '#' + ov.cycle_number : '—'],
      ['Phase', ov.phase || '—'],
      ov.actions_scored != null ? ['Actions scored', ov.actions_scored] : null,
      ov.actions_dispatched != null ? ['Actions dispatched', ov.actions_dispatched] : null,
      ov.actions_escalated != null ? ['Actions escalated', ov.actions_escalated] : null,
      ov.last_computed ? ['Last computed', new Date(ov.last_computed).toLocaleString()] : null,
    ].filter(Boolean);
    return '<table class="insp-kv-table">' +
      rows.map(([k, v]) => `<tr><td class="insp-kv-key">${esc(k)}</td><td class="insp-kv-val">${esc(String(v))}</td></tr>`).join('') +
      '</table>';
  }

  function _renderPayloadTable(items, dirField, dirLabel, currentNodeId) {
    if (!items.length) return '<p class="insp-empty">No data for this cycle.</p>';
    return '<table class="insp-payload-table">' +
      '<thead><tr><th>Field</th><th>Value</th><th>Type</th><th>' + esc(dirLabel) + '</th><th></th></tr></thead><tbody>' +
      items.map((item, idx) => {
        const rawVal = item.value;
        const isComplex = rawVal !== null && typeof rawVal === 'object';
        const displayVal = isComplex
          ? `<span class="insp-collapse-toggle" onclick="window._anvToggleCollapse(this)">[${Array.isArray(rawVal) ? rawVal.length + ' items' : 'object'}]</span><span class="insp-collapse-body" style="display:none"><pre style="font-size:.7rem;margin:.25rem 0 0">${esc(JSON.stringify(rawVal, null, 2))}</pre></span>`
          : esc(String(rawVal != null ? rawVal : '—'));
        const dirId = item[dirField];
        const traceDir = dirField === 'from_node' ? '←' : '→';
        const traceBtn = dirId ? `<button class="insp-trace-btn" title="${traceDir} Trace" onclick="window._anvTrace('${esc(dirId)}','${esc(currentNodeId)}','${dirField}')">${traceDir}</button>` : '';
        return `<tr><td class="insp-kv-key">${esc(item.field)}</td><td>${displayVal}</td>` +
          `<td style="color:#64748b;font-size:.72rem">${esc(item.type||'')}</td>` +
          `<td style="color:#64748b;font-size:.72rem">${esc(dirId||'—')}</td><td>${traceBtn}</td></tr>`;
      }).join('') +
      '</tbody></table>';
  }

  function _renderHistoryTab(history) {
    if (!history.length) return '<p class="insp-empty">No history yet.</p>';
    const statusColor = { complete:'#22c55e', error:'#ef4444', escalated:'#f59e0b', running:'#60a5fa', idle:'#475569', active:'#818cf8' };
    return '<table class="insp-payload-table"><thead><tr><th>Cycle</th><th>Date</th><th>Status</th><th>Summary</th><th></th></tr></thead><tbody>' +
      history.map(h =>
        `<tr><td>#${esc(String(h.cycle_number))}</td>` +
        `<td style="font-size:.75rem;color:#64748b">${esc(new Date(h.date).toLocaleDateString())}</td>` +
        `<td style="color:${statusColor[h.status]||'#94a3b8'}">${esc(h.status)}</td>` +
        `<td style="font-size:.78rem;color:#94a3b8">${esc(h.summary||'')}</td>` +
        `<td><button class="insp-trace-btn" onclick="window._anvSwitchCycle('${esc(h.cycle_id)}')">View</button></td></tr>`
      ).join('') +
      '</tbody></table>';
  }

  async function _fetchAndShowEdgeInspector(edgeId) {
    const panel = document.getElementById('anv-inspector');
    if (!panel) return;
    panel.classList.add('open');
    _activeInspector = { type: 'edge', id: edgeId };
    document.getElementById('anv-inspector-content').innerHTML =
      '<div style="padding:1.5rem;color:#64748b">Loading…</div>';

    try {
      const url = '/network/edge/' + edgeId + (_currentCycleId ? '?cycle_id=' + _currentCycleId : '');
      const data = await api(url);
      _renderEdgeInspector(data);
    } catch (e) {
      document.getElementById('anv-inspector-content').innerHTML =
        '<div style="padding:1rem;color:#ef4444">Error: ' + esc(e.message) + '</div>';
    }
  }

  function _renderEdgeInspector(data) {
    const edge = data.edge || {};
    const payload = data.payload || [];
    const history = data.history || [];
    const active = data.active;
    const isErr = data.error;

    document.getElementById('anv-inspector-content').innerHTML = `
      <div class="insp-node-header">
        <div class="insp-node-id">${esc(edge.source)} → ${esc(edge.target)}</div>
        <div class="insp-node-label">Step: ${esc(edge.step || '—')}</div>
        <div class="insp-status-badge" style="color:${isErr ? '#ef4444' : active ? '#22c55e' : '#475569'}">
          ● ${isErr ? 'Error' : active ? 'Active' : 'Idle'}
        </div>
        ${edge.conditional ? '<div class="insp-tier-label" style="color:#f59e0b">Conditional</div>' : ''}
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;padding:.5rem 1rem;border-bottom:1px solid #1e293b">
        <span style="font-size:.75rem;color:#64748b">Payload — ${payload.length} fields</span>
        <button class="insp-copy-btn" onclick="window._anvCopyJson(${esc(JSON.stringify(payload))})">Copy as JSON</button>
      </div>
      ${_renderEdgePayloadTable(payload, edge)}
      <div style="padding:.75rem 1rem .25rem;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#475569">
        Payload History (last 5 cycles)
      </div>
      ${_renderEdgeHistory(history)}
      ${isErr ? `<div class="insp-error-detail">
        <div style="font-weight:700;color:#ef4444;margin-bottom:.4rem">Connection Error</div>
        <div style="font-size:.82rem;color:#94a3b8">The connector returned an error during the last harvest attempt.</div>
        <button class="insp-retry-btn" id="retry-${esc(edge.source)}" onclick="window._anvRetryConnector('${esc(edge.source)}','${esc(edge.id)}')">↻ Retry connection</button>
      </div>` : ''}`;
  }

  function _renderEdgePayloadTable(payload, edge) {
    if (!payload.length) return '<p class="insp-empty" style="padding:1rem">No payload data for this cycle.</p>';
    return '<table class="insp-payload-table" style="margin:.25rem 0">' +
      '<thead><tr><th>Field</th><th>Value</th><th>Type</th><th></th><th></th></tr></thead><tbody>' +
      payload.map(item => {
        const rawVal = item.value;
        const isComplex = rawVal !== null && typeof rawVal === 'object';
        const displayVal = isComplex
          ? `<span class="insp-collapse-toggle" onclick="window._anvToggleCollapse(this)">[${Array.isArray(rawVal) ? rawVal.length + ' items' : 'object'}]</span><span class="insp-collapse-body" style="display:none"><pre style="font-size:.7rem;margin:.25rem 0 0">${esc(JSON.stringify(rawVal, null, 2))}</pre></span>`
          : esc(String(rawVal != null ? rawVal : '—'));
        return `<tr><td class="insp-kv-key">${esc(item.field)}</td><td>${displayVal}</td>` +
          `<td style="color:#64748b;font-size:.72rem">${esc(item.type||'')}</td>` +
          `<td><button class="insp-trace-btn" title="← Where from" onclick="window._anvTrace('${esc(edge.source)}',null,'from_node')">←</button></td>` +
          `<td><button class="insp-trace-btn" title="→ Where to" onclick="window._anvTrace('${esc(edge.target)}',null,'to_node')">→</button></td></tr>`;
      }).join('') +
      '</tbody></table>';
  }

  function _renderEdgeHistory(history) {
    if (!history.length) return '<p class="insp-empty" style="padding:.5rem 1rem">No history yet.</p>';
    return '<table class="insp-payload-table" style="margin:.25rem 0">' +
      '<thead><tr><th>Cycle</th><th>Date</th><th>Status</th><th></th></tr></thead><tbody>' +
      history.map(h =>
        `<tr><td>#${esc(String(h.cycle_number))}</td>` +
        `<td style="font-size:.75rem;color:#64748b">${esc(new Date(h.date).toLocaleDateString())}</td>` +
        `<td style="color:${h.active ? '#22c55e' : '#475569'}">${h.active ? 'Active' : 'Idle'}</td>` +
        `<td><button class="insp-trace-btn" onclick="window._anvSwitchCycle('${esc(h.cycle_id)}')">View</button></td></tr>`
      ).join('') +
      '</tbody></table>';
  }

  // ── Breadcrumb trail ─────────────────────────────────────────────────────
  function _renderBreadcrumb() {
    const bar = document.getElementById('anv-breadcrumb');
    if (!bar) return;
    bar.innerHTML = _breadcrumb.map((item, idx) => {
      const isLast = idx === _breadcrumb.length - 1;
      const label = item.type === 'edge' ? item.id.replace('-','→') : item.id;
      const cls = isLast ? 'anv-bc-item anv-bc-current' : 'anv-bc-item';
      const onclick = isLast ? '' : `onclick="window._anvBreadcrumbNav(${idx})"`;
      return `<span class="${cls}" ${onclick}>${esc(label)}</span>` +
        (isLast ? '' : '<span class="anv-bc-sep">›</span>');
    }).join('');
  }

  window._anvBreadcrumbNav = function (idx) {
    _breadcrumb = _breadcrumb.slice(0, idx + 1);
    _renderBreadcrumb();
    const item = _breadcrumb[idx];
    if (item.type === 'node') _fetchAndShowNodeInspector(item.id);
    else _fetchAndShowEdgeInspector(item.id);
  };

  window._anvTrace = function (targetId, currentId, dirField) {
    const isEdge = targetId && targetId.includes('-');
    if (isEdge) _openEdgeInspector(targetId);
    else if (targetId) _openNodeInspector(targetId);
  };

  window._anvSwitchCycle = function (cycleId) {
    loadNetwork(cycleId);
    _updateCycleSelectorValue(cycleId);
    _closeInspector();
    _breadcrumb = [];
    _renderBreadcrumb();
  };

  window._anvCopyJson = function (data) {
    const str = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
    navigator.clipboard.writeText(str).then(() => _showToast('Payload copied.')).catch(() => {});
  };

  window._anvToggleCollapse = function (el) {
    const body = el.nextElementSibling;
    if (!body) return;
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : 'block';
    el.textContent = open ? el.textContent.replace('▴','▾') : el.textContent.replace('▾','▴');
  };

  window._anvRetryConnector = function (connectorId, edgeId) {
    const btn = document.getElementById('retry-' + connectorId);
    if (btn) { btn.disabled = true; btn.textContent = 'Retrying…'; }
    api('/harvest/' + connectorId, { method: 'POST' }).then(d => {
      _showToast(d.message || 'Retry queued.');
      if (btn) {
        setTimeout(() => { btn.disabled = false; btn.textContent = '↻ Retry connection'; }, 30000);
      }
    }).catch(e => {
      if (btn) { btn.disabled = false; btn.textContent = '↻ Retry connection'; }
      _showToast('Error: ' + e.message);
    });
  };

  // ── Inspector close ──────────────────────────────────────────────────────
  function _closeInspector() {
    const panel = document.getElementById('anv-inspector');
    if (panel) panel.classList.remove('open');
    _activeInspector = null;
    // Re-highlight all nodes/edges
    if (_g) {
      _g.selectAll('.anv-node-group').attr('opacity', 1);
      _g.selectAll('.anv-edge').attr('opacity', function () {
        const eid = this.getAttribute('data-edge-id');
        if (!_networkData) return 0.35;
        const e = (_networkData.edges || []).find(x => x.id === eid);
        return e && e.active ? 1 : 0.35;
      });
    }
  }

  // ── Tier filter ──────────────────────────────────────────────────────────
  function _buildTierFilter() {
    const bar = document.getElementById('anv-tier-filter');
    if (!bar) return;
    for (let t = 1; t <= 7; t++) {
      const btn = document.createElement('button');
      btn.className = 'anv-tier-btn active';
      btn.dataset.tier = t;
      btn.textContent = 'T' + t;
      btn.setAttribute('title', 'Tier ' + t);
      btn.addEventListener('click', () => {
        if (_hiddenTiers.has(t)) {
          _hiddenTiers.delete(t);
          btn.classList.add('active');
        } else {
          _hiddenTiers.add(t);
          btn.classList.remove('active');
        }
        if (_networkData) _renderDiagram(_networkData.nodes, _networkData.edges);
      });
      bar.appendChild(btn);
    }
    const reset = document.createElement('button');
    reset.className = 'anv-tier-btn';
    reset.textContent = 'All';
    reset.style.marginLeft = '8px';
    reset.addEventListener('click', () => {
      _hiddenTiers.clear();
      bar.querySelectorAll('.anv-tier-btn[data-tier]').forEach(b => b.classList.add('active'));
      if (_networkData) _renderDiagram(_networkData.nodes, _networkData.edges);
    });
    bar.appendChild(reset);
  }

  // ── Cycle selector ───────────────────────────────────────────────────────
  async function _buildCycleSelector() {
    try {
      const cycles = await api('/cycles');
      const sel = document.getElementById('anv-cycle-select');
      if (!sel) return;
      sel.innerHTML = '<option value="">Latest cycle</option>' +
        cycles.map(c =>
          `<option value="${esc(c.id)}">Cycle ${esc(String(c.cycle_number))} — ${esc((c.cycle_started_at||'').slice(0,10))} — ${esc(c.phase)}</option>`
        ).join('');
      sel.addEventListener('change', () => {
        if (sel.value) loadNetwork(sel.value);
        else loadNetwork(null);
      });
    } catch (_) {}
  }

  function _updateCycleSelectorValue(cycleId) {
    const sel = document.getElementById('anv-cycle-select');
    if (sel) sel.value = cycleId || '';
  }

  // ── Phase badge ──────────────────────────────────────────────────────────
  function _updatePhaseBadge(phase, transitionCountdown) {
    const el = document.getElementById('anv-phase-badge');
    if (!el) return;
    el.className = 'anv-phase-badge';
    if (phase === 'explore') {
      el.classList.add('anv-phase-explore');
      el.textContent = transitionCountdown != null && transitionCountdown > 0
        ? 'Explore · ' + transitionCountdown + ' cycle' + (transitionCountdown === 1 ? '' : 's') + ' left'
        : 'Explore';
    } else if (phase === 'exploit') {
      el.classList.add('anv-phase-exploit');
      el.textContent = 'Exploit';
    } else {
      el.textContent = phase || '—';
    }
    el.title = phase === 'explore'
      ? 'Explore phase: the orchestrator samples all layers to build outcome data. Transitions to Exploit once enough cycles have run.'
      : phase === 'exploit'
        ? 'Exploit phase: the orchestrator concentrates actions on highest-yield layers based on Bayesian posteriors.'
        : '';
  }

  // ── Live banner ──────────────────────────────────────────────────────────
  function _updateLiveBanner(show) {
    const b = document.getElementById('anv-live-banner');
    if (!b) return;
    b.style.display = show ? 'flex' : 'none';
  }

  // ── SSE particle animation ───────────────────────────────────────────────
  function _initSSE() {
    if (typeof EventSource === 'undefined') return;
    if (_sseSource) { _sseSource.close(); _sseSource = null; }
    _sseSource = new EventSource(_apiBase + '/stream');

    _sseSource.addEventListener('cycle_started', ev => {
      const d = _safeJson(ev.data);
      _updatePhaseBadge(d.phase, d.transition_countdown);
      loadNetwork(null);
    });

    _sseSource.addEventListener('harvest_pull', ev => {
      const d = _safeJson(ev.data);
      _animateParticle(d.connector_id + '-ORC', d.connector_id);
      _setNodeStateLive(d.connector_id, 'running');
    });

    _sseSource.addEventListener('harvest_complete', ev => {
      ['APO','LIN','CON','CAL','STR'].forEach(id => _setNodeStateLive(id, 'complete'));
    });

    _sseSource.addEventListener('score_started', ev => {
      ['BAY','DAG','EXP'].forEach(id => _setNodeStateLive(id, 'running'));
      ['ORC-BAY','ORC-DAG','ORC-EXP'].forEach(eid => _animateParticle(eid, 'ORC'));
    });

    _sseSource.addEventListener('score_complete', ev => {
      ['BAY','DAG','EXP'].forEach(id => _setNodeStateLive(id, 'complete'));
      ['BAY-ORC','DAG-ORC','EXP-ORC'].forEach(eid => _animateParticle(eid, null));
    });

    _sseSource.addEventListener('dispatch', ev => {
      const d = _safeJson(ev.data);
      const layer = d.source_layer || 1;
      _setNodeStateLive('L' + layer, 'running');
      _animateParticle('ORC-L' + layer, 'ORC');
    });

    _sseSource.addEventListener('action_complete', ev => {
      const d = _safeJson(ev.data);
      const layer = d.source_layer || 1;
      _setNodeStateLive('L' + layer, 'complete');
      _animateParticle('L' + layer + '-ORC', 'L' + layer);
      if (_activeView === 'swimlane' && window.SL && d.action_type) {
        window.SL.pulse(d.action_type);
      }
    });

    _sseSource.addEventListener('escalation_added', ev => {
      _setNodeStateLive('ESC', 'escalated');
      _animateParticle('ORC-ESC', 'ORC');
    });

    _sseSource.addEventListener('report_push', ev => {
      _animateParticle('ORC-GCC', 'ORC');
      ['GCC','NUM','ACT','MOM'].forEach(id => _setNodeStateLive(id, 'complete'));
    });

    _sseSource.addEventListener('log_write', ev => {
      _animateParticle('ORC-LOG', 'ORC');
    });

    _sseSource.addEventListener('cycle_complete', ev => {
      const d = _safeJson(ev.data);
      const cycleId = (d.cycle || {}).id;
      if (cycleId) {
        _updateCycleSelectorValue(cycleId);
        loadNetwork(cycleId);
      }
      _updateLiveBanner(false);
    });

    _sseSource.addEventListener('step_running', ev => {
      // Updates step indicator in existing 4-step diagram — passthrough
    });
  }

  function _safeJson(str) {
    try { return JSON.parse(str); } catch (_) { return {}; }
  }

  // ── Live node state update (SSE-driven, no full reload) ──────────────────
  function _setNodeStateLive(nodeId, status) {
    if (!_g) return;
    const group = _g.select(`.anv-node-group[data-node-id="${nodeId}"]`);
    if (group.empty()) return;

    const circle = group.select('.anv-node-circle');
    const ring = group.select('.anv-pulse-ring');

    // Badges
    group.selectAll('.anv-badge-circle, .anv-badge-text').remove();
    circle.attr('stroke', status === 'error' ? '#ef4444' : status === 'running' ? '#60a5fa' : 'rgba(255,255,255,0.25)');
    circle.attr('opacity', status === 'idle' ? 0.35 : 1);

    if (status === 'running') {
      const n = (_networkData.nodes || []).find(x => x.id === nodeId);
      if (n) _startPulse(ring, _nodeR(n));
    } else {
      ring.interrupt().attr('opacity', 0).attr('r', 24);
    }
    if (status === 'complete') {
      const r = parseFloat(circle.attr('r') || 24);
      group.append('circle').attr('class','anv-badge-circle').attr('cx',r*0.7).attr('cy',-r*0.7).attr('r',7).attr('fill','#22c55e');
      group.append('text').attr('class','anv-badge-text').attr('x',r*0.7).attr('y',-r*0.7).attr('dy','0.35em').attr('text-anchor','middle').attr('fill','#fff').attr('font-size','8px').attr('font-weight','700').attr('pointer-events','none').text('✓');
    }
  }

  // ── Particle animation ───────────────────────────────────────────────────
  function _animateParticle(edgeId, sourceNodeId) {
    if (!_g || !_networkData) return;
    const edgeDef = (_networkData.edges || []).find(e => e.id === edgeId);
    if (!edgeDef) return;

    // Find the edge path element
    const pathEl = _g.select(`.anv-edge[data-edge-id="${edgeId}"]`).node();
    if (!pathEl) return;

    const tierColors = {1:'#1B3A6B',2:'#6366f1',3:'#BA7517',4:'#13A89E',5:'#aaaaaa',6:'#1B3A6B',7:'#777777'};
    const srcId = typeof edgeDef.source === 'object' ? edgeDef.source.id : edgeDef.source;
    const srcNode = (_networkData.nodes || []).find(n => n.id === srcId);
    const tier = srcNode ? srcNode.tier : 1;
    const color = tierColors[tier] || '#6366f1';

    const totalLen = pathEl.getTotalLength ? pathEl.getTotalLength() : 0;
    if (!totalLen) return;

    const particle = _g.append('circle')
      .attr('r', 5)
      .attr('fill', color)
      .style('filter', 'url(#anv-glow)')
      .attr('opacity', 0.9)
      .attr('pointer-events', 'none');

    let start = null;
    const duration = 1800;

    function step(ts) {
      if (!start) start = ts;
      const t = Math.min((ts - start) / duration, 1);
      const pt = pathEl.getPointAtLength(t * totalLen);
      particle.attr('transform', `translate(${pt.x},${pt.y})`);
      if (t < 1) {
        requestAnimationFrame(step);
      } else {
        particle.remove();
        // Flash destination
        const tgtId = typeof edgeDef.target === 'object' ? edgeDef.target.id : edgeDef.target;
        _flashNode(tgtId);
        // Refresh inspector if this edge is open
        if (_activeInspector && _activeInspector.type === 'edge' && _activeInspector.id === edgeId) {
          _fetchAndShowEdgeInspector(edgeId);
        }
      }
    }
    requestAnimationFrame(step);
  }

  function _flashNode(nodeId) {
    if (!_g) return;
    const circle = _g.select(`.anv-node-group[data-node-id="${nodeId}"] .anv-node-circle`);
    if (circle.empty()) return;
    circle.transition().duration(200).attr('opacity', 1)
      .transition().duration(200).attr('opacity', 0.6)
      .transition().duration(200).attr('opacity', 1);
  }

  // ── Diagram controls ─────────────────────────────────────────────────────
  function _bindModalControls() {
    // Modal close — disconnect SSE on close
    const modalCloseBtn = document.getElementById('anv-modal-close-btn');
    if (modalCloseBtn) {
      modalCloseBtn.addEventListener('click', () => window.closeAgentNetwork());
    }

    // Run Now
    const runBtn = document.getElementById('anv-run-now');
    if (runBtn) {
      runBtn.addEventListener('click', async () => {
        runBtn.disabled = true; runBtn.textContent = 'Running…';
        try {
          await api('/run', { method: 'POST' });
          await loadNetwork(null);
        } catch (e) {
          _showToast('Error: ' + e.message);
        } finally {
          setTimeout(() => { runBtn.disabled = false; runBtn.textContent = 'Run Now'; }, 60000);
        }
      });
    }

    // Zoom controls
    const zoomIn = document.getElementById('anv-zoom-in');
    const zoomOut = document.getElementById('anv-zoom-out');
    const zoomFit = document.getElementById('anv-zoom-fit');
    if (zoomIn) zoomIn.addEventListener('click', () => _adjustZoom(1.25));
    if (zoomOut) zoomOut.addEventListener('click', () => _adjustZoom(0.8));
    if (zoomFit) zoomFit.addEventListener('click', () => _zoomToFit());

    // Reset layout
    const resetBtn = document.getElementById('anv-reset-layout');
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        _nodePositionCache = {};
        if (_networkData) _renderDiagram(_networkData.nodes, _networkData.edges);
      });
    }

    // View toggle
    const btnNet  = document.getElementById('anv-view-network');
    const btnSwim = document.getElementById('anv-view-swimlane');
    if (btnNet)  btnNet.addEventListener('click', () => _switchView('network'));
    if (btnSwim) btnSwim.addEventListener('click', () => _switchView('swimlane'));

    // Node editor close button
    const nedClose = document.getElementById('ned-close-btn');
    if (nedClose) nedClose.addEventListener('click', _closeNodeEditor);

    // Share
    const shareBtn = document.getElementById('anv-share');
    if (shareBtn) {
      shareBtn.addEventListener('click', async () => {
        try {
          const data = await api('/share', { method: 'POST', body: JSON.stringify({ cycle_id: _currentCycleId }) });
          const url = window.location.origin + window.location.pathname.split('/simulations/')[0] + '/share/layer6/' + data.token;
          prompt('Share URL (30 days):', url);
        } catch (e) { _showToast('Error: ' + e.message); }
      });
    }

    // Close inspector on canvas click
    const canvas = document.getElementById('anv-canvas');
    if (canvas) canvas.addEventListener('click', () => _closeInspector());

    // Inspector close button
    const inspClose = document.getElementById('anv-inspector-close');
    if (inspClose) inspClose.addEventListener('click', () => _closeInspector());

    // Live banner "Go live" button
    const goLive = document.getElementById('anv-go-live');
    if (goLive) goLive.addEventListener('click', () => { loadNetwork(null); _updateLiveBanner(false); });

    // Diff mode
    const diffBtn = document.getElementById('anv-diff-btn');
    if (diffBtn) diffBtn.addEventListener('click', _openDiffMode);

    // Close diff
    const diffClose = document.getElementById('anv-diff-close');
    if (diffClose) diffClose.addEventListener('click', () => {
      const panel = document.getElementById('anv-diff-panel');
      if (panel) panel.classList.add('hidden');
    });

    // Diff compare
    const diffCompare = document.getElementById('anv-diff-compare');
    if (diffCompare) diffCompare.addEventListener('click', _runDiff);
  }

  function _bindKeyboard() {
    document.addEventListener('keydown', ev => {
      const modal = document.getElementById('anv-modal');
      if (!modal || !modal.classList.contains('open')) return;

      if (ev.key === 'Escape') {
        const insp = document.getElementById('anv-inspector');
        if (insp && insp.classList.contains('open')) {
          _closeInspector();
        } else {
          document.getElementById('anv-modal')?.classList.remove('open');
          if (_sseSource) { _sseSource.close(); _sseSource = null; }
        }
        return;
      }
      if (ev.key === 'Backspace' && _activeInspector) {
        ev.preventDefault();
        if (_breadcrumb.length > 1) window._anvBreadcrumbNav(_breadcrumb.length - 2);
        return;
      }
      if (!_g) return;
      const panBy = 30;
      const transforms = { ArrowLeft: [-panBy, 0], ArrowRight: [panBy, 0], ArrowUp: [0, -panBy], ArrowDown: [0, panBy] };
      if (transforms[ev.key]) {
        const [dx, dy] = transforms[ev.key];
        _svg.transition().duration(100).call(_zoom.translateBy, dx, dy);
        return;
      }
      if (ev.key === 'v' || ev.key === 'V') {
        _switchView(_activeView === 'network' ? 'swimlane' : 'network');
        return;
      }
      if (ev.key === '+') { _adjustZoom(1.1); return; }
      if (ev.key === '-') { _adjustZoom(0.9); return; }
      if (ev.key === '0') { _zoomToFit(); return; }
    });
  }

  function _adjustZoom(factor) {
    if (!_svg || !_zoom) return;
    _svg.transition().duration(200).call(_zoom.scaleBy, factor);
  }

  function _zoomToFit() {
    if (!_svg || !_zoom) return;
    _svg.transition().duration(400).call(_zoom.transform, d3.zoomIdentity);
  }

  // ── View switcher ─────────────────────────────────────────────────────────
  function _switchView(view) {
    if (view === _activeView) return;
    _activeView = view;

    const canvas = document.getElementById('anv-canvas');
    const slCanvas = document.getElementById('anv-sl-canvas');
    const tierFilter = document.getElementById('anv-tier-filter');
    const btnNet = document.getElementById('anv-view-network');
    const btnSwim = document.getElementById('anv-view-swimlane');

    if (view === 'swimlane') {
      if (canvas) canvas.style.display = 'none';
      if (slCanvas) slCanvas.style.display = 'block';
      if (tierFilter) tierFilter.style.display = 'none';
      if (btnNet)  btnNet.classList.remove('active');
      if (btnSwim) btnSwim.classList.add('active');
      _closeInspector();
      _closeNodeEditor();
      if (!_slInitialized && window.SL) {
        _slInitialized = true;
        window.SL.render('anv-sl-canvas', _apiBase, { onNodeClick: _openNodeEditor });
      } else if (window.SL) {
        window.SL.refreshStatus();
      }
    } else {
      if (canvas) canvas.style.display = '';
      if (slCanvas) slCanvas.style.display = 'none';
      if (tierFilter && !_readOnly) tierFilter.style.display = '';
      if (btnNet)  btnNet.classList.add('active');
      if (btnSwim) btnSwim.classList.remove('active');
      _closeNodeEditor();
    }
  }

  // ── Live Node Editor ──────────────────────────────────────────────────────
  function _openNodeEditor(agent, dagNode) {
    const panel = document.getElementById('anv-node-editor');
    if (!panel) return;

    _nedActionId = (dagNode && dagNode.action_id) || null;

    // Header
    const nameEl = document.getElementById('ned-action-name');
    const badgeEl = document.getElementById('ned-layer-badge');
    if (nameEl) nameEl.textContent = agent.label;
    if (badgeEl) {
      const colors = { 1:'#534AB7', 2:'#0F6E56', 3:'#D85A30', 4:'#BA7517', 5:'#185FA5' };
      const labels = { 1:'L1 Active', 2:'L2 Leveraged', 3:'L3 Productized', 4:'L4 Automated', 5:'L5 Wealth' };
      badgeEl.textContent = labels[agent.layer] || ('L' + agent.layer);
      badgeEl.style.background = (colors[agent.layer] || '#334155') + '33';
      badgeEl.style.color = colors[agent.layer] || '#94a3b8';
    }

    _buildNodeEditorBody(agent, dagNode);

    panel.classList.add('open');

    // Run button
    const runBtn = document.getElementById('ned-run-btn');
    if (runBtn) {
      runBtn.disabled = !_nedActionId;
      runBtn.onclick = function () { _submitRerun(agent, dagNode); };
    }
  }

  function _buildNodeEditorBody(agent, dagNode) {
    const body = document.getElementById('ned-body');
    if (!body) return;

    const form = (dagNode && dagNode.form) || [];
    const userInputs = (dagNode && dagNode.user_inputs) || {};
    const hasArtifact = dagNode && dagNode.has_artifact;
    const cacheKey = agent.id;
    const cached = _nedCache[cacheKey] || {};

    let html = '';

    if (form.length === 0) {
      html += '<div class="ned-empty-state">No configurable inputs for this action.</div>';
    } else {
      html += '<div class="ned-section-label">Inputs</div>';
      form.forEach(f => {
        const val = (cacheKey in _nedCache && f.key in cached)
          ? cached[f.key]
          : (userInputs[f.key] || '');
        const hasVal = val && String(val).trim().length > 0;
        const confClass = hasVal ? 'conf-high' : (f.required ? 'conf-low' : 'conf-none');
        const isLong = String(val).length > 80;
        const tag = isLong ? 'textarea' : 'input';
        const extra = tag === 'textarea' ? ' rows="3"' : ' type="text"';
        html += `<div class="ned-field">
          <div class="ned-field-label">
            ${f.required ? '<span class="ned-required-dot"></span>' : ''}
            ${esc(f.label)}
          </div>
          <${tag} class="ned-input ${confClass}"
            data-key="${esc(f.key)}"
            placeholder="${esc(f.label)}"
            ${extra}>${esc(val)}</${tag}>
        </div>`;
      });
    }

    if (hasArtifact) {
      html += '<div class="ned-section-label" style="margin-top:0.9rem">Previous artifact</div>';
      html += `<div style="color:#475569;font-size:0.68rem;margin-bottom:0.35rem">
        <span class="ned-version-badge">v1</span>Re-run will archive this and generate a new version.
      </div>`;
    }

    if (!_nedActionId) {
      html += `<div class="ned-stale-warning" style="margin-top:0.5rem">
        This action has not been run yet. Create it via the simulation layer page first.
      </div>`;
    }

    body.innerHTML = html;

    // Cache unsaved edits on input
    body.querySelectorAll('.ned-input').forEach(inp => {
      inp.addEventListener('input', function () {
        if (!_nedCache[cacheKey]) _nedCache[cacheKey] = {};
        _nedCache[cacheKey][this.dataset.key] = this.value;
      });
    });
  }

  async function _submitRerun(agent, dagNode) {
    if (!_nedActionId) return;
    const runBtn = document.getElementById('ned-run-btn');
    const statusEl = document.getElementById('ned-run-status');

    // Collect current inputs from the form
    const body = document.getElementById('ned-body');
    const inputs = {};
    if (body) {
      body.querySelectorAll('.ned-input').forEach(inp => {
        if (inp.dataset.key) inputs[inp.dataset.key] = inp.value;
      });
    }

    if (runBtn) runBtn.disabled = true;
    if (statusEl) statusEl.textContent = 'Running…';

    // 90-second timeout
    _nedRunTimer = setTimeout(function () {
      if (runBtn) runBtn.disabled = false;
      if (statusEl) statusEl.textContent = 'Timed out — try again';
      _showToast('Agent run timed out after 90 s.');
    }, 90000);

    try {
      const result = await api('/actions/' + _nedActionId + '/rerun', {
        method: 'POST',
        body: JSON.stringify({ user_inputs: inputs }),
      });
      clearTimeout(_nedRunTimer);
      if (statusEl) statusEl.textContent = '✓ Queued';
      if (runBtn) runBtn.disabled = false;
      // Clear session cache for this action
      delete _nedCache[agent.id];
      // Refresh swimlane status
      if (window.SL) { setTimeout(() => window.SL.refreshStatus(), 500); }
      _showToast('Agent re-run queued — artifact will appear when complete.');
    } catch (e) {
      clearTimeout(_nedRunTimer);
      if (runBtn) runBtn.disabled = false;
      if (statusEl) statusEl.textContent = 'Error';
      _showToast('Run failed: ' + e.message);
    }
  }

  function _closeNodeEditor() {
    const panel = document.getElementById('anv-node-editor');
    if (panel) panel.classList.remove('open');
    _nedActionId = null;
    clearTimeout(_nedRunTimer);
  }

  // ── Diff mode ────────────────────────────────────────────────────────────
  async function _openDiffMode() {
    const panel = document.getElementById('anv-diff-panel');
    if (!panel) return;
    panel.classList.remove('hidden');

    try {
      const cycles = await api('/cycles');
      const selA = document.getElementById('anv-diff-a');
      const selB = document.getElementById('anv-diff-b');
      if (!selA || !selB) return;
      const opts = cycles.map(c =>
        `<option value="${esc(c.id)}">Cycle ${esc(String(c.cycle_number))} (${esc((c.cycle_started_at||'').slice(0,10))})</option>`
      ).join('');
      selA.innerHTML = opts;
      selB.innerHTML = opts;
      if (cycles.length > 1) selB.selectedIndex = 1;
    } catch (e) { _showToast('Could not load cycles: ' + e.message); }
  }

  async function _runDiff() {
    const a = document.getElementById('anv-diff-a')?.value;
    const b = document.getElementById('anv-diff-b')?.value;
    if (!a || !b || a === b) { _showToast('Select two different cycles.'); return; }
    try {
      const data = await api('/cycles/compare?a=' + a + '&b=' + b);
      _renderDiffResult(data);
    } catch (e) { _showToast('Compare error: ' + e.message); }
  }

  function _renderDiffResult(data) {
    const el = document.getElementById('anv-diff-result');
    if (!el) return;
    const A = data.a, B = data.b;
    const layers = ['L1','L2','L3','L4','L5'];
    el.innerHTML = `<table class="insp-payload-table" style="margin-top:.75rem">
      <thead><tr><th>Metric</th><th>Cycle ${esc(String(A.cycle_number))} (${esc(A.phase)})</th><th>Cycle ${esc(String(B.cycle_number))} (${esc(B.phase)})</th><th>Δ</th></tr></thead>
      <tbody>
        <tr><td class="insp-kv-key">Total dispatched</td><td>${esc(String(A.actions_dispatched))}</td><td>${esc(String(B.actions_dispatched))}</td><td style="color:${B.actions_dispatched>=A.actions_dispatched?'#22c55e':'#ef4444'}">${B.actions_dispatched-A.actions_dispatched>=0?'+':''}${esc(String(B.actions_dispatched-A.actions_dispatched))}</td></tr>
        <tr><td class="insp-kv-key">Total escalated</td><td>${esc(String(A.actions_escalated))}</td><td>${esc(String(B.actions_escalated))}</td><td>${esc(String(B.actions_escalated-A.actions_escalated))}</td></tr>
        ${layers.map(l => {
          const ad = ((A.by_layer||{})[l]||{}).dispatched||0;
          const bd = ((B.by_layer||{})[l]||{}).dispatched||0;
          return `<tr><td class="insp-kv-key">${l} dispatched</td><td>${ad}</td><td>${bd}</td><td style="color:${bd>=ad?'#22c55e':'#ef4444'}">${bd-ad>=0?'+':''}${bd-ad}</td></tr>`;
        }).join('')}
      </tbody>
    </table>`;
  }

  // ── Toast ────────────────────────────────────────────────────────────────
  function _showToast(msg) {
    let t = document.getElementById('anv-toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'anv-toast';
      t.style.cssText = 'position:fixed;bottom:1.5rem;left:50%;transform:translateX(-50%);background:#1e293b;color:#f1f5f9;padding:.5rem 1.25rem;border-radius:8px;font-size:.82rem;z-index:9999;pointer-events:none;transition:opacity .3s';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.style.opacity = '1';
    clearTimeout(t._timer);
    t._timer = setTimeout(() => { t.style.opacity = '0'; }, 2500);
  }

  // ── External API for GCC tool chip clicks ────────────────────────────────
  window._openNodeEditorForAction = function (actionType) {
    const agent = (window.SL && window.SL.getAgentById(actionType))
      || { id: actionType, label: actionType.replaceAll('_', ' '), layer: 1, sub: '' };
    const dagNode = (window.SL && window.SL.getDagNode(actionType)) || null;
    _openNodeEditor(agent, dagNode);
  };

})();
