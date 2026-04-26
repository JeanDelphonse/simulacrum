/**
 * orchestrator_diagram.js
 * Interactive Orchestrator Cycle Diagram — Simulacrum Layer 6
 * Vanilla JS, no framework dependencies. D3 v7 optional (loaded from CDN).
 * Expects: SIM_ID, basePath, API globals from layer6.html
 */

(function () {
  'use strict';

  // ── State ────────────────────────────────────────────────────────────────
  let activeStep      = null;
  let activeCycleId   = null;
  let dagInstance     = null;
  let dagLayerFilter  = 'all';
  let sseSource       = null;

  // Layer placeholder income ranges for Score panel
  const LAYER_RANGES = {
    1: '$2k–$8k',
    2: '$3k–$12k',
    3: '$2k–$5k',
    4: '$1k–$6k',
    5: '$5k–$25k',
  };

  // DAG status colour map
  const STATUS_COLORS = {
    complete:   '#9ca3af',
    dispatched: '#0d9488',
    queued:     '#d97706',
    escalated:  '#f59e0b',
    blocked:    '#ef4444',
    rejected:   '#6b7280',
  };

  const STEP_NAMES = ['harvest', 'score', 'schedule', 'report'];

  // ── Helpers ──────────────────────────────────────────────────────────────
  function esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmtScore(n) {
    return (typeof n === 'number') ? n.toFixed(3) : '—';
  }

  async function apiFetch(path, opts) {
    opts = opts || {};
    const res = await fetch(path, {
      headers: Object.assign({'Content-Type': 'application/json'}, opts.headers || {}),
      method: opts.method || 'GET',
      body: opts.body || undefined,
    });
    if (!res.ok) {
      const text = await res.text().catch(function () { return ''; });
      let msg = res.statusText || ('HTTP ' + res.status);
      try { msg = JSON.parse(text).error || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res.json();
  }

  // ── Entry point ──────────────────────────────────────────────────────────
  window.initDiagram = function initDiagram(cycleData, allCycles) {
    setupCycleSelector(allCycles, cycleData);
    setupNodeClicks();
    setupRunNow();
    setupShare();
    setupLayerFilterButtons();
    setupModalClose();
    if (cycleData) {
      activeCycleId = cycleData.id;
      loadCycleData(cycleData);
    } else {
      updatePhaseBar(null, 0);
    }
    initSSE();
  };

  function setupModalClose() {
    var closeBtn = document.getElementById('modalClose');
    if (closeBtn) closeBtn.addEventListener('click', closeModal);

    var overlay = document.getElementById('step-detail');
    if (overlay) {
      overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closeModal();
      });
    }

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && activeStep) closeModal();
    });
  }

  // ── Cycle selector ───────────────────────────────────────────────────────
  function setupCycleSelector(allCycles, currentCycle) {
    const sel = document.getElementById('cycleSelector');
    if (!sel) return;

    // If the select already has options from Jinja, honour them; just wire the change event
    sel.addEventListener('change', function () {
      const id = sel.value;
      if (id) fetchCycleDetail(id);
    });
  }

  async function fetchCycleDetail(cycleId) {
    try {
      const data = await apiFetch(API + '/cycles/' + cycleId + '/detail');
      activeCycleId = cycleId;
      loadCycleData(data);
    } catch (e) {
      console.error('fetchCycleDetail:', e);
    }
  }

  // ── Load cycle data ──────────────────────────────────────────────────────
  function loadCycleData(cycleData) {
    if (!cycleData) return;
    activeCycleId = cycleData.id;

    updatePhaseBadge(cycleData.phase);
    updatePhaseBar(cycleData.phase, cycleData.cycle_number);
    updateNodeStates(cycleData);
    populateHarvestPanel(cycleData);
    populateScorePanel(cycleData);
    populateReportPanel(cycleData);

    // DAG — fetch from /dag endpoint and render
    apiFetch(API + '/dag').then(function (dagData) {
      const queue = cycleData.action_queue || cycleData.actions || [];
      renderDAG(dagData.nodes || [], queue);
    }).catch(function () {
      // Render with action_queue as fallback nodes
      const queue = cycleData.action_queue || cycleData.actions || [];
      renderDAG([], queue);
    });
  }

  // ── Phase badge ──────────────────────────────────────────────────────────
  function updatePhaseBadge(phase) {
    const badge = document.getElementById('phaseBadgeDiag');
    if (!badge) return;
    badge.className = 'orch-phase-badge';
    if (phase === 'explore') {
      badge.classList.add('orch-phase-explore');
      badge.textContent = 'Explore';
    } else if (phase === 'exploit') {
      badge.classList.add('orch-phase-exploit');
      badge.textContent = 'Exploit';
    } else {
      badge.classList.add('orch-phase-unknown');
      badge.textContent = phase ? phase : '—';
    }
  }

  // ── Node state management ────────────────────────────────────────────────
  // states: 'idle' | 'active' | 'running' | 'complete' | 'escalated' | 'error'
  function setNodeState(stepName, state) {
    const group = document.getElementById('step-' + stepName);
    if (!group) return;

    // Remove all state classes
    ['active', 'running', 'complete', 'escalated', 'error', 'idle'].forEach(function (cls) {
      group.classList.remove(cls);
    });
    group.classList.add(state);

    const badge  = document.getElementById('badge-' + stepName);
    const check  = document.getElementById('check-' + stepName);

    if (badge) badge.style.display  = 'none';
    if (check) check.style.display  = 'none';

    if (state === 'complete') {
      if (badge) { badge.style.display = ''; badge.setAttribute('class', 'check-badge'); }
      if (check) check.style.display = '';
    } else if (state === 'escalated') {
      if (badge) { badge.style.display = ''; badge.setAttribute('class', 'escalated-badge'); }
    }
  }

  function updateNodeStates(cycleData) {
    const queue = cycleData.action_queue || cycleData.actions || [];
    const isComplete = !!cycleData.cycle_completed_at;
    const hasEscalated = queue.some(function (a) { return a.status === 'escalated'; });

    if (isComplete) {
      setNodeState('harvest',  'complete');
      setNodeState('score',    'complete');
      setNodeState('schedule', hasEscalated ? 'escalated' : 'complete');
      setNodeState('report',   'complete');
    } else if (cycleData.actions_dispatched > 0 || queue.length > 0) {
      setNodeState('harvest',  'complete');
      setNodeState('score',    'complete');
      setNodeState('schedule', hasEscalated ? 'escalated' : 'active');
      setNodeState('report',   'idle');
    } else {
      STEP_NAMES.forEach(function (s) { setNodeState(s, 'idle'); });
    }
  }

  // ── Node click / keyboard ────────────────────────────────────────────────
  function setupNodeClicks() {
    STEP_NAMES.forEach(function (name) {
      const group = document.getElementById('step-' + name);
      if (!group) return;

      group.addEventListener('click', function () {
        togglePanel(name);
      });

      group.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          togglePanel(name);
        }
      });
    });
  }

  var STEP_TITLES = {
    harvest:  'Harvest — Connector Status',
    score:    'Score — Ranked Income Streams',
    schedule: 'Schedule — Action DAG',
    report:   'Report — Cycle Summary',
  };

  function togglePanel(stepName) {
    if (activeStep === stepName) {
      closeModal();
      return;
    }
    activeStep = stepName;
    openModal(stepName);
  }

  function openModal(stepName) {
    var overlay = document.getElementById('step-detail');
    if (!overlay) return;

    var titleEl = document.getElementById('modal-title');
    if (titleEl) titleEl.textContent = STEP_TITLES[stepName] || stepName;

    STEP_NAMES.forEach(function (name) {
      var panel = document.getElementById('panel-' + name);
      if (panel) panel.classList.toggle('orch-panel-visible', name === stepName);
    });

    overlay.classList.add('orch-modal-open');

    if (stepName === 'schedule' && dagInstance && dagInstance.restart) {
      setTimeout(function () { dagInstance.restart(); }, 50);
    }
  }

  function closeModal() {
    var overlay = document.getElementById('step-detail');
    if (overlay) overlay.classList.remove('orch-modal-open');
    collapseAllPanels();
    activeStep = null;
  }

  function collapseAllPanels() {
    STEP_NAMES.forEach(function (name) {
      var panel = document.getElementById('panel-' + name);
      if (panel) panel.classList.remove('orch-panel-visible');
    });
  }

  // ── Harvest panel ────────────────────────────────────────────────────────
  function populateHarvestPanel(cycleData) {
    const queue = cycleData.action_queue || cycleData.actions || [];
    const dispatched = queue.filter(function (a) { return a.status === 'dispatched' || a.status === 'complete'; }).length;
    const escalated  = queue.filter(function (a) { return a.status === 'escalated'; }).length;

    // Connector grid — show simulated rows (integrations not yet live)
    const connectors = [
      { name: 'Email',        prev: dispatched, cur: dispatched, status: 'live' },
      { name: 'ConvertKit',   prev: 0,          cur: 0,          status: 'live' },
      { name: 'Cal.com',      prev: 0,          cur: 0,          status: 'live' },
      { name: 'LinkedIn',     prev: 0,          cur: 0,          status: 'live' },
      { name: 'Stripe',       prev: 0,          cur: 0,          status: 'live' },
    ];

    const tbody = document.getElementById('harvest-grid-body');
    if (!tbody) return;

    tbody.innerHTML = connectors.map(function (c) {
      const delta = c.cur - c.prev;
      const deltaStr = delta > 0 ? ('+' + delta) : (delta < 0 ? String(delta) : '—');
      const statusCls = 'connector-status-' + c.status;
      const statusLabel = c.status === 'live' ? 'Live' : c.status === 'stale' ? 'Stale' : 'Error';
      return '<tr>' +
        '<td>' + esc(c.name) + '</td>' +
        '<td>' + c.prev + '</td>' +
        '<td>' + c.cur  + '</td>' +
        '<td>' + deltaStr + '</td>' +
        '<td class="' + statusCls + '">' + statusLabel + '</td>' +
        '</tr>';
    }).join('');

    const reasoning = document.getElementById('harvest-reasoning');
    if (reasoning) {
      reasoning.textContent = cycleData.orchestrator_reasoning || '— No reasoning recorded for this cycle.';
    }
  }

  // ── Score panel ──────────────────────────────────────────────────────────
  function populateScorePanel(cycleData) {
    const queue = cycleData.action_queue || cycleData.actions || [];
    const phase = cycleData.phase || 'explore';

    // Phase badge in score panel
    const phaseBadgeWrap = document.getElementById('score-phase-badge-wrap');
    if (phaseBadgeWrap) {
      const cls = phase === 'explore' ? 'orch-phase-explore' : 'orch-phase-exploit';
      const monthCount = cycleData.cycle_number || 1;
      phaseBadgeWrap.innerHTML =
        '<span class="orch-phase-badge ' + cls + '">' +
        esc(phase.charAt(0).toUpperCase() + phase.slice(1)) +
        ' — Month ' + monthCount + '</span>';
    }

    const tbody = document.getElementById('score-table-body');
    if (!tbody) return;

    if (!queue.length) {
      tbody.innerHTML = '<tr class="placeholder-row"><td colspan="7">No scored actions for this cycle.</td></tr>';
      return;
    }

    // Group by source_layer, pick top-scored action per layer
    const byLayer = {};
    queue.forEach(function (a) {
      const layer = a.source_layer || 0;
      if (!byLayer[layer] || (a.priority_score || 0) > (byLayer[layer].priority_score || 0)) {
        byLayer[layer] = a;
      }
    });

    const rows = Object.keys(byLayer).sort().map(function (layer) {
      const a = byLayer[layer];
      const score = typeof a.priority_score === 'number' ? a.priority_score : 0;
      const confPct = Math.min(100, Math.round(score * 100));
      const trend = score > 0.7 ? '↑' : score > 0.4 ? '→' : '↓';
      const range = LAYER_RANGES[layer] || '—';
      const posteriorMean = fmtScore(score);

      return '<tr>' +
        '<td>' + esc((a.action_type || '').replace(/_/g, ' ')) + '</td>' +
        '<td>L' + esc(String(layer)) + '</td>' +
        '<td>' + range + '</td>' +
        '<td>' + posteriorMean + '</td>' +
        '<td>' +
          '<div class="conf-bar-wrap"><div class="conf-bar" style="width:' + confPct + '%"></div></div>' +
        '</td>' +
        '<td>' + fmtScore(score) + '</td>' +
        '<td>' + trend + '</td>' +
        '</tr>';
    });

    tbody.innerHTML = rows.join('');
  }

  // ── Schedule panel / DAG ─────────────────────────────────────────────────
  function setupLayerFilterButtons() {
    const buttons = document.querySelectorAll('.layer-btn');
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        buttons.forEach(function (b) { b.classList.remove('layer-btn-active'); });
        btn.classList.add('layer-btn-active');
        dagLayerFilter = btn.dataset.layer || 'all';
        applyDagFilter(dagLayerFilter);
      });
    });

    const resetBtn = document.getElementById('dagResetView');
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        resetDagView();
      });
    }
  }

  function applyDagFilter(layer) {
    if (!dagInstance) return;
    const svg = document.getElementById('dagSvg');
    if (!svg) return;

    const nodeGroups = svg.querySelectorAll('.dag-node-group');
    nodeGroups.forEach(function (g) {
      const nodeLayer = g.dataset.layer;
      if (layer === 'all' || nodeLayer === String(layer)) {
        g.style.opacity = '1';
      } else {
        g.style.opacity = '0.1';
      }
    });
  }

  function resetDagView() {
    if (dagInstance && dagInstance.resetTransform) {
      dagInstance.resetTransform();
    } else {
      // Fallback: re-select all
      const allBtn = document.getElementById('dagFilterAll');
      if (allBtn) allBtn.click();
    }
  }

  function renderDAG(nodes, actionQueue) {
    const container = document.getElementById('dagContainer');
    const svgEl     = document.getElementById('dagSvg');
    const fallback  = document.getElementById('dagFallback');
    const tooltip   = document.getElementById('dagTooltip');

    if (!container || !svgEl) return;

    // Merge: prefer nodes from API, fall back to action_queue
    let allNodes = [];
    if (nodes && nodes.length > 0) {
      allNodes = nodes.map(function (n) {
        return {
          id:     n.id,
          label:  n.label || n.id,
          layer:  n.layer || 0,
          status: n.status || 'queued',
          prerequisites: n.prerequisites || [],
          priority_score: n.priority_score || 0,
        };
      });
    } else if (actionQueue && actionQueue.length > 0) {
      allNodes = actionQueue.map(function (a) {
        return {
          id:     a.id,
          label:  (a.action_type || a.id).replace(/_/g, ' '),
          layer:  a.source_layer || 0,
          status: a.status || 'queued',
          prerequisites: a.dependency_ids || [],
          priority_score: a.priority_score || 0,
        };
      });
    }

    populateEscalationList(actionQueue);

    if (typeof d3 === 'undefined') {
      svgEl.style.display = 'none';
      fallback.style.display = 'block';
      renderDagFallback(fallback, allNodes);
      dagInstance = null;
      return;
    }

    fallback.style.display = 'none';
    svgEl.style.display    = '';

    // Clear previous render
    d3.select(svgEl).selectAll('*').remove();

    const width  = container.clientWidth  || 600;
    const height = container.clientHeight || 260;

    const svg = d3.select(svgEl)
      .attr('width', width)
      .attr('height', height);

    // Defs: arrowhead
    const defs = svg.append('defs');
    defs.append('marker')
      .attr('id', 'dag-arrow')
      .attr('viewBox', '0 0 8 8')
      .attr('refX', 14)
      .attr('refY', 3)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,0 L0,6 L8,3 z')
      .attr('fill', '#94a3b8');

    // Container group for zoom/pan
    const g = svg.append('g').attr('class', 'dag-root');

    // Build link data from prerequisites
    const nodeById = {};
    allNodes.forEach(function (n) { nodeById[n.id] = n; });

    const links = [];
    allNodes.forEach(function (n) {
      (n.prerequisites || []).forEach(function (prereqId) {
        if (nodeById[prereqId]) {
          links.push({ source: prereqId, target: n.id });
        }
      });
    });

    // Swim lanes: layers 1-5, assign y based on layer
    const layerCount = 5;
    const laneHeight = height / layerCount;

    function yForLayer(layer) {
      const l = Math.min(Math.max(layer || 1, 1), 5);
      return laneHeight * (l - 0.5);
    }

    // Initialise node positions
    allNodes.forEach(function (n) {
      n.x = Math.random() * width;
      n.y = yForLayer(n.layer);
    });

    // D3 force simulation
    const simulation = d3.forceSimulation(allNodes)
      .force('link', d3.forceLink(links).id(function (d) { return d.id; }).distance(90).strength(0.8))
      .force('charge', d3.forceManyBody().strength(-120))
      .force('x', d3.forceX(width / 2).strength(0.05))
      .force('y', d3.forceY(function (d) { return yForLayer(d.layer); }).strength(0.6))
      .force('collision', d3.forceCollide(20));

    // Lane dividers
    for (let i = 1; i < layerCount; i++) {
      g.append('line')
        .attr('x1', 0).attr('y1', laneHeight * i)
        .attr('x2', width).attr('y2', laneHeight * i)
        .attr('stroke', '#e2e8f0')
        .attr('stroke-width', 1)
        .attr('stroke-dasharray', '4,4');
    }

    // Lane labels
    for (let i = 1; i <= layerCount; i++) {
      g.append('text')
        .attr('x', 6)
        .attr('y', laneHeight * (i - 0.5) + 4)
        .attr('fill', '#94a3b8')
        .attr('font-size', '9px')
        .attr('font-family', 'system-ui, sans-serif')
        .text('L' + i);
    }

    // Links
    const linkSel = g.append('g').attr('class', 'dag-links')
      .selectAll('line')
      .data(links)
      .enter()
      .append('line')
      .attr('stroke', '#cbd5e1')
      .attr('stroke-width', 1.5)
      .attr('marker-end', 'url(#dag-arrow)');

    // Nodes
    const nodeGroupSel = g.append('g').attr('class', 'dag-nodes')
      .selectAll('g')
      .data(allNodes)
      .enter()
      .append('g')
      .attr('class', 'dag-node-group')
      .attr('data-layer', function (d) { return d.layer; })
      .style('cursor', 'pointer')
      .call(d3.drag()
        .on('start', function (event, d) {
          if (!event.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        })
        .on('drag', function (event, d) {
          d.fx = event.x; d.fy = event.y;
        })
        .on('end', function (event, d) {
          if (!event.active) simulation.alphaTarget(0);
          d.fx = null; d.fy = null;
        })
      );

    nodeGroupSel.append('circle')
      .attr('r', function (d) { return (d.priority_score || 0) > 0.6 ? 12 : 8; })
      .attr('fill', function (d) { return STATUS_COLORS[d.status] || '#94a3b8'; })
      .attr('stroke', '#ffffff')
      .attr('stroke-width', 1.5);

    // Tooltip interaction
    nodeGroupSel
      .on('mouseenter', function (event, d) {
        if (!tooltip) return;
        tooltip.innerHTML =
          '<strong>' + esc(d.label) + '</strong><br>' +
          'Layer: ' + d.layer + ' &nbsp;|&nbsp; Status: ' + esc(d.status) +
          (d.priority_score ? '<br>Score: ' + fmtScore(d.priority_score) : '');
        tooltip.style.opacity = '1';
        tooltip.style.left = (event.clientX + 12) + 'px';
        tooltip.style.top  = (event.clientY - 10) + 'px';
      })
      .on('mousemove', function (event) {
        if (!tooltip) return;
        tooltip.style.left = (event.clientX + 12) + 'px';
        tooltip.style.top  = (event.clientY - 10) + 'px';
      })
      .on('mouseleave', function () {
        if (tooltip) tooltip.style.opacity = '0';
      });

    // Zoom / pan
    const zoom = d3.zoom()
      .scaleExtent([0.3, 3])
      .on('zoom', function (event) {
        g.attr('transform', event.transform);
      });
    svg.call(zoom);

    simulation.on('tick', function () {
      linkSel
        .attr('x1', function (d) { return d.source.x; })
        .attr('y1', function (d) { return d.source.y; })
        .attr('x2', function (d) { return d.target.x; })
        .attr('y2', function (d) { return d.target.y; });

      nodeGroupSel.attr('transform', function (d) {
        return 'translate(' + d.x + ',' + d.y + ')';
      });
    });

    // Store instance for filter / reset
    dagInstance = {
      simulation: simulation,
      restart: function () { simulation.alphaTarget(0.1).restart(); },
      resetTransform: function () {
        svg.transition().duration(400).call(zoom.transform, d3.zoomIdentity);
      },
    };

    // Apply current filter
    if (dagLayerFilter !== 'all') {
      applyDagFilter(dagLayerFilter);
    }
  }

  function renderDagFallback(container, nodes) {
    if (!nodes.length) {
      container.innerHTML = '<p style="color:var(--muted)">No actions in this cycle.</p>';
      return;
    }
    container.innerHTML =
      '<ul style="list-style:none;padding:0;margin:0">' +
      nodes.map(function (n) {
        const color = STATUS_COLORS[n.status] || '#94a3b8';
        return '<li style="padding:0.35rem 0;display:flex;align-items:center;gap:0.5rem">' +
          '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + color + '"></span>' +
          '<span>' + esc(n.label) + '</span>' +
          '<span style="color:var(--muted);font-size:0.78rem">[L' + n.layer + ' · ' + esc(n.status) + ']</span>' +
          '</li>';
      }).join('') +
      '</ul>';
  }

  function populateEscalationList(actionQueue) {
    const list  = document.getElementById('escalation-list');
    const label = document.getElementById('escalation-list-label');
    if (!list) return;

    const escalated = (actionQueue || []).filter(function (a) { return a.status === 'escalated'; });

    if (!escalated.length) {
      list.innerHTML = '';
      if (label) label.style.display = 'none';
      return;
    }

    if (label) label.style.display = '';
    list.innerHTML =
      '<table class="escalation-table">' +
      '<thead><tr><th>Action</th><th>Layer</th><th>Reason</th></tr></thead>' +
      '<tbody>' +
      escalated.map(function (a) {
        return '<tr>' +
          '<td>' + esc((a.action_type || a.id || '').replace(/_/g, ' ')) + '</td>' +
          '<td>L' + esc(String(a.source_layer || '?')) + '</td>' +
          '<td>' + esc(a.escalation_reason || '—') + '</td>' +
          '</tr>';
      }).join('') +
      '</tbody></table>';
  }

  // ── Report panel ─────────────────────────────────────────────────────────
  function populateReportPanel(cycleData) {
    const queue = cycleData.action_queue || cycleData.actions || [];
    const reasoning = cycleData.orchestrator_reasoning || '';

    // Recalibration banner
    const banner = document.getElementById('recalibration-banner');
    if (banner) {
      banner.style.display = /calibrat/i.test(reasoning) ? '' : 'none';
    }

    // Section 1: Actions completed
    const actionsBody = document.getElementById('report-actions-body');
    if (actionsBody) {
      const done = queue.filter(function (a) {
        return a.status === 'dispatched' || a.status === 'complete';
      });
      if (done.length) {
        actionsBody.innerHTML =
          '<ul>' +
          done.map(function (a) {
            return '<li>' + esc((a.action_type || a.id || '').replace(/_/g, ' ')) +
              (a.outcome_summary ? ' — ' + esc(a.outcome_summary) : '') +
              '</li>';
          }).join('') +
          '</ul>';
      } else {
        actionsBody.innerHTML = '<em style="color:var(--muted)">No dispatched actions in this cycle.</em>';
      }
    }

    // Section 3: Projection vs Reality
    const projBody = document.getElementById('report-projection-body');
    if (projBody) {
      const scored     = cycleData.actions_scored     || 0;
      const dispatched = cycleData.actions_dispatched || 0;
      const escalated  = cycleData.actions_escalated  || 0;
      const rejected   = scored - dispatched - escalated;
      projBody.innerHTML =
        '<ul>' +
        '<li>Actions scored: <strong>' + scored     + '</strong></li>' +
        '<li>Actions dispatched: <strong>' + dispatched + '</strong></li>' +
        '<li>Actions escalated for review: <strong>' + escalated  + '</strong></li>' +
        (rejected > 0 ? '<li>Actions rejected / blocked: <strong>' + Math.max(0, rejected) + '</strong></li>' : '') +
        '</ul>';
    }

    // Section 4: Agent Recommendation — extract last sentence / paragraph from reasoning
    const recBody = document.getElementById('report-recommendation-body');
    if (recBody) {
      if (reasoning) {
        // Try to extract a recommendation sentence
        const paras = reasoning.split('\n').filter(function (p) { return p.trim(); });
        const last = paras[paras.length - 1] || reasoning;
        recBody.textContent = last;
      } else {
        recBody.innerHTML = '<em style="color:var(--muted)">No recommendation available for this cycle.</em>';
      }
    }
  }

  // ── Phase bar ────────────────────────────────────────────────────────────
  function updatePhaseBar(phase, cycleNumber) {
    const fill   = document.getElementById('phase-fill');
    const marker = document.getElementById('phase-marker');
    if (!fill || !marker) return;

    const barX    = 50;
    const barW    = 580;
    const midX    = barX + barW / 2;

    if (phase === 'exploit') {
      fill.setAttribute('width', String(barW));
      marker.setAttribute('cx', String(barX + barW));
    } else if (phase === 'explore') {
      // Progress within explore: use cycle number (assume max 6 explore cycles)
      const cycle = cycleNumber || 1;
      const progress = Math.min(1, cycle / 6);
      const fillW = Math.round((barW / 2) * progress);
      fill.setAttribute('width', String(fillW));
      const mx = barX + fillW;
      marker.setAttribute('cx', String(mx));
    } else {
      fill.setAttribute('width', '0');
      marker.setAttribute('cx', String(barX));
    }
  }

  // ── Run Now button ───────────────────────────────────────────────────────
  function setupRunNow() {
    const btn = document.getElementById('diagRunNow');
    if (!btn) return;

    btn.addEventListener('click', function () {
      btn.disabled = true;
      btn.textContent = 'Running…';
      setNodeState('harvest', 'running');

      apiFetch(API + '/run', { method: 'POST' })
        .then(function (data) {
          if (data && data.id) {
            activeCycleId = data.id;
            loadCycleData(data);
          }
        })
        .catch(function (e) {
          console.error('Run cycle error:', e);
          setNodeState('harvest', 'error');
        })
        .finally(function () {
          setTimeout(function () {
            btn.disabled = false;
            btn.textContent = 'Run Now';
          }, 3000);
        });
    });
  }

  // ── Share buttons ────────────────────────────────────────────────────────
  function setupShare() {
    function doShare() {
      if (!activeCycleId) {
        alert('No cycle selected.');
        return;
      }
      apiFetch(API + '/share', {
        method: 'POST',
        body: JSON.stringify({ cycle_id: activeCycleId }),
      })
        .then(function (data) {
          const url = window.location.origin + basePath + '/share/layer6/' + data.token;
          prompt('Share this cycle report URL (expires ' + (data.expires_at || '') + '):', url);
        })
        .catch(function (e) {
          alert('Share error: ' + e.message);
        });
    }

    const headerShareBtn = document.getElementById('diagShare');
    if (headerShareBtn) headerShareBtn.addEventListener('click', doShare);

    const reportShareBtn = document.getElementById('shareBtn');
    if (reportShareBtn) reportShareBtn.addEventListener('click', doShare);
  }

  // ── SSE ──────────────────────────────────────────────────────────────────
  function initSSE() {
    if (typeof EventSource === 'undefined') return;
    if (sseSource) { sseSource.close(); sseSource = null; }

    sseSource = new EventSource(API + '/stream');

    sseSource.addEventListener('cycle_complete', function (e) {
      try {
        const data = JSON.parse(e.data);
        const cycle = data.cycle || data;
        loadCycleData(cycle);
        // Pulse all 4 steps briefly
        STEP_NAMES.forEach(function (name) {
          setNodeState(name, 'running');
          setTimeout(function () { setNodeState(name, 'complete'); }, 1200);
        });
        // Update cycle selector
        addCycleToSelector(cycle);
      } catch (_) {}
    });

    sseSource.addEventListener('step_running', function (e) {
      try {
        const data = JSON.parse(e.data);
        const stepName = (data.step || '').toLowerCase();
        if (STEP_NAMES.indexOf(stepName) !== -1) {
          setNodeState(stepName, 'running');
        }
      } catch (_) {}
    });

    sseSource.onerror = function () {
      // Browser will attempt auto-reconnect for EventSource
    };
  }

  function addCycleToSelector(cycle) {
    const sel = document.getElementById('cycleSelector');
    if (!sel) return;

    // Check if option already exists
    const existing = sel.querySelector('option[value="' + cycle.id + '"]');
    if (existing) return;

    const date = cycle.cycle_started_at ? cycle.cycle_started_at.slice(0, 10) : '';
    const opt  = document.createElement('option');
    opt.value       = cycle.id;
    opt.textContent = 'Cycle ' + (cycle.cycle_number || '?') + ' — ' + date;
    sel.insertBefore(opt, sel.firstChild);
    sel.value = cycle.id;
    activeCycleId = cycle.id;
  }

})();
