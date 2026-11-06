/* The estimate component for ranges built in the browser (COMPLIANCE_PLAN A4).
   Same markup and rules as templates/partials/_estimate.html: a low–high range,
   never a single number; the "Estimate, not a promise" label; assumptions and
   source one click away; the base-rate note on Layers 3 and 4. Wording comes
   from window.SIMI_EST (partials/_estimate_assets.html, the legal-text source). */
(function () {
  'use strict';
  var T = window.SIMI_EST || {label: 'Estimate, not a promise', intro: '', base: ''};
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  };
  var money = function (v) { return '$' + Math.round(Number(v)).toLocaleString('en-US'); };
  var has = function (v) { return v !== null && v !== undefined && v !== '' && !isNaN(Number(v)); };

  function rangeText(low, high, unit) {
    var u = unit === '' ? '' : '/' + (unit || 'mo');
    if (has(low) && has(high) && Number(low) !== Number(high)) return money(low) + '–' + money(high) + u;
    if (has(high)) return 'up to ' + money(high) + u;
    if (has(low)) return 'from ' + money(low) + u;
    return '';
  }

  /* opts: {unit, layer, basis, source, compact}. compact drops the basis panel
     (for table cells and slides); the label always stays. */
  function html(low, high, opts) {
    opts = opts || {};
    var r = rangeText(low, high, opts.unit);
    if (!r) return '';
    var out = '<span class="est"' + (opts.layer ? ' data-layer="' + esc(opts.layer) + '"' : '') + '>'
      + '<span class="est-range">' + esc(r) + '</span>'
      + '<span class="est-label">' + esc(T.label) + '</span>';
    if (!opts.compact) {
      out += '<details class="est-basis" onclick="event.stopPropagation()"><summary>How we estimated this</summary>'
        + '<span class="est-body"><span class="est-p">' + esc(T.intro) + '</span>'
        + (opts.basis ? '<span class="est-p"><b>Assumptions:</b> ' + esc(opts.basis) + '</span>' : '')
        + (opts.source ? '<span class="est-p"><b>Source:</b> ' + esc(opts.source) + '</span>' : '')
        + (Number(opts.layer) === 3 || Number(opts.layer) === 4 ? '<span class="est-p est-base">' + esc(T.base) + '</span>' : '')
        + '</span></details>';
    }
    return out + '</span>';
  }

  window.SimiEstimate = {html: html, rangeText: rangeText, label: T.label, baseNote: T.base};
})();
