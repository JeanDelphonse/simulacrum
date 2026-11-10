/* SME Console (/guide). Ported from docs/simisi_sme_console_mockup.html: the
   avatar stage, guide picker and illustrated portraits are the mockup's; the
   scripted demo is replaced by the real conversation API.
   Voice: the browser's speech synthesis stands in until a neural speech
   provider is chosen (PRD open question). Speech in uses the browser's speech
   recognition where available and allowed; SimiSI never receives audio. */
(function () {
  'use strict';
  var BOOT = window.GUIDE_BOOT || {};
  var ROOT = (window.ROOT || '').replace(/\/+$/, '');
  var $ = function (id) { return document.getElementById(id); };
  var stage = $('stage'), msgs = $('msgs'), cap = $('caption');
  var S = { session: null, layer: 1, facts: [], factById: {}, busy: false, state: BOOT.state || { cards: {}, skills: [] },
            draftId: null, epoch: 0 };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function api(url, body, method) {
    if (BOOT.simulation) url += (url.indexOf('?') < 0 ? '?' : '&') + 'sim=' + encodeURIComponent(BOOT.simulation.id);
    return fetch(ROOT + url, { method: method || 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { var e = new Error(d.message || d.error || 'Something went wrong.'); e.status = r.status; throw e; }
        return d; }); });
  }

  /* ---------- voice (browser stand-in) ---------- */
  var SPEECH = !!(window.speechSynthesis && window.SpeechSynthesisUtterance);   // some browsers expose the key with no engine
  var muted = !SPEECH || !BOOT.voice_enabled, VOICES = [];
  function loadVoices() { try { VOICES = speechSynthesis.getVoices().filter(function (v) { return /^en/i.test(v.lang); }); } catch (e) { VOICES = []; } }
  if (SPEECH) { loadVoices(); speechSynthesis.onvoiceschanged = loadVoices; }
  var FEMALE = ['samantha', 'ava', 'allison', 'susan', 'victoria', 'karen', 'moira', 'tessa', 'fiona', 'zira', 'aria', 'jenny', 'female', 'google us english', 'google uk english female'];
  var MALE = ['daniel', 'alex', 'aaron', 'tom', 'oliver', 'fred', 'guy', 'david', 'mark', 'male', 'google uk english male'];
  function pickVoice(g) {
    if (!VOICES.length) return null;
    var pref = g.gender === 'm' ? MALE : FEMALE;
    var c = VOICES.filter(function (v) { return pref.some(function (n) { return v.name.toLowerCase().indexOf(n) >= 0; }); });
    var pool = c.length ? c : VOICES;
    return pool[(g.vi || 0) % pool.length];
  }
  function hush() { try { if (SPEECH) speechSynthesis.cancel(); } catch (e) {} }
  function speak(text, g) {
    g = g || G;
    return new Promise(function (res) {
      if (muted) { res(); return; }
      var done = false, fin = function () { if (!done) { done = true; res(); } };
      try {
        var u = new SpeechSynthesisUtterance(text), v = pickVoice(g);
        if (v) u.voice = v;
        u.rate = g.rate || 1; u.pitch = g.pitch || 1; u.onend = fin; u.onerror = fin;
        setTimeout(fin, text.split(' ').length * 520 / u.rate + 3500);
        var started = false; u.onstart = function () { started = true; };
        // First-audio timeout: if speech never starts, carry on with captions only.
        setTimeout(function () { if (!started) { hush(); fin(); } }, (BOOT.first_audio_timeout_s || 6) * 1000);
        speechSynthesis.speak(u);
      } catch (e) { fin(); }
    });
  }
  function TICK() { return muted ? 60 : Math.round(330 / ((G && G.rate) || 1)); }
  function setState(s, label) { stage.className = 'card stage ' + (s || ''); $('state').textContent = label || 'Ready'; }

  /* ---------- portraits (from the mockup) ---------- */
  var PID = 0;
  function portrait(g, vb) {
    var u = 'p' + (++PID), s = g.skin, sh = g.shade, h = g.hair, brow = (g.style === 'bald' ? '#2a1a10' : (g.hair === '#e4e1da' ? '#b9b4aa' : h));
    var back = '', front = '';
    switch (g.style) {
      case 'curly': back = '<g fill="' + h + '"><circle cx="100" cy="84" r="56"/><circle cx="56" cy="98" r="22"/><circle cx="144" cy="98" r="22"/><circle cx="66" cy="56" r="25"/><circle cx="134" cy="56" r="25"/><circle cx="100" cy="36" r="26"/><circle cx="54" cy="124" r="16"/><circle cx="146" cy="124" r="16"/></g>';
        front = '<path d="M62 92 C60 64 80 50 100 50 C120 50 140 64 138 92 C128 76 116 70 100 70 C84 70 72 76 62 92Z" fill="' + h + '"/>'; break;
      case 'long': back = '<path d="M54 100 C46 48 80 34 102 36 C132 38 156 58 148 104 C152 140 158 172 152 192 L48 192 C42 168 50 140 54 100Z" fill="' + h + '"/>';
        front = '<path d="M60 98 C54 56 86 40 106 42 C132 44 146 64 140 102 C134 80 120 64 98 64 C84 70 70 82 60 98Z" fill="' + h + '"/>'; break;
      case 'bob': back = '<path d="M56 98 C50 50 82 38 100 38 C124 38 152 52 146 98 C148 122 146 138 140 146 L60 146 C54 138 52 122 56 98Z" fill="' + h + '"/>';
        front = '<path d="M60 92 C60 58 82 46 100 46 C122 46 140 58 140 92 C128 80 112 74 100 74 C88 74 72 80 60 92Z" fill="' + h + '"/>'; break;
      case 'bun': back = '<circle cx="100" cy="40" r="17" fill="' + h + '"/>';
        front = '<path d="M60 92 C58 60 80 46 100 46 C122 46 142 60 140 92 C132 72 118 62 100 62 C82 62 68 72 60 92Z" fill="' + h + '"/>'; break;
      case 'short': front = '<path d="M60 92 C56 54 84 42 102 42 C126 42 146 58 140 92 C136 74 124 62 100 62 C80 62 66 72 60 92Z" fill="' + h + '"/>'; break;
      case 'wavy': front = '<path d="M58 96 C50 58 80 38 104 40 C132 42 150 62 142 96 C138 80 128 66 112 64 C98 72 76 72 58 96Z" fill="' + h + '"/>'; break;
      case 'bald': front = '<ellipse cx="88" cy="60" rx="14" ry="6" fill="#fff" opacity=".13"/>'; break;
      case 'hijab': back = '<path d="M42 112 C38 50 70 28 100 28 C130 28 162 50 158 112 C160 150 164 184 172 220 L28 220 C36 184 40 150 42 112Z" fill="' + g.scarf + '"/>';
        front = '<path d="M58 100 C56 62 78 42 100 42 C122 42 144 62 142 100 C140 78 126 58 100 58 C74 58 60 78 58 100Z" fill="' + g.scarf + '"/><path d="M58 114 C64 146 82 160 100 160 C118 160 136 146 142 114 C150 148 146 172 100 182 C54 172 50 148 58 114Z" fill="' + g.scarf + '"/>'; break;
    }
    var hideEars = ['curly', 'long', 'bob', 'hijab'].indexOf(g.style) >= 0, noNeck = g.style === 'hijab';
    var eyes = ''; [84, 116].forEach(function (x) { eyes += '<ellipse cx="' + x + '" cy="100" rx="8" ry="4.7" fill="#fbf7f2"/><circle cx="' + x + '" cy="100" r="3.9" fill="' + g.eye + '"/><circle cx="' + x + '" cy="100" r="1.8" fill="#111"/><circle cx="' + (x + 1.3) + '" cy="98.7" r="1" fill="#fff"/>'; });
    var extra = '';
    if (g.beard) extra += '<path d="M64 112 C66 146 84 158 100 158 C116 158 134 146 136 112 C130 132 118 140 100 140 C82 140 70 132 64 112Z" fill="' + h + '" opacity=".92"/>';
    if (g.beard || g.goatee || g.mustache) extra += '<path d="M88 124 Q100 117 112 124 Q100 127 88 124Z" fill="' + h + '"/>';
    if (g.goatee) extra += '<path d="M90 139 Q100 154 110 139 Q100 143 90 139Z" fill="' + h + '"/>';
    if (g.wrinkles) extra += '<g stroke="' + sh + '" stroke-width="1.4" fill="none" stroke-linecap="round"><path d="M84 70 Q100 67 116 70"/><path d="M86 76 Q100 74 114 76"/><path d="M71 106 Q74 109 77 108"/><path d="M129 106 Q126 109 123 108"/></g>';
    if (g.glasses) extra += '<g fill="none" stroke="#2b2b2b" stroke-width="2.2"><rect x="72" y="92" width="24" height="16" rx="6"/><rect x="104" y="92" width="24" height="16" rx="6"/><path d="M96 99 L104 99"/><path d="M72 98 L62 96"/><path d="M128 98 L138 96"/></g>';
    if (g.earrings) extra += '<circle cx="60" cy="116" r="3.3" fill="#E4B855"/><circle cx="140" cy="116" r="3.3" fill="#E4B855"/>';
    return '<svg class="portrait" viewBox="' + (vb || '0 0 200 220') + '" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="' + esc(g.name) + ', AI guide">'
      + '<defs><radialGradient id="sk' + u + '" cx="45%" cy="38%" r="68%"><stop offset="0" stop-color="' + s + '"/><stop offset="1" stop-color="' + sh + '"/></radialGradient></defs>'
      + '<g class="p-body"><path d="M24 220 C28 176 62 160 100 158 C138 160 172 176 176 220Z" fill="' + g.cloth + '"/><path d="M24 220 C28 176 62 160 100 158 C138 160 172 176 176 220Z" fill="#000" opacity=".08"/>'
      + (noNeck ? '' : '<path d="M86 160 L100 184 L114 160Z" fill="' + sh + '"/><path d="M80 162 L100 192 M120 162 L100 192" stroke="#000" stroke-opacity=".18" stroke-width="2"/>') + '</g>'
      + '<g class="p-head">' + back
      + (noNeck ? '' : '<path d="M86 130 L86 162 Q100 172 114 162 L114 130Z" fill="' + sh + '"/>')
      + (hideEars ? '' : '<ellipse cx="60" cy="101" rx="7" ry="11" fill="' + sh + '"/><ellipse cx="140" cy="101" rx="7" ry="11" fill="' + sh + '"/>')
      + '<ellipse cx="100" cy="96" rx="40" ry="50" fill="url(#sk' + u + ')"/>' + front
      + '<ellipse cx="78" cy="118" rx="7" ry="4" fill="#d77a6e" opacity=".14"/><ellipse cx="122" cy="118" rx="7" ry="4" fill="#d77a6e" opacity=".14"/>'
      + '<g class="p-eyes">' + eyes + '</g>'
      + '<path d="M74 89 Q84 84 93 88" stroke="' + brow + '" stroke-width="3" stroke-linecap="round" fill="none"/><path d="M107 88 Q116 84 126 89" stroke="' + brow + '" stroke-width="3" stroke-linecap="round" fill="none"/>'
      + '<path d="M100 101 Q97 113 99 117 Q102 119 106 116" stroke="' + sh + '" stroke-width="2" fill="none" stroke-linecap="round"/>'
      + '<path class="p-smile" d="M89 129 Q100 136 111 129" stroke="' + g.lip + '" stroke-width="3.2" fill="none" stroke-linecap="round"/>'
      + '<g class="p-open"><ellipse cx="100" cy="131" rx="8.5" ry="5.5" fill="#4a1c1c"/><rect x="93.5" y="126.5" width="13" height="2.6" rx="1.2" fill="#f4efe8"/></g>'
      + extra + '</g></svg>';
  }
  var MASCOT_HTML = $('avatarHolder').innerHTML;
  var GUIDES = BOOT.guides || [];
  var G = GUIDES.filter(function (g) { return g.id === BOOT.guide_id; })[0] || GUIDES[0] || { name: 'Simi', kind: 'mascot' };
  var pending = G;
  function guideAv() { return G.kind === 'mascot' ? 'S' : portrait(G, '48 36 104 104'); }
  function applyGuide(g) {
    G = g; $('avatarHolder').innerHTML = g.kind === 'mascot' ? MASCOT_HTML : portrait(g);
    $('guideName').textContent = g.name;
    document.querySelectorAll('[data-guide-name]').forEach(function (e) { e.textContent = g.name; });
    document.querySelectorAll('.row.simi .av').forEach(function (a) { a.innerHTML = guideAv(); });
    var w = cap.querySelector('.who'); if (w) w.textContent = g.name;
  }

  /* ---------- panels ---------- */
  function scroll() { msgs.scrollTop = msgs.scrollHeight; }
  function toast(t) { var e = $('toast'); e.textContent = t; e.classList.add('show'); setTimeout(function () { e.classList.remove('show'); }, 4200); }
  function showErr(t) { $('err').textContent = t || ''; }
  function grow(g) { var order = ['G', 'R', 'O', 'W']; document.querySelectorAll('.gs').forEach(function (e) {
    var i = order.indexOf(e.dataset.g), c = order.indexOf(g); e.className = 'gs' + (i < c ? ' done' : i === c ? ' on' : ''); }); }
  function renderLayers() {
    document.querySelectorAll('.lp').forEach(function (e) {
      var l = +e.dataset.l, c = (S.state.cards || {})[l] || {};
      e.className = 'lp' + (c.approved ? ' done' : '') + (l === S.layer ? ' on' : '');
      e.setAttribute('aria-pressed', l === S.layer ? 'true' : 'false');
    });
    $('layerTitle').textContent = 'Layer ' + S.layer + ' · ' + ((BOOT.layers || {})[S.layer] || '');
    $('cardTitle').textContent = 'L' + S.layer + ' goal card';
    var done = [];
    for (var l = 1; l <= 5; l++) { var c = (S.state.cards || {})[l]; if (c && c.approved) done.push('<div><b>L' + l + '</b> ' + esc(c.approved.goal) + '</div>'); }
    $('goalList').innerHTML = done.length ? '<div class="k" style="font-size:.66rem;letter-spacing:.07em;text-transform:uppercase;color:var(--mute)">Approved goals</div>' + done.join('') : '';
  }
  function renderFacts() {
    var box = $('facts'), skills = (S.state.skills || []).map(function (s) { return s.skill; });
    var rows = S.facts.filter(function (f) { return f.id !== 'skills'; }).map(function (f) {
      return '<div class="gitem" id="g-' + esc(f.id).replace(/[^a-zA-Z0-9_-]/g, '_') + '"><div class="k">' + esc(f.label) + '</div><div class="v">' + esc(f.text) + '</div></div>'; });
    rows.push('<div class="gitem" id="g-skills"><div class="k">Skills you confirmed</div><div class="v" id="skillsv">' + (skills.length ? esc(skills.join(' · ')) : '<i>None yet</i>') + '</div></div>');
    box.innerHTML = S.facts.length ? rows.join('') : '<div class="v empty" style="font-size:.82rem">Start the conversation to load your profile and simulations.</div>';
  }
  function factEl(id) { return $('g-' + String(id).replace(/[^a-zA-Z0-9_-]/g, '_')); }
  function lit(ids) { document.querySelectorAll('.gitem').forEach(function (e) { e.classList.remove('lit'); });
    (ids || []).forEach(function (i) { var e = i === 'skills' ? $('g-skills') : factEl(i); if (e) e.classList.add('lit'); }); }
  function fill(id, txt) { var v = $(id); v.textContent = txt || '—'; v.classList.toggle('empty', !txt); v.classList.remove('fill'); void v.offsetWidth; if (txt) v.classList.add('fill'); }
  function renderCard() {
    var c = (S.state.cards || {})[S.layer] || {}, d = c.draft, a = c.approved, card = d || a;
    var gc = $('gc'); gc.className = 'gc' + (d ? ' ready' : a ? ' saved' : '');
    S.draftId = d ? d.id : null;
    if (!card) { ['f-goal', 'f-measure', 'f-evidence', 'f-agents'].forEach(function (id, i) { fill(id, ''); $(id).textContent = i ? '—' : 'Waiting for the conversation…'; }); }
    else {
      fill('f-goal', card.goal);
      fill('f-measure', [card.measure, card.target && ('target ' + card.target), card.deadline && ('by ' + card.deadline)].filter(Boolean).join(' · '));
      fill('f-evidence', (card.evidence || []).map(function (e) { return e.label; }).join(' · '));
      fill('f-agents', (card.agent_changes || []).map(function (x) { return x.agent.replace(/_/g, ' ') + (x.setting ? ' → ' + x.setting + (x.new ? ': ' + x.new : '') : ''); }).join(' · '));
    }
    $('approve').disabled = !d; $('edit').disabled = !card;
    $('approve').textContent = a && !d ? 'Approved' : 'Approve goal';
  }

  /* ---------- conversation ---------- */
  function addMine(text, mode) {
    var row = document.createElement('div'); row.className = 'row me';
    row.innerHTML = '<div class="av">' + esc((BOOT.first_name || 'You').slice(0, 2).toUpperCase()) + '</div><div class="bubble"></div>';
    var b = row.querySelector('.bubble'); b.textContent = text;
    if (mode === 'voice') { var v = document.createElement('span'); v.className = 'via'; v.textContent = '🎙 Spoken · transcribed'; b.appendChild(v); }
    msgs.appendChild(row); scroll();
  }
  function addGuide(t) {
    var ep = ++S.epoch;
    lit(t.sources); grow(t.grow_step);
    if (t.layer && t.layer !== S.layer) { S.layer = t.layer; renderLayers(); renderCard(); }
    setState('speaking', 'Speaking');
    var row = document.createElement('div'); row.className = 'row simi';
    row.innerHTML = '<div class="av">' + guideAv() + '</div><div class="bubble"><span class="txt"></span></div>';
    msgs.appendChild(row);
    var b = row.querySelector('.bubble'), txt = row.querySelector('.txt'), words = t.say.split(' '), i = 0;
    cap.innerHTML = '<span class="who">' + esc(G.name) + '</span>';
    var spoken = speak(t.say);
    (function tick() {
      if (ep !== S.epoch) return;
      if (i < words.length) { txt.textContent += (i ? ' ' : '') + words[i]; cap.innerHTML = '<span class="who">' + esc(G.name) + '</span>' + esc(txt.textContent); i++; scroll(); setTimeout(tick, TICK()); return; }
      if (t.sources && t.sources.length) {
        var c = document.createElement('div'); c.className = 'cites';
        c.innerHTML = t.sources.map(function (s) { var f = S.factById[s]; return '<span class="cite" title="' + esc(f ? f.text : s) + '">From: ' + esc(f ? f.label : s) + '</span>'; }).join('');
        b.appendChild(c);
      }
      if (t.note) { var n = document.createElement('div'); n.className = 'note-l5'; n.textContent = t.note; b.appendChild(n); }
      if (t.resources) { var r = document.createElement('div'); r.className = 'resources'; r.innerHTML = 'Support is available now:' + t.resources.map(function (x) { return '<a href="' + esc(x.href) + '">' + esc(x.label) + '</a>'; }).join(''); b.appendChild(r); }
      if (t.offer_coach && !t.resources) { var o = document.createElement('div'); o.className = 'coach-offer'; o.innerHTML = 'Want to talk to a person? <a href="' + ROOT + '/transition">Your human coach</a> (if your package includes one) · <a href="mailto:simi@simulacrumai.io">simi@simulacrumai.io</a>'; b.appendChild(o); }
      if (t.fallback) { var fb = document.createElement('div'); fb.className = 'fallback-tag'; fb.textContent = 'Simi only says what it can point to in your records, so it asked a question instead.'; b.appendChild(fb); }
      if (t.skill && t.skill.id) {
        var sp = document.createElement('div'); sp.className = 'skillprop';
        sp.innerHTML = 'Add <b>' + esc(t.skill.name) + '</b> to your skills?<div class="acts"><span class="yes" role="button" tabindex="0">Add skill</span><span role="button" tabindex="0">Not now</span></div>';
        var acts = sp.querySelectorAll('.acts span');
        var decide = function (yes) {
          api('/api/guide/skills/' + t.skill.id, { accept: yes }).then(function (st) {
            S.state = st; sp.classList.add('added'); sp.innerHTML = yes ? '✓ Added <b>' + esc(t.skill.name) + '</b> to your skills' : 'Not added.';
            var sv = $('skillsv'); if (sv) sv.textContent = (st.skills || []).map(function (s) { return s.skill; }).join(' · ') || 'None yet';
            if (yes && $('g-skills')) $('g-skills').classList.add('newskill');
            send(yes ? 'Yes, that fits. Add it.' : 'No, that one doesn\'t fit me.', 'typed', yes ? 'skill accepted' : 'skill declined');
          }).catch(function (e) { showErr(e.message); });
        };
        acts[0].onclick = function () { decide(true); }; acts[1].onclick = function () { decide(false); };
        acts.forEach(function (a) { a.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); a.click(); } }; });
        b.appendChild(sp);
      }
      if (t.options && t.options.length) {
        var op = document.createElement('div'); op.className = 'options';
        t.options.forEach(function (x) { var btn = document.createElement('button'); btn.type = 'button'; btn.className = 'opt';
          btn.innerHTML = '<b>' + esc(x.key) + '</b>' + esc(x.label);
          btn.onclick = function () { op.querySelectorAll('.opt').forEach(function (o) { o.classList.remove('picked'); o.disabled = true; }); btn.classList.add('picked');
            send(x.key + '. ' + x.label, 'typed', 'picked option ' + x.key); };
          op.appendChild(btn); });
        b.appendChild(op);
      }
      scroll();
      spoken.then(function () { if (ep === S.epoch) setState('', 'Your turn'); });
    })();
  }
  function thinking() { setState('thinking', 'Thinking…'); }
  function send(text, mode, event) {
    if (S.busy) return;
    if (!S.session) { start(S.layer, text); return; }
    S.busy = true; showErr(''); hush(); S.epoch++;
    if (!event) addMine(text, mode);
    thinking(); $('send').disabled = true;
    api('/api/guide/sessions/' + S.session + '/turn', { text: text, input_mode: mode || 'typed', event: event || null })
      .then(function (d) { S.state = d.state; renderLayers(); renderCard(); addGuide(d.turn); })
      .catch(function (e) { setState('', 'Your turn'); showErr(e.message); })
      .then(function () { S.busy = false; $('send').disabled = false; });
  }
  function start(layer, firstText) {
    if (S.busy) return;
    S.busy = true; showErr(''); hush(); S.epoch++; msgs.innerHTML = ''; thinking();
    api('/api/guide/sessions', { layer: layer || null }).then(function (d) {
      S.session = d.session_id; S.layer = d.layer; S.facts = d.facts || []; S.state = d.state;
      S.factById = {}; S.facts.forEach(function (f) { S.factById[f.id] = f; });
      renderFacts(); renderLayers(); renderCard(); addGuide(d.turn);
      S.busy = false;
      if (firstText) send(firstText, 'typed');
    }).catch(function (e) { S.busy = false; setState('', 'Ready'); showErr(e.message); });
  }
  function endSession() {
    if (!S.session) return;
    try { fetch(ROOT + '/api/guide/sessions/' + S.session + '/end', { method: 'POST', credentials: 'same-origin', keepalive: true }); } catch (e) {}
  }
  window.addEventListener('pagehide', endSession);

  /* ---------- speech in (push-to-talk) ---------- */
  var Rec = window.SpeechRecognition || window.webkitSpeechRecognition, rec = null, heard = '';
  function micPress() {
    if (!S.session) { start(S.layer); return; }
    if (!Rec || !BOOT.speech_in) { $('inp').focus(); return; }
    if (rec) { rec.stop(); return; }
    hush(); S.epoch++; heard = '';
    rec = new Rec(); rec.lang = 'en-US'; rec.interimResults = true; rec.continuous = false;
    setState('listening', 'Listening');
    cap.innerHTML = '<span class="who">You (speaking)</span>';
    rec.onresult = function (ev) { var t = ''; for (var i = 0; i < ev.results.length; i++) t += ev.results[i][0].transcript;
      heard = t; cap.innerHTML = '<span class="who">You (speaking)</span>' + esc(t); };
    rec.onerror = function () { setState('', 'Your turn'); };
    rec.onend = function () { rec = null; if (heard.trim()) send(heard.trim(), 'voice'); else setState('', 'Your turn'); };
    rec.start();
  }
  $('mic').onclick = micPress;
  if (!Rec || !BOOT.speech_in) { $('mic').title = 'Voice input is not available in this browser — type instead'; }

  /* ---------- controls ---------- */
  $('send').onclick = function () { var v = $('inp').value.trim(); if (!v) return; $('inp').value = ''; send(v, 'typed'); };
  $('inp').onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); $('send').click(); } };
  $('restart').onclick = function () { endSession(); S.session = null; start(S.layer); };
  document.querySelectorAll('.lp').forEach(function (e) {
    e.onclick = function () { var l = +e.dataset.l; if (l === S.layer && S.session) return; S.layer = l; renderLayers(); renderCard();
      if (S.session) send("Let's work on Layer " + l + ' now.', 'typed'); };
  });
  $('approve').onclick = function () {
    if (!S.draftId) return;
    $('approve').disabled = true; $('approve').textContent = 'Approving…';
    api('/api/guide/goals/' + S.draftId + '/approve', {}).then(function (d) {
      S.state = d.state; renderLayers(); renderCard();
      toast('L' + d.card.layer + ' goal saved. Your L' + d.card.layer + ' agents will plan with it; any send still needs your approval.');
      send('I approved my Layer ' + d.card.layer + ' goal.', 'typed', 'goal card approved');
    }).catch(function (e) { showErr(e.message); renderCard(); });
  };
  $('edit').onclick = function () {
    var c = (S.state.cards || {})[S.layer] || {}, card = c.draft || c.approved; if (!card) return;
    var box = $('gc');
    var html = '<div class="edit-row">' + [['goal', 'Goal'], ['measure', 'How we\'ll measure it'], ['target', 'Target'], ['deadline', 'Deadline']].map(function (f) {
      return '<div class="gf"><div class="k">' + f[1] + '</div><input data-f="' + f[0] + '" value="' + esc(card[f[0]] || '') + '"></div>'; }).join('') +
      '<div class="acts"><button type="button" id="editCancel">Cancel</button><button type="button" class="ok" id="editSave">Save</button></div></div>';
    var holder = document.createElement('div'); holder.innerHTML = html; box.appendChild(holder);
    $('editCancel').onclick = function () { holder.remove(); };
    $('editSave').onclick = function () { var b = {}; holder.querySelectorAll('[data-f]').forEach(function (i) { b[i.dataset.f] = i.value; });
      api('/api/guide/goals/' + card.id, b, 'PUT').then(function (st) { S.state = st; holder.remove(); renderCard(); toast('Goal updated.'); })
        .catch(function (e) { showErr(e.message); }); };
  };
  function syncVoiceBtn() { var b = $('voiceBtn'); b.textContent = muted ? '🔇 Voice off' : '🔊 Voice on'; b.classList.toggle('off', muted); b.disabled = !SPEECH; if (!SPEECH) b.title = 'This browser cannot speak aloud'; }
  $('voiceBtn').onclick = function () { muted = !muted; if (muted) hush(); syncVoiceBtn(); api('/api/guide/prefs', { voice_enabled: !muted }).catch(function () {}); };
  syncVoiceBtn();

  /* ---------- guide picker ---------- */
  function openPicker() {
    pending = G; var grid = $('ggrid'); grid.innerHTML = '';
    GUIDES.forEach(function (g) {
      var d = document.createElement('div'); d.className = 'gopt' + (g.id === G.id ? ' sel' : ''); d.tabIndex = 0; d.setAttribute('role', 'radio');
      d.innerHTML = '<div class="pic">' + (g.kind === 'mascot' ? MASCOT_HTML : portrait(g)) + '</div><b>' + esc(g.name) + '</b><small>' + esc(g.tone || '') + '</small><button class="vprev" type="button">🔊 Hear ' + esc(g.name) + ' · ' + esc(g.voice || '') + '</button>';
      d.querySelector('.vprev').onclick = function (e) { e.stopPropagation(); hush(); var was = muted; muted = false; speak("Hi, I'm " + g.name + ". Let's figure out what your skills are really worth.", g).then(function () { muted = was; }); };
      var pick = function () { pending = g; grid.querySelectorAll('.gopt').forEach(function (x) { x.classList.remove('sel'); }); d.classList.add('sel'); $('pickName').textContent = g.name; };
      d.onclick = pick; d.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } };
      grid.appendChild(d);
    });
    $('pickName').textContent = G.name; $('picker').classList.add('show');
  }
  $('changeGuide').onclick = openPicker;
  $('pickCancel').onclick = function () { $('picker').classList.remove('show'); };
  $('pickGo').onclick = function () {
    api('/api/guide/prefs', { guide_id: pending.id }).then(function () { applyGuide(pending); $('picker').classList.remove('show'); })
      .catch(function (e) { showErr(e.message); $('picker').classList.remove('show'); });
  };
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') $('picker').classList.remove('show'); });

  applyGuide(G);
  S.layer = 1;
  for (var l = 1; l <= 5; l++) { var c = (S.state.cards || {})[l]; if (!(c && c.approved)) { S.layer = l; break; } }
  renderFacts(); renderLayers(); renderCard();
  cap.innerHTML = '<span class="who">' + esc(G.name) + '</span>Press the mic, or type below, to start talking.';
  if (!BOOT.has_chosen) setTimeout(openPicker, 400);
})();
