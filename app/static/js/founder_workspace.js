/* Founder Track workspace, plan and team pages (FOUNDER_PLAN.md WP4).
 * Buttons carry data-act; the live area (#fw-live) re-renders from
 * /api/founder/v/<id>/live whenever the venture's SSE stream reports a change.
 */
(function () {
  'use strict';
  var root = document.getElementById('founder-ws');
  if (!root) return;
  var vid = root.getAttribute('data-venture');
  var view = root.getAttribute('data-view') || 'workspace';
  var msg = document.getElementById('fw-msg');
  var live = document.getElementById('fw-live');

  function say(text, isErr) {
    if (!msg) return;
    msg.textContent = text || '';
    msg.className = 'fw-msg' + (isErr ? ' err' : '');
  }

  function api(method, url, body) {
    return fetch(url, {
      method: method, credentials: 'same-origin',
      headers: body ? {'Content-Type': 'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.status === 402 && d.checkout_url) { window.location.href = d.checkout_url; }
        return {ok: r.ok, status: r.status, d: d || {}};
      });
    });
  }

  var timer = null;
  function refresh() {
    if (!live) { window.location.reload(); return; }
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () {
      fetch('/api/founder/v/' + vid + '/live?view=' + encodeURIComponent(view), {credentials: 'same-origin'})
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) { if (html !== null) live.innerHTML = html; })
        .catch(function () {});
    }, 250);
  }

  function done(res, okText) {
    if (res.ok) {
      say(res.d.message || okText || 'Saved.');
      if (view === 'team') { setTimeout(function () { window.location.reload(); }, 400); } else { refresh(); }
    } else {
      say(res.d.error || 'That did not work. Try again.', true);
    }
    return res;
  }

  // ---- dialogs ---------------------------------------------------------
  var dlg = document.getElementById('fw-dialog');
  var dlgText = document.getElementById('fw-dialog-text');
  var dlgErr = document.getElementById('fw-dialog-err');
  var pending = null;
  var opener = null;

  function openDialog(kind, stepId, title) {
    if (!dlg || typeof dlg.showModal !== 'function') {
      var t = window.prompt(kind === 'skip' ? 'Why are you skipping this step?' : 'Evidence (optional)');
      if (t === null) return;
      submitStep(kind, stepId, t);
      return;
    }
    opener = document.activeElement;
    pending = {kind: kind, step: stepId};
    document.getElementById('fw-dialog-h').textContent = kind === 'skip' ? 'Skip this step' : 'Mark this step done';
    document.getElementById('fw-dialog-sub').textContent = title || '';
    document.getElementById('fw-dialog-label').textContent = kind === 'skip'
      ? 'Why are you skipping it? (required)' : 'Evidence: what you did or found (optional)';
    dlgText.value = '';
    dlgText.required = kind === 'skip';
    dlgErr.textContent = '';
    dlg.showModal();
    dlgText.focus();
  }

  function submitStep(kind, stepId, text) {
    var body = kind === 'skip' ? {reason: text} : {evidence_note: text};
    return api('POST', '/api/founder/steps/' + stepId + '/' + kind, body).then(function (res) {
      return done(res, kind === 'skip' ? 'Step skipped.' : 'Step done. The plan has been updated.');
    });
  }

  if (dlg) {
    document.getElementById('fw-dialog-form').addEventListener('submit', function (e) {
      e.preventDefault();
      if (!pending) return;
      var text = dlgText.value.trim();
      if (pending.kind === 'skip' && text.length < 3) { dlgErr.textContent = 'Say why you are skipping this step.'; return; }
      submitStep(pending.kind, pending.step, text).then(function (res) {
        if (res.ok) { dlg.close(); pending = null; if (opener) opener.focus(); }
        else { dlgErr.textContent = res.d.error || 'That did not work.'; }
      });
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll('.fw-dialog [data-close]'), function (b) {
    b.addEventListener('click', function () { b.closest('dialog').close(); if (opener) opener.focus(); });
  });

  function showOutput(id) {
    var box = document.getElementById('fw-output');
    api('GET', '/api/founder/outputs/' + id).then(function (res) {
      if (!res.ok) { say(res.d.error || 'Could not open the output.', true); return; }
      document.getElementById('fw-output-h').textContent = (res.d.step_title || 'Agent output') + ' · v' + res.d.output.version;
      document.getElementById('fw-output-body').textContent = res.d.output.content || '';
      opener = document.activeElement;
      if (box && typeof box.showModal === 'function') box.showModal(); else window.alert(res.d.output.content);
    });
  }

  // ---- clicks ----------------------------------------------------------
  root.addEventListener('click', function (e) {
    var b = e.target.closest('[data-act]');
    if (!b || b.disabled) return;
    var act = b.getAttribute('data-act');
    var step = b.getAttribute('data-step');
    if (act === 'complete' || act === 'skip') { openDialog(act, step, b.getAttribute('data-title')); return; }
    if (act === 'output') { showOutput(b.getAttribute('data-output')); return; }
    b.disabled = true;
    var p;
    if (act === 'build-plan') {
      say('Building your plan…');
      p = api('POST', '/api/founder/v/' + vid + '/plan/build').then(function (r) { return done(r, 'Plan ready.'); });
    } else if (act === 'build-team') {
      say('Matching agents to your findings…');
      p = api('POST', '/api/founder/v/' + vid + '/team/build').then(function (r) { return done(r, 'Team updated.'); });
    } else if (act === 'accept-layer') {
      p = api('POST', '/api/founder/v/' + vid + '/team/accept', {layer: b.getAttribute('data-layer')})
        .then(function (r) { return done(r, 'Layer accepted.'); });
    } else if (act === 'bp-status') {
      p = api('POST', '/api/founder/v/' + vid + '/team/' + b.getAttribute('data-bp') + '/status',
              {status: b.getAttribute('data-status')}).then(function (r) { return done(r, 'Saved.'); });
    } else if (act === 'start') {
      p = api('POST', '/api/founder/steps/' + step + '/start').then(function (r) { return done(r, 'Started.'); });
    } else if (act === 'run') {
      say('Starting the agent…');
      p = api('POST', '/api/founder/steps/' + step + '/run', {})
        .then(function (r) { return done(r, 'The agent is working. The plan updates when it finishes.'); });
    } else if (act === 'approve' || act === 'reject') {
      var body = {};
      if (act === 'reject') {
        var why = window.prompt('Why decline this draft? (optional)');
        if (why === null) { b.disabled = false; return; }
        body.reason = why;
      }
      p = api('POST', '/api/founder/actions/' + b.getAttribute('data-action') + '/' + act, body)
        .then(function (r) { return done(r); });
    }
    if (p) p.then(function () { b.disabled = false; }, function () { b.disabled = false; say('Network error. Try again.', true); });
    else b.disabled = false;
  });

  // ---- team settings ---------------------------------------------------
  root.addEventListener('change', function (e) {
    var sel = e.target;
    var bp = sel.getAttribute('data-autonomy');
    var layer = sel.getAttribute('data-layer-autonomy');
    if (!bp && !layer) return;
    if (!sel.value) return;
    var body = {param_key: 'autonomy_level', value: sel.value};
    if (bp) body.blueprint_id = bp; else body.layer = layer;
    api('POST', '/api/founder/v/' + vid + '/team/override', body).then(function (r) {
      done(r, r.d && r.d.note ? 'Saved. ' + r.d.note : 'Autonomy saved.');
    });
  });

  root.addEventListener('submit', function (e) {
    var form = e.target.closest('[data-override-form]');
    if (!form) return;
    e.preventDefault();
    var bp = form.getAttribute('data-override-form');
    var inputs = form.querySelectorAll('input[name], textarea[name]');
    var jobs = [];
    Array.prototype.forEach.call(inputs, function (i) {
      if (i.value === i.getAttribute('data-orig')) return;
      jobs.push({param_key: i.name, value: i.value, blueprint_id: bp});
    });
    if (!jobs.length) { say('Nothing changed.'); return; }
    var chain = Promise.resolve({ok: true, d: {}});
    jobs.forEach(function (j) {
      chain = chain.then(function (prev) {
        if (!prev.ok) return prev;
        return api('POST', '/api/founder/v/' + vid + '/team/override', j);
      });
    });
    chain.then(function (r) { done(r, 'Settings saved.'); });
  });

  // ---- live stream -----------------------------------------------------
  if (root.getAttribute('data-stream') && window.EventSource) {
    var names = ['step_locked', 'step_ready', 'step_in_progress', 'step_awaiting_approval', 'step_done', 'step_skipped',
                 'step_added', 'plan_built', 'agent_started', 'agent_output', 'agent_stopped', 'draft_approved', 'draft_declined', 'team_built',
                 'team_accepted', 'param_override', 'evaluation_done', 'update'];
    var es = new EventSource('/api/founder/v/' + vid + '/stream');
    names.forEach(function (n) { es.addEventListener(n, refresh); });
    es.onmessage = refresh;
    es.onerror = function () {
      // The server closes the stream every few minutes; the browser reconnects.
    };
    window.addEventListener('beforeunload', function () { es.close(); });
  }
})();
