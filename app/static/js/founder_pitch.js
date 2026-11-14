/* Founder Track intake (WP1): create venture, upload documents, spoken/typed
   pitch, run evaluation. Speech in uses the browser's speech recognition where
   available (same push-to-talk approach as guide_console.js); SimiSI never
   receives audio, only the text. */
(function () {
  'use strict';
  var root = document.getElementById('fi-root');
  if (!root) return;
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function api(url, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || d.ok === false) {
          var msg = d.error || d.message || (r.status === 413 ? 'That file is too large.' : 'Something went wrong. Please try again.');
          var e = new Error(msg); e.status = r.status; e.data = d; throw e;
        }
        return d;
      });
    });
  }
  function postJSON(url, body) {
    return api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }

  /* ---------- create venture ---------- */
  var form = $('fi-create');
  if (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var msg = $('fi-create-msg'), name = $('fi-name').value.trim();
      if (!name) { msg.textContent = 'Give the venture a name.'; $('fi-name').focus(); return; }
      msg.textContent = 'Creating...';
      postJSON('/api/founder/ventures', { name: name, one_liner: $('fi-oneliner').value, stage: $('fi-stage').value })
        .then(function (d) { window.location.href = d.url; })
        .catch(function (e) { msg.textContent = e.message; });
    });
    return;
  }

  var VID = root.getAttribute('data-venture');
  var BASE = '/api/founder/v/' + encodeURIComponent(VID);

  /* ---------- uploads ---------- */
  var LABELS = { startup_plan: 'Startup plan', exec_summary: 'Executive summary', ops_plan: 'Operations plan',
                 pitch_deck: 'Pitch deck', financial_model: 'Financial model', pitch_transcript: 'Pitch transcript' };
  function setDocState(a) {
    var row = $('doc-' + a.kind); if (!row) return;
    var st = row.querySelector('[data-state]');
    st.innerHTML = '<span class="fi-have">Version ' + esc(a.version) + ' · ' + esc((a.created_at || '').slice(0, 10)) +
      (a.filename && a.kind !== 'pitch_transcript' ? ' · ' + esc(a.filename) : '') + '</span>';
    var lab = row.querySelector('.fi-up label');
    if (lab) lab.firstChild.nodeValue = 'Replace';
  }
  Array.prototype.forEach.call(root.querySelectorAll('input[type=file][data-kind]'), function (inp) {
    var row = inp.closest('.fi-doc'), msg = row.querySelector('[data-msg]'), lab = row.querySelector('.fi-up label');
    if (lab) lab.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inp.click(); } });
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0]; if (!f) return;
      if (!/\.(pdf|docx)$/i.test(f.name)) { msg.className = 'fi-msg err'; msg.textContent = 'Only PDF and Word (.docx) files are accepted.'; inp.value = ''; return; }
      var fd = new FormData(); fd.append('file', f); fd.append('kind', inp.getAttribute('data-kind'));
      msg.className = 'fi-msg'; msg.textContent = 'Uploading and reading ' + f.name + '...';
      api(BASE + '/artifacts', { method: 'POST', body: fd })
        .then(function (d) { setDocState(d.artifact); msg.className = 'fi-msg ok'; msg.textContent = d.message; })
        .catch(function (e) { msg.className = 'fi-msg err'; msg.textContent = e.message; })
        .then(function () { inp.value = ''; });
    });
  });

  /* ---------- run evaluation (WP2 provides the endpoint) ---------- */
  var evalBtn = $('fi-eval');
  if (evalBtn) evalBtn.addEventListener('click', function () {
    var msg = $('fi-eval-msg');
    evalBtn.disabled = true; msg.textContent = 'Starting...';
    postJSON(BASE + '/evaluate', {})
      .then(function (d) {
        msg.textContent = d.message || 'The evaluation has started.';
        if (d.url || d.report_url) setTimeout(function () { window.location.href = d.url || d.report_url; }, 900);
      })
      .catch(function (e) { msg.textContent = e.status === 404 ? 'The evaluation is not available yet.' : e.message; })
      .then(function () { evalBtn.disabled = false; });
  });

  /* ---------- pitch ---------- */
  if (root.getAttribute('data-pitch') !== '1') return;
  var log = $('fi-log'), txt = $('fi-text'), send = $('fi-send'), mic = $('fi-mic'), pmsg = $('fi-pitch-msg');
  var ELEM = { problem: 'Problem', customer: 'Customer', solution: 'Solution', market: 'Market',
               business_model: 'How it makes money', traction: 'Evidence so far', team: 'Team', ask: 'What you need next' };
  var active = send.textContent.trim() === 'Send', busy = false;
  log.scrollTop = log.scrollHeight;   /* a restored conversation opens at its latest turn */

  function add(role, text, extra) {
    var e = $('fi-empty'); if (e) e.remove();
    var p = document.createElement('p');
    p.className = 'fi-turn' + (role === 'founder' ? ' me' : '') + (extra && extra.crisis ? ' crisis' : '');
    var html = '<b>' + (role === 'founder' ? 'You' : 'SimiSI') + '</b>' + esc(text);
    if (extra && extra.resources) extra.resources.forEach(function (r) {
      html += '<br><a href="' + esc(r.href) + '">' + esc(r.label) + '</a>'; });
    p.innerHTML = html; log.appendChild(p); log.scrollTop = log.scrollHeight;
  }
  function render(t) {
    add('simi', t.say, t);
    var mw = $('fi-missing-wrap'), ml = $('fi-missing');
    if (t.crisis) {
      /* Paused: no checklist, no restatement; the next Send starts a fresh conversation. */
      mw.hidden = true; $('fi-restate').hidden = true; active = false; send.textContent = 'Start';
      return;
    }
    if (!t.fallback) {
      mw.hidden = false;
      ml.innerHTML = (t.missing || []).map(function (m) { return '<li>' + esc(ELEM[m] || m) + '</li>'; }).join('') ||
        '<li>Nothing major</li>';
    }
    if (t.confirmed) {
      $('fi-restate').hidden = true; active = false; send.textContent = 'Start';
      if (t.artifact) setDocState(t.artifact);
      pmsg.textContent = 'Pitch saved as transcript version ' + (t.artifact ? t.artifact.version : '') + '.';
    } else if (t.ready_to_confirm && t.restatement) {
      $('fi-restatement').value = t.restatement; $('fi-restate').hidden = false;
    } else {
      $('fi-restate').hidden = true;
    }
  }
  function busyOn(on, label) { busy = on; send.disabled = on; pmsg.textContent = label || ''; }
  function startPitch(then) {
    busyOn(true, 'Starting...');
    postJSON(BASE + '/pitch/start', {}).then(function (d) {
      log.innerHTML = ''; $('fi-restate').hidden = true; active = true; send.textContent = 'Send';
      busyOn(false); render(d.turn); if (then) then();
    }).catch(function (e) { busyOn(false, e.message); });
  }
  function say(text, mode) {
    if (!active) { startPitch(function () { say(text, mode); }); return; }
    add('founder', text);
    busyOn(true, 'SimiSI is thinking...');
    postJSON(BASE + '/pitch/turn', { text: text, input_mode: mode })
      .then(function (d) { busyOn(false); render(d.turn); })
      .catch(function (e) { busyOn(false, e.message); });
  }
  send.addEventListener('click', function () {
    if (busy) return;
    var v = txt.value.trim();
    if (!active && !v) { startPitch(); return; }
    if (!v) { txt.focus(); return; }
    txt.value = ''; say(v, 'typed');
  });
  txt.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send.click(); } });
  $('fi-confirm').addEventListener('click', function () {
    var b = this; b.disabled = true; pmsg.textContent = 'Saving...';
    postJSON(BASE + '/pitch/confirm', { restatement: $('fi-restatement').value })
      .then(function (d) {
        $('fi-restate').hidden = true; active = false; send.textContent = 'Start';
        setDocState(d.artifact); pmsg.textContent = d.message;
        add('simi', 'Saved. Your confirmed pitch is now part of your documents.');
      })
      .catch(function (e) { pmsg.textContent = e.message; })
      .then(function () { b.disabled = false; });
  });
  $('fi-restart').addEventListener('click', function () { if (!busy) startPitch(); });

  /* speech in (push-to-talk), as in guide_console.js */
  var Rec = window.SpeechRecognition || window.webkitSpeechRecognition, rec = null, heard = '';
  if (!Rec) {
    mic.disabled = true; mic.title = 'Voice input is not available in this browser. Type instead.';
    mic.setAttribute('aria-label', 'Speak (not available in this browser, type instead)');
  }
  mic.addEventListener('click', function () {
    if (!Rec || busy) return;
    if (rec) { rec.stop(); return; }
    heard = '';
    rec = new Rec(); rec.lang = 'en-US'; rec.interimResults = true; rec.continuous = false;
    mic.setAttribute('aria-pressed', 'true'); mic.textContent = 'Stop'; pmsg.textContent = 'Listening...';
    rec.onresult = function (ev) { var t = ''; for (var i = 0; i < ev.results.length; i++) t += ev.results[i][0].transcript;
      heard = t; txt.value = t; };
    rec.onerror = function () { pmsg.textContent = 'Could not hear you. Try again or type.'; };
    rec.onend = function () {
      rec = null; mic.setAttribute('aria-pressed', 'false'); mic.textContent = 'Speak';
      if (heard.trim()) { txt.value = ''; pmsg.textContent = ''; say(heard.trim(), 'voice'); }
      else if (pmsg.textContent === 'Listening...') pmsg.textContent = '';
    };
    rec.start();
  });
})();
