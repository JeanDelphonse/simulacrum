/**
 * swimlane.js — Layer 1–5 Agent Action Swimlane Diagram
 * SIM-PRD-VIZ-002 §9 — FR-ANV-15 through FR-ANV-22
 *
 * Only agents that are eligible or complete for the current simulation are shown
 * (status !== 'blocked'). The dag endpoint drives which nodes appear.
 */
(function () {
  'use strict';

  // ── Layer palette ──────────────────────────────────────────────────────────
  const LAYER_COLORS = { 1: '#534AB7', 2: '#0F6E56', 3: '#D85A30', 4: '#BA7517', 5: '#185FA5' };
  const LAYER_LABELS = {
    1: 'L1 · Active Income',
    2: 'L2 · Leveraged',
    3: 'L3 · Productized',
    4: 'L4 · Automated',
    5: 'L5 · Wealth',
  };

  // ── Dimensions ─────────────────────────────────────────────────────────────
  const NODE_W = 72, NODE_H = 42;
  const LANE_H = 110, LABEL_W = 94;

  // ── Status helpers ─────────────────────────────────────────────────────────
  function _nodeFill(status, layer) {
    const c = LAYER_COLORS[layer];
    if (status === 'complete')                         return _hexAlpha(c, 0.32);
    if (status === 'dispatched' || status === 'in_progress') return _hexAlpha(c, 0.55);
    return '#111827'; // queued / unknown
  }
  function _nodeStroke(status, layer) {
    const c = LAYER_COLORS[layer];
    if (status === 'complete' || status === 'dispatched' || status === 'in_progress') return c;
    if (status === 'queued') return _hexAlpha(c, 0.45);
    return '#1e293b';
  }
  function _nodeStrokeW(status) {
    if (status === 'dispatched' || status === 'in_progress') return 2.5;
    if (status === 'complete') return 2;
    return 1.5;
  }
  function _nodeDash(status) { return status === 'queued' ? '4 3' : 'none'; }

  function _hexAlpha(hex, a) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${a})`;
  }

  // ── Full agent catalog (49 nodes — used as the source of truth) ────────────
  const AGENTS = [
    // Layer 1 (11)
    { id: 'outreach_email',             layer: 1, label: 'Outreach Emails',    sub: '×10 personalized' },
    { id: 'cold_email_campaign',         layer: 1, label: 'Cold Email',         sub: '25-company seq' },
    { id: 'rate_card',                   layer: 1, label: 'Rate Card',          sub: 'Capability 1-pager' },
    { id: 'role_search',                 layer: 1, label: 'Role Search',        sub: 'Fractional/contract' },
    { id: 'linkedin_optimize',           layer: 1, label: 'LinkedIn Profile',   sub: 'Headline & About' },
    { id: 'booking_page',                layer: 1, label: 'Booking Page',       sub: 'Cal.com config' },
    { id: 'consulting_proposal',         layer: 1, label: 'Proposal & SOW',     sub: 'Full consulting' },
    { id: 'consulting_agreement',        layer: 1, label: 'Agreement',          sub: 'IP & payment terms' },
    { id: 'referral_network',            layer: 1, label: 'Referral Network',   sub: '×15 messages' },
    { id: 'social_proof',                layer: 1, label: 'Social Proof',       sub: 'Testimonial sys' },
    { id: 'rate_negotiation',            layer: 1, label: 'Rate Negotiation',   sub: 'Coaching script' },
    // Layer 2 (8)
    { id: 'speaking_proposals',          layer: 2, label: 'Speaking CFPs',      sub: '20 events' },
    { id: 'speaker_fee_rider',           layer: 2, label: 'Speaker Fees',       sub: 'Tiered structure' },
    { id: 'coaching_curriculum',         layer: 2, label: 'Group Coaching',     sub: 'Curriculum + CRM' },
    { id: 'corporate_training_proposal', layer: 2, label: 'Corp Training',      sub: '25 companies' },
    { id: 'workshop_content',            layer: 2, label: 'Workshop Guide',     sub: 'Facilitator guide' },
    { id: 'waitlist_landing_page',       layer: 2, label: 'Waitlist Page',      sub: '+email sequence' },
    { id: 'alumni_reactivation',         layer: 2, label: 'Alumni Reactivation', sub: 'Re-enrollment' },
    { id: 'workshop_roi',                layer: 2, label: 'Workshop ROI',       sub: 'ROI calculator' },
    // Layer 3 (10)
    { id: 'course_framework',            layer: 3, label: 'Course Framework',   sub: 'Full curriculum' },
    { id: 'competitive_pricing',         layer: 3, label: 'Pricing Research',   sub: 'Competitor analysis' },
    { id: 'sales_page',                  layer: 3, label: 'Sales Page',         sub: '1,500–2,500 words' },
    { id: 'ebook_guide',                 layer: 3, label: 'E-Book / Guide',     sub: 'Outline + listing' },
    { id: 'ab_test_plan',                layer: 3, label: 'A/B Test Plan',      sub: 'Pricing experiment' },
    { id: 'membership_structure',        layer: 3, label: 'Membership',         sub: 'Tiers + calendar' },
    { id: 'launch_email_sequence',       layer: 3, label: 'Launch Sequence',    sub: '7-email campaign' },
    { id: 'affiliate_program',           layer: 3, label: 'Affiliate Program',  sub: 'Commission build' },
    { id: 'testimonial_system',          layer: 3, label: 'Testimonials',       sub: 'Case study sys' },
    { id: 'lapsed_buyer_reactivation',   layer: 3, label: 'Buyer Reactivation', sub: '3-email sequence' },
    // Layer 4 (10)
    { id: 'seo_content_calendar',        layer: 4, label: 'SEO Calendar',       sub: '90-day, 36 posts' },
    { id: 'funnel_design',               layer: 4, label: 'Email Funnel',       sub: 'Lead magnet +5' },
    { id: 'newsletter_monetization',     layer: 4, label: 'Newsletter $',       sub: 'Sponsorships' },
    { id: 'saas_product_spec',           layer: 4, label: 'SaaS Spec',          sub: '10-15 page spec' },
    { id: 'ip_licensing',                layer: 4, label: 'IP Licensing',       sub: '15 targets' },
    { id: 'affiliate_partnerships',      layer: 4, label: 'Affiliate Partners', sub: '20 programs' },
    { id: 'youtube_podcast',             layer: 4, label: 'Video / Podcast',    sub: '24-episode plan' },
    { id: 'community_flywheel',          layer: 4, label: 'Community Flywheel', sub: 'Content→community' },
    { id: 'programmatic_ads',            layer: 4, label: 'Programmatic Ads',   sub: '10 copy variations' },
    { id: 'client_winback',              layer: 4, label: 'Client Win-Back',    sub: '3-email sequence' },
    // Layer 5 (10)
    { id: 'portfolio_analysis',          layer: 5, label: 'Income Allocation',  sub: 'Portfolio strategy' },
    { id: 'compound_growth',             layer: 5, label: 'Growth Model',       sub: 'Compound projections' },
    { id: 'fund_recommendations',        layer: 5, label: 'Fund Picks',         sub: 'ETF portfolio' },
    { id: 'investment_policy_statement', layer: 5, label: 'Inv Policy Stmt',    sub: '2-page IPS' },
    { id: 'real_estate_strategy',        layer: 5, label: 'Real Estate',        sub: 'Entry strategy' },
    { id: 'tax_optimization',            layer: 5, label: 'Tax Optimization',   sub: 'Entity + deductions' },
    { id: 'entity_structure',            layer: 5, label: 'Entity Structure',   sub: 'LLC/S-Corp compare' },
    { id: 'dca_schedule',                layer: 5, label: 'DCA Schedule',       sub: 'Contribution plan' },
    { id: 'insurance_gap_analysis',      layer: 5, label: 'Insurance Gap',      sub: 'Coverage review' },
    { id: 'estate_planning',             layer: 5, label: 'Estate Planning',    sub: 'Will + beneficiaries' },
  ];

  // ── Cross-layer edges (24 pairs) ───────────────────────────────────────────
  const CROSS_EDGES = [
    { s: 'outreach_email',           t: 'speaking_proposals' },
    { s: 'rate_card',                t: 'speaker_fee_rider' },
    { s: 'consulting_proposal',      t: 'coaching_curriculum' },
    { s: 'social_proof',             t: 'waitlist_landing_page' },
    { s: 'booking_page',             t: 'workshop_content' },
    { s: 'coaching_curriculum',      t: 'course_framework' },
    { s: 'workshop_content',         t: 'ebook_guide' },
    { s: 'waitlist_landing_page',    t: 'sales_page' },
    { s: 'speaker_fee_rider',        t: 'competitive_pricing' },
    { s: 'alumni_reactivation',      t: 'lapsed_buyer_reactivation' },
    { s: 'sales_page',               t: 'seo_content_calendar' },
    { s: 'launch_email_sequence',    t: 'funnel_design' },
    { s: 'affiliate_program',        t: 'affiliate_partnerships' },
    { s: 'membership_structure',     t: 'community_flywheel' },
    { s: 'course_framework',         t: 'saas_product_spec' },
    { s: 'ebook_guide',              t: 'newsletter_monetization' },
    { s: 'ab_test_plan',             t: 'programmatic_ads' },
    { s: 'testimonial_system',       t: 'youtube_podcast' },
    { s: 'funnel_design',            t: 'portfolio_analysis' },
    { s: 'newsletter_monetization',  t: 'compound_growth' },
    { s: 'seo_content_calendar',     t: 'fund_recommendations' },
    { s: 'saas_product_spec',        t: 'entity_structure' },
    { s: 'community_flywheel',       t: 'real_estate_strategy' },
    { s: 'affiliate_partnerships',   t: 'tax_optimization' },
  ];

  // ── Same-layer edges (arcing above the lane) ───────────────────────────────
  const SAME_EDGES = [
    { s: 'outreach_email',       t: 'consulting_proposal' },
    { s: 'rate_card',            t: 'rate_negotiation' },
    { s: 'speaking_proposals',   t: 'speaker_fee_rider' },
    { s: 'workshop_content',     t: 'workshop_roi' },
    { s: 'course_framework',     t: 'competitive_pricing' },
    { s: 'sales_page',           t: 'launch_email_sequence' },
    { s: 'funnel_design',        t: 'newsletter_monetization' },
    { s: 'seo_content_calendar', t: 'youtube_podcast' },
    { s: 'portfolio_analysis',   t: 'compound_growth' },
    { s: 'tax_optimization',     t: 'entity_structure' },
  ];

  // ── Module state ───────────────────────────────────────────────────────────
  let _svg        = null;
  let _container  = null;
  let _positions  = {};
  let _apiBase    = null;
  let _onNodeClick = null;
  let _dagCache   = {};         // action_type → dag node
  let _visible    = [];         // filtered AGENTS subset
  let _tip        = null;

  // ── Public API ─────────────────────────────────────────────────────────────
  async function render(containerId, apiBase, opts) {
    opts = opts || {};
    _apiBase    = apiBase;
    _onNodeClick = opts.onNodeClick || null;

    const el = typeof containerId === 'string'
      ? document.getElementById(containerId)
      : containerId;
    if (!el) return;
    _container = el;

    _svg    = null;
    _dagCache = {};
    _visible  = [];
    el.innerHTML = '<div style="color:#475569;padding:2rem;text-align:center;font-size:.8rem">Loading…</div>';

    if (typeof d3 === 'undefined') { _renderFallback(el); return; }

    // Fetch dag to know which agents are active for this simulation
    await _fetchDag();

    // Only show agents that have an actual agent_actions record for this simulation
    _visible = AGENTS.filter(a => {
      const dag = _dagCache[a.id];
      return dag && dag.action_id !== null;
    });

    el.innerHTML = '';

    if (_visible.length === 0) {
      el.innerHTML = [
        '<div style="color:#475569;padding:3rem 2rem;text-align:center">',
        '<div style="font-size:1.4rem;margin-bottom:.5rem">🤖</div>',
        '<div style="font-size:.82rem;font-weight:600;color:#64748b">No agents eligible yet</div>',
        '<div style="font-size:.75rem;margin-top:.35rem">Run a cycle to unlock agents for this simulation.</div>',
        '</div>',
      ].join('');
      return;
    }

    _draw(el);
    _ensureTip();
  }

  function pulse(actionType) {
    if (!_svg) return;
    const node = _svg.select(`.sl-node[data-id="${actionType}"] .sl-rect`);
    if (node.empty()) return;
    node.transition().duration(200).attr('opacity', 0.35)
        .transition().duration(400).attr('opacity', 1);
  }

  async function refreshStatus() {
    await _fetchDag();
    const newVisible = AGENTS.filter(a => {
      const dag = _dagCache[a.id];
      return dag && dag.action_id !== null;
    });

    // Re-render if the visible set changed
    const oldIds = new Set(_visible.map(a => a.id));
    const changed = newVisible.some(a => !oldIds.has(a.id)) || newVisible.length !== _visible.length;
    if (changed && _container) {
      _visible = newVisible;
      _container.innerHTML = '';
      if (_visible.length === 0) return;
      _draw(_container);
    } else {
      _applyStatus();
    }
  }

  function getAgentById(id)  { return AGENTS.find(a => a.id === id) || null; }
  function getDagNode(id)    { return _dagCache[id] || null; }

  function destroy() {
    _svg = null; _container = null; _positions = {};
    _dagCache = {}; _visible = [];
    if (_tip) { _tip.remove(); _tip = null; }
  }

  window.SL = { render, pulse, refreshStatus, getAgentById, getDagNode, destroy, AGENTS };

  // ── Drawing ────────────────────────────────────────────────────────────────
  function _draw(el) {
    const W = el.clientWidth || 1100;
    const activeLayers = [...new Set(_visible.map(a => a.layer))].sort();
    const H = activeLayers.length * LANE_H;

    _positions = _computeLayout(W, activeLayers);

    const svg = d3.select(el).append('svg')
      .attr('width', '100%')
      .attr('height', H)
      .attr('viewBox', `0 0 ${W} ${H}`)
      .style('background', '#060e1c')
      .style('display', 'block');

    _svg = svg;

    _drawLanes(svg, W, H, activeLayers);
    _drawEdges(svg);
    _drawNodes(svg);
    _applyStatus();
  }

  // ── Layout ─────────────────────────────────────────────────────────────────
  function _computeLayout(W, activeLayers) {
    const nodeAreaW = W - LABEL_W;
    const positions = {};
    activeLayers.forEach((layer, laneIdx) => {
      const nodes = _visible.filter(a => a.layer === layer);
      const count = nodes.length;
      const spacing = nodeAreaW / (count + 1);
      nodes.forEach((n, i) => {
        positions[n.id] = {
          x: LABEL_W + spacing * (i + 1),
          y: laneIdx * LANE_H + LANE_H / 2,
        };
      });
    });
    return positions;
  }

  // ── Lanes ──────────────────────────────────────────────────────────────────
  function _drawLanes(svg, W, H, activeLayers) {
    activeLayers.forEach((layer, laneIdx) => {
      const y    = laneIdx * LANE_H;
      const fill = laneIdx % 2 === 0 ? '#060e1c' : '#0a1626';
      const col  = LAYER_COLORS[layer];

      svg.append('rect').attr('x', 0).attr('y', y)
        .attr('width', W).attr('height', LANE_H).attr('fill', fill);

      svg.append('line')
        .attr('x1', 0).attr('y1', y).attr('x2', W).attr('y2', y)
        .attr('stroke', '#1a2535').attr('stroke-width', 0.5);

      // Label column tint
      svg.append('rect').attr('x', 0).attr('y', y)
        .attr('width', LABEL_W).attr('height', LANE_H)
        .attr('fill', _hexAlpha(col, 0.06));

      const parts = LAYER_LABELS[layer].split(' · ');
      svg.append('text')
        .attr('x', LABEL_W / 2).attr('y', y + LANE_H / 2 - 7)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
        .attr('font-size', '8.5px').attr('font-weight', '800').attr('fill', col)
        .text(parts[0]);
      svg.append('text')
        .attr('x', LABEL_W / 2).attr('y', y + LANE_H / 2 + 8)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
        .attr('font-size', '7.5px').attr('fill', _hexAlpha(col, 0.7))
        .text(parts[1] || '');
    });

    svg.append('line')
      .attr('x1', LABEL_W).attr('y1', 0).attr('x2', LABEL_W).attr('y2', H)
      .attr('stroke', '#1e293b').attr('stroke-width', 1);

    svg.append('line')
      .attr('x1', 0).attr('y1', H).attr('x2', W).attr('y2', H)
      .attr('stroke', '#1a2535').attr('stroke-width', 0.5);
  }

  // ── Edges ──────────────────────────────────────────────────────────────────
  function _drawEdges(svg) {
    const visIds = new Set(_visible.map(a => a.id));

    // Arrow marker
    const defs = svg.insert('defs', ':first-child');
    defs.append('marker')
      .attr('id', 'sl-arrow').attr('viewBox', '0 0 8 8')
      .attr('refX', 6).attr('refY', 4)
      .attr('markerWidth', 5).attr('markerHeight', 5).attr('orient', 'auto')
      .append('path').attr('d', 'M0,0 L8,4 L0,8 Z').attr('fill', '#2d3f55');

    const edgeG = svg.append('g').attr('class', 'sl-edges').attr('pointer-events', 'none');

    // Cross-layer — dashed cubic Bézier, bottom of source → top of target
    CROSS_EDGES.forEach(e => {
      if (!visIds.has(e.s) || !visIds.has(e.t)) return;
      const s = _positions[e.s], t = _positions[e.t];
      if (!s || !t) return;
      const midY = (s.y + NODE_H / 2 + t.y - NODE_H / 2) / 2;
      edgeG.append('path')
        .attr('d', `M ${s.x} ${s.y + NODE_H / 2} C ${s.x} ${midY}, ${t.x} ${midY}, ${t.x} ${t.y - NODE_H / 2}`)
        .attr('fill', 'none').attr('stroke', '#2d3f55').attr('stroke-width', 1)
        .attr('stroke-dasharray', '5 4').attr('opacity', 0.55)
        .attr('marker-end', 'url(#sl-arrow)');
    });

    // Same-layer — solid arc above lane
    SAME_EDGES.forEach(e => {
      if (!visIds.has(e.s) || !visIds.has(e.t)) return;
      const s = _positions[e.s], t = _positions[e.t];
      if (!s || !t) return;
      const x1 = s.x + NODE_W / 2, x2 = t.x - NODE_W / 2;
      const midX = (x1 + x2) / 2, arcY = s.y - 28;
      edgeG.append('path')
        .attr('d', `M ${x1} ${s.y} C ${midX} ${arcY}, ${midX} ${arcY}, ${x2} ${t.y}`)
        .attr('fill', 'none').attr('stroke', '#2d3f55').attr('stroke-width', 1).attr('opacity', 0.35);
    });
  }

  // ── Nodes ──────────────────────────────────────────────────────────────────
  function _drawNodes(svg) {
    const nodeG = svg.append('g').attr('class', 'sl-nodes');

    _visible.forEach(agent => {
      const pos = _positions[agent.id];
      if (!pos) return;

      const g = nodeG.append('g')
        .attr('class', 'sl-node')
        .attr('data-id', agent.id)
        .attr('transform', `translate(${pos.x - NODE_W / 2},${pos.y - NODE_H / 2})`)
        .style('cursor', 'pointer');

      g.append('rect')
        .attr('class', 'sl-rect')
        .attr('width', NODE_W).attr('height', NODE_H).attr('rx', 5)
        .attr('fill', '#111827').attr('stroke', _hexAlpha(LAYER_COLORS[agent.layer], 0.4))
        .attr('stroke-width', 1.5);

      g.append('text').attr('class', 'sl-label')
        .attr('x', NODE_W / 2).attr('y', NODE_H / 2 - 6)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
        .attr('font-size', '7.5px').attr('font-weight', '600').attr('fill', '#cbd5e1')
        .text(_trunc(agent.label, 14));

      g.append('text')
        .attr('x', NODE_W / 2).attr('y', NODE_H / 2 + 8)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
        .attr('font-size', '6.5px').attr('fill', '#475569')
        .text(_trunc(agent.sub, 16));

      g.on('mouseover', ev => _showTip(ev, agent))
       .on('mousemove', ev => _moveTip(ev))
       .on('mouseout',  ()  => _hideTip());

      g.on('click', function (ev) {
        ev.stopPropagation();
        if (_onNodeClick) _onNodeClick(agent, _dagCache[agent.id]);
      });
    });
  }

  // ── Status coloring ────────────────────────────────────────────────────────
  function _applyStatus() {
    if (!_svg) return;
    _visible.forEach(agent => {
      const dag    = _dagCache[agent.id];
      const status = dag ? dag.status : 'queued';
      const col    = LAYER_COLORS[agent.layer];

      _svg.select(`.sl-node[data-id="${agent.id}"] .sl-rect`)
        .attr('fill', _nodeFill(status, agent.layer))
        .attr('stroke', _nodeStroke(status, agent.layer))
        .attr('stroke-width', _nodeStrokeW(status))
        .attr('stroke-dasharray', _nodeDash(status));

      _svg.select(`.sl-node[data-id="${agent.id}"] .sl-label`)
        .attr('fill', status === 'complete' ? col : (status === 'queued' ? '#94a3b8' : '#cbd5e1'));
    });
  }

  // ── Dag fetch ──────────────────────────────────────────────────────────────
  async function _fetchDag() {
    if (!_apiBase) return;
    try {
      const res = await fetch(_apiBase + '/dag');
      if (!res.ok) return;
      const data = await res.json();
      data.nodes.forEach(n => { _dagCache[n.id] = n; });
    } catch (_) {}
  }

  // ── Tooltip ────────────────────────────────────────────────────────────────
  function _ensureTip() {
    if (_tip) return;
    _tip = document.createElement('div');
    _tip.style.cssText = [
      'position:fixed', 'pointer-events:none', 'background:#0f172a',
      'border:1px solid #1e293b', 'border-radius:6px', 'padding:.5rem .75rem',
      'font-size:.72rem', 'color:#cbd5e1', 'z-index:9999', 'max-width:220px',
      'display:none', 'box-shadow:0 8px 24px rgba(0,0,0,.5)',
    ].join(';');
    document.body.appendChild(_tip);
  }

  function _showTip(ev, agent) {
    if (!_tip) return;
    const dag    = _dagCache[agent.id];
    const status = dag ? dag.status : '—';
    const col    = LAYER_COLORS[agent.layer];
    const prereqs = (dag && dag.prerequisites && dag.prerequisites.length)
      ? dag.prerequisites.join(', ') : null;
    _tip.innerHTML =
      `<div style="font-weight:700;color:#f1f5f9;margin-bottom:.2rem">${_esc(agent.label)}</div>` +
      `<div style="color:#64748b;font-size:.67rem;margin-bottom:.3rem">${_esc(agent.sub)}</div>` +
      `<div>Status: <span style="color:${col};font-weight:700">${status}</span></div>` +
      (prereqs ? `<div style="color:#475569;font-size:.67rem;margin-top:.2rem">Prereqs: ${_esc(prereqs)}</div>` : '') +
      `<div style="color:#334155;font-size:.65rem;margin-top:.3rem">Click to open editor</div>`;
    _tip.style.display = 'block';
    _moveTip(ev);
  }

  function _moveTip(ev) {
    if (!_tip) return;
    _tip.style.left = (ev.clientX + 14) + 'px';
    _tip.style.top  = (ev.clientY - 10) + 'px';
  }

  function _hideTip() { if (_tip) _tip.style.display = 'none'; }

  // ── Helpers ────────────────────────────────────────────────────────────────
  function _trunc(s, n) { return s && s.length > n ? s.slice(0, n - 1) + '…' : (s || ''); }
  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function _renderFallback(el) {
    el.innerHTML = '<div style="color:#475569;padding:2rem;text-align:center">D3.js not loaded.</div>';
  }

})();
