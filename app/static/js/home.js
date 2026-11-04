/* Home page behaviour (landing.html). From docs/simisi_system_mockup.html.
   The hero chart and sample runs are illustrative; the page labels them so. */
(function () {
  'use strict';
  var NAMES = (window.HOME_LAYERS && window.HOME_LAYERS.length === 5) ? window.HOME_LAYERS : ['Active income', 'Leveraged income', 'Productized income', 'Automated residual', 'Wealth deployment'];
  var COLORS = ['var(--l1)', 'var(--l2)', 'var(--l3)', 'var(--l4)', 'var(--l5)'];
  var root = getComputedStyle(document.documentElement);
  var fill = function (i) { return root.getPropertyValue('--l' + (i + 1)).trim(); };
  var $ = function (id) { return document.getElementById(id); };

  /* ── hero chart: illustrative monthly income ($k) by layer ── */
  var M = 25;
  var ss = function (t) { return t <= 0 ? 0 : t >= 1 ? 1 : t * t * (3 - 2 * t); };
  var ramp = function (start, end, peak) { return function (m) { return peak * ss((m - start) / (end - start)); }; };
  var SER = [function (m) { return 9 + 3.4 * ss(m / 20); }, ramp(3, 16, 3.6), ramp(7, 22, 2.8), ramp(11, 24, 2.1), ramp(14, 24, 1.5)];
  var DATA = SER.map(function (f) { var a = []; for (var m = 0; m < M; m++) a.push(+f(m).toFixed(2)); return a; });
  var on = [true, true, true, true, true];
  var YMAX = 24, W = 1000, H = 400;
  var svg = $('svg'), labels = $('labels'), yax = $('yax'), legend = $('legend');

  if (svg && labels && yax && legend) {
    legend.innerHTML = NAMES.map(function (n, i) {
      return '<button type="button" aria-pressed="true" data-i="' + i + '" style="--c:' + COLORS[i] + '"><i></i>' + n + '</button>';
    }).join('');
    yax.innerHTML = [0, 6, 12, 18, 24].map(function (v) {
      return '<span style="top:' + (100 - v / YMAX * 100) + '%">' + (v ? '$' + v + 'k' : '$0') + '</span>';
    }).join('');
    var x = function (m) { return m / (M - 1) * W; }, y = function (v) { return H - v / YMAX * H; };
    var stacks = function () {
      var base = []; for (var m = 0; m < M; m++) base.push(0);
      return DATA.map(function (d, i) {
        var lo = base.slice();
        if (on[i]) d.forEach(function (v, m) { base[m] += v; });
        return {lo: lo, hi: base.slice()};
      });
    };
    var spread = function () { /* keep end labels from colliding */
      var els = [].slice.call(labels.children).filter(function (e, i) { return on[i]; });
      var h = labels.clientHeight || 300, minGap = 40;
      var pos = els.map(function (e) { return {p: parseFloat(e.style.top) / 100 * h, e: e}; }).sort(function (a, b) { return b.p - a.p; });
      for (var i = 1; i < pos.length; i++) if (pos[i - 1].p - pos[i].p < minGap) pos[i].p = pos[i - 1].p - minGap;
      pos.forEach(function (o) { o.e.style.top = o.p + 'px'; });
    };
    var draw = function (first) {
      var S = stacks();
      var g = '<g class="grid">' + [6, 12, 18].map(function (v) { return '<line x1="0" x2="' + W + '" y1="' + y(v) + '" y2="' + y(v) + '"/>'; }).join('') +
        '<line x1="0" x2="' + W + '" y1="' + H + '" y2="' + H + '" style="stroke:var(--ink-2)"/></g>';
      S.forEach(function (s, i) {
        var top = s.hi.map(function (v, m) { return (m ? 'L' : 'M') + x(m).toFixed(1) + ' ' + y(v).toFixed(1); }).join(' ');
        var bot = s.lo.map(function (v, m) { return [m, v]; }).reverse().map(function (p) { return 'L' + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1); }).join(' ');
        g += '<path class="band" d="' + top + ' ' + bot + ' Z" fill="' + fill(i) + '" style="animation-delay:' + (first ? i * .14 : 0) + 's;' +
          (first ? '' : 'animation:none;') + 'opacity:' + (on[i] ? 1 : 0) + '"/>';
      });
      svg.innerHTML = g;
      labels.innerHTML = S.map(function (s, i) {
        var mid = (s.lo[M - 1] + s.hi[M - 1]) / 2, v = DATA[i][M - 1];
        return '<div style="top:' + (100 - mid / YMAX * 100) + '%;opacity:' + (on[i] ? 1 : 0) + ';animation-delay:' + (first ? .9 + i * .12 : 0) + 's"><b>' +
          NAMES[i] + '</b><span>$' + v.toFixed(1) + 'k a month</span></div>';
      }).join('');
      spread();
    };
    draw(true);
    addEventListener('resize', function () { draw(false); });
    legend.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      var i = +b.dataset.i; on[i] = !on[i];
      if (!on.some(Boolean)) { on[i] = true; return; }
      b.setAttribute('aria-pressed', on[i]); draw(false);
    });

    var plot = $('plot'), cur = $('cursor'), tip = $('tip'), mi = M - 1;
    var show = function (m) {
      mi = Math.max(0, Math.min(M - 1, m));
      var pct = mi / (M - 1) * 100; cur.style.left = pct + '%';
      var rows = NAMES.map(function (n, i) {
        return on[i] ? '<div class="row"><span><i style="--c:' + COLORS[i] + '"></i>' + n + '</span><b>$' + DATA[i][mi].toFixed(1) + 'k</b></div>' : '';
      }).reverse().join('');
      var tot = DATA.reduce(function (a, d, i) { return a + (on[i] ? d[mi] : 0); }, 0);
      tip.innerHTML = '<h4>' + (mi === 0 ? 'Now' : 'Month ' + mi) + '</h4>' + rows + '<div class="row tot"><span>Total a month</span><span>$' + tot.toFixed(1) + 'k</span></div>';
      var w = plot.clientWidth, tw = tip.offsetWidth || 220, px = pct / 100 * w;
      tip.style.left = (px + 16 + tw > w ? px - 16 - tw : px + 16) + 'px';
      plot.classList.add('on');
    };
    plot.addEventListener('pointermove', function (e) { var r = plot.getBoundingClientRect(); show(Math.round((e.clientX - r.left) / r.width * (M - 1))); });
    plot.addEventListener('pointerleave', function () { plot.classList.remove('on'); });
    plot.addEventListener('focus', function () { show(mi); });
    plot.addEventListener('blur', function () { plot.classList.remove('on'); });
    plot.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') { show(mi + 1); e.preventDefault(); }
      if (e.key === 'ArrowLeft') { show(mi - 1); e.preventDefault(); }
      if (e.key === 'Home') show(0);
      if (e.key === 'End') show(M - 1);
    });
  }

  /* ── approval demo (nothing is sent; it only shows the loop) ── */
  var draft = $('draft'), tag = $('draftTag'), loop = [].slice.call(document.querySelectorAll('#loopList li'));
  if (draft && tag && $('approve') && $('undo')) {
    var setStep = function (n) { loop.forEach(function (li, i) { li.classList.toggle('cur', i === n); }); };
    $('approve').addEventListener('click', function () {
      draft.classList.add('is-sent'); tag.textContent = 'Sent'; tag.classList.add('sent'); setStep(3); $('undo').focus();
    });
    $('undo').addEventListener('click', function () {
      draft.classList.remove('is-sent'); tag.textContent = 'Waiting for your approval'; tag.classList.remove('sent'); setStep(0); $('approve').focus();
    });
    draft.querySelectorAll('.foot .btn:not(.primary)').forEach(function (b) { b.addEventListener('click', function () { setStep(1); }); });
  }

  /* ── sample runs: small multiples on one shared scale ── */
  var grid = $('simGrid');
  if (grid) {
    var SIMS = [
      {who: 'Operations consultant, 12 years', to: 'Fractional COO, then a course', k: 'Layer 1 rate', v: '$145–185 an hour', mix: [[8, 4], [0, 3], [0, 4], [0, 1], [0, .5]]},
      {who: 'Senior program manager, enterprise data', to: 'Contract work, then workshops', k: 'Layer 1 rate', v: '$120–160 an hour', mix: [[9, 3.4], [0, 3.6], [0, 2.8], [0, 2.1], [0, 1.5]]},
      {who: 'Marketing lead, SaaS', to: 'Consulting, then a newsletter', k: 'Layer 4 subscribers', v: '1,000–3,000', mix: [[6, 2], [0, 1.5], [0, 1.5], [0, 4.5], [0, .8]]}
    ];
    var SW = 300, SH = 150, SY = 22, START = [0, 3, 7, 11, 14];
    grid.innerHTML = SIMS.map(function (s) {
      var fs = s.mix.map(function (bg, i) {
        return function (m) { return i === 0 ? bg[0] + bg[1] * ss(m / 20) : bg[1] * ss((m - START[i]) / (24 - START[i])); };
      });
      var base = []; for (var m = 0; m < M; m++) base.push(0);
      var X = function (m) { return m / (M - 1) * SW; }, Y = function (v) { return SH - v / SY * SH; };
      var p = '';
      fs.forEach(function (f, i) {
        var lo = base.slice();
        for (var m2 = 0; m2 < M; m2++) base[m2] += f(m2);
        p += '<path d="' + base.map(function (v, k) { return (k ? 'L' : 'M') + X(k).toFixed(1) + ' ' + Y(v).toFixed(1); }).join(' ') + ' ' +
          lo.map(function (v, k) { return [k, v]; }).reverse().map(function (q) { return 'L' + X(q[0]).toFixed(1) + ' ' + Y(q[1]).toFixed(1); }).join(' ') +
          ' Z" fill="var(--l' + (i + 1) + ')"/>';
      });
      return '<article class="sim"><svg viewBox="0 0 ' + SW + ' ' + SH + '" preserveAspectRatio="none" role="img" aria-label="Stacked income over 24 months for ' + s.who + '">' +
        '<line x1="0" x2="' + SW + '" y1="' + SH / 2 + '" y2="' + SH / 2 + '" stroke="var(--rule)" vector-effect="non-scaling-stroke"/>' + p + '</svg>' +
        '<h3>' + s.who + '</h3><p class="to">' + s.to + '</p><dl><dt>' + s.k + '</dt><dd>' + s.v + '</dd></dl></article>';
    }).join('');
  }

  /* ── playbook parts ── */
  var parts = $('parts');
  if (parts) {
    var PARTS = [
      ['I', 'The Model', 'All seven lenses', 'The five-layer wealth engine, agents instead of advice, the anatomy of a case study and thinking in algorithms.'],
      ['II', 'Layer 1: Active income', 'Optimal stopping', 'The floor, not the ceiling. One case study per Layer 1 agent: Rate Card, Consulting Outreach, Cold Email Campaign, Role Search and more.'],
      ['III', 'Layer 2: Leveraged income', 'Explore vs. exploit', 'Breaking the link between hours and dollars. Workshop Curriculum, Group Coaching, Speaking Proposals and more.'],
      ['IV', 'Layer 3: Productized income', 'Caching', 'From services to products. Course Curriculum, Pricing Research, Sales Page, Launch Sequence and more.'],
      ['V', 'Layer 4: Automated residual', 'Scheduling', 'The automation threshold. Newsletter, Lead Magnet Funnel, SEO Content Calendar and more.'],
      ['VI', 'Layer 5: Wealth deployment', 'Overfitting', 'When money changes jobs. Income Allocation, Projections, Investment Policy and more. Planning only.'],
      ['VII', 'All together now', 'Relaxation', 'One life running all five layers at once, and what to do when the simulation disagrees with your gut.'],
      ['VIII', 'Running your own simulation', 'All seven, applied', 'Your first 90 days, failure modes, honest limits and the road ahead.']];
    parts.innerHTML = PARTS.map(function (p, i) {
      return '<details' + (i === 0 ? ' open' : '') + '><summary><span class="pn">' + p[0] + '</span><span class="pt">' + p[1] + '</span><span class="lens">' + p[2] +
        '</span><span class="pm" aria-hidden="true">+</span></summary><div class="body">' + p[3] + '</div></details>';
    }).join('');
  }
  var tpl = $('tpl');
  if (tpl) {
    tpl.innerHTML = ['Persona', 'Starting state', 'Agent action', 'Simulation run', 'Decision fork', '24-month outcome', 'Lessons and failure modes']
      .map(function (s) { return '<li>' + s + '</li>'; }).join('');
  }

  /* ── launch-price countdown (shown in the last 72 hours) ── */
  var cd = $('priceCountdown');
  if (cd && cd.dataset.expires) {
    var expires = new Date(cd.dataset.expires + (cd.dataset.expires.slice(-1) === 'Z' ? '' : 'Z'));
    var tick = function () {
      var diff = Math.max(0, Math.floor((expires - Date.now()) / 1000));
      if (diff <= 0) { cd.textContent = 'Offer ended'; return; }
      if (diff > 72 * 3600) { cd.style.display = 'none'; return; }
      var d = Math.floor(diff / 86400), h = Math.floor((diff % 86400) / 3600), m = Math.floor((diff % 3600) / 60);
      cd.textContent = 'Ends in ' + (d ? d + 'd ' : '') + h + 'h ' + m + 'm';
    };
    tick(); setInterval(tick, 60000);
  }
})();
