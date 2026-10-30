/* Artifact action bar (EXEC_PLAN P1) — safe actions only.
 *
 * One module for every place an artifact is viewed:
 *   - the artifact page: full bar (refine, export PDF, copy, duplicate) wired by
 *     ArtifactActions.initPage(...)
 *   - Journey rows, the cycle activity list and the Agent Network drawer:
 *     ArtifactActions.compact(simId, actionId) returns "View · PDF" links.
 * Nothing here sends email, posts, or calls an integration.
 */
(function () {
  'use strict';

  function root() { return (window.ROOT || '').replace(/\/+$/, ''); }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  async function postJson(url, body) {
    const r = await fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const d = await r.json().catch(function () { return {}; });
    if (!r.ok) throw new Error(d.message || d.error || ('Request failed (' + r.status + ')'));
    return d;
  }

  function api(id, suffix) { return root() + '/api/artifacts/' + id + (suffix || ''); }

  /* ── Compact links for lists and drawers ─────────────────────────────── */
  function compact(simId, actionId) {
    if (!actionId) return '';
    return '<span class="aab-compact">' +
      '<a class="aab-link" href="' + root() + '/simulations/' + esc(simId) + '/artifacts/' + esc(actionId) + '">View artifact →</a>' +
      ' <span class="aab-sep">·</span> ' +
      '<a class="aab-link" href="' + api(esc(actionId), '/export.pdf') + '" title="Download as PDF">PDF</a>' +
      '</span>';
  }

  /* ── Full bar on the artifact page ───────────────────────────────────── */
  function initPage(opts) {
    const id = opts.artifactId;
    const getEditorText = opts.getEditorText || function () { return null; };

    const copyBtn = document.getElementById('aabCopyBtn');
    if (copyBtn) copyBtn.addEventListener('click', async function () {
      try {
        let text = getEditorText();
        if (text == null) {
          const r = await fetch(api(id), { credentials: 'same-origin' });
          text = (await r.json()).current_payload || '';
        }
        await navigator.clipboard.writeText(text);
        flash(copyBtn, 'Copied');
      } catch (e) { alert('Copy failed: ' + e.message); }
    });

    const dupBtn = document.getElementById('aabDuplicateBtn');
    if (dupBtn) dupBtn.addEventListener('click', async function () {
      if (!confirm('Create a copy of this artifact? The original stays as it is.')) return;
      dupBtn.disabled = true;
      try {
        const d = await postJson(api(id, '/duplicate'));
        window.location.href = d.url;
      } catch (e) { alert(e.message); dupBtn.disabled = false; }
    });

    const refineBtn = document.getElementById('aabRefineBtn');
    if (refineBtn) refineBtn.addEventListener('click', function () { openRefine(id, getEditorText); });

    const sendBtn = document.getElementById('aabSendBtn');
    if (sendBtn) sendBtn.addEventListener('click', function () { openSendDrafts(id, getEditorText, sendBtn); });

    const planBtn = document.getElementById('aabPlanBtn');
    if (planBtn) planBtn.addEventListener('click', function () { openPlan(id); });
  }

  /* ── Plan (EXEC_PLAN P3 — L1: suggestions; nothing executes) ─────────── */
  const BLAST = ['none', 'one person', 'a list', 'public'];
  const STATUS_LABEL = { not_required: 'no approval needed', pending: 'awaiting your decision',
                         approved: 'approved', rejected: 'rejected', unavailable: 'unavailable' };
  const REASON_LABEL = { hard_gate: 'always confirmed', irreversible: 'cannot be undone', blast: 'reaches many people' };

  async function openPlan(id) {
    let modal = document.getElementById('aabPlanModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'aabPlanModal';
      modal.className = 'aab-modal';
      modal.innerHTML = '<div class="aab-dialog"><h3>Agent plan</h3>' +
        '<p class="aab-hint aab-nosend" id="aabPlanNote"></p><div id="aabPlanBody">Loading…</div><div id="aabPlanMsg" class="aab-msg"></div>' +
        '<div class="aab-actions"><button type="button" class="av-btn" id="aabPlanRegen">Rebuild plan</button>' +
        '<button type="button" class="av-btn primary" id="aabPlanClose">Close</button></div></div>';
      document.body.appendChild(modal);
      modal.querySelector('#aabPlanClose').addEventListener('click', function () { modal.style.display = 'none'; });
    }
    const body = modal.querySelector('#aabPlanBody'), msg = modal.querySelector('#aabPlanMsg');

    const RUN_FIELDS = {
      stripe_payment_link: [['product_name', 'Product name', 'text'], ['amount_usd', 'Price (USD)', 'number'],
                            ['description', 'Description (optional)', 'text']],
      calcom_booking_link: [['title', 'Session title', 'text'], ['length_minutes', 'Length (minutes)', 'number'],
                            ['price_usd', 'Price (USD, 0 = free)', 'number'], ['description', 'Description (optional)', 'text']],
    };
    const RUN_PREVIEW = {
      stripe_payment_link: function (v) { return 'This creates a payment link for <b>' + esc(v.product_name) + '</b> at <b>$' +
        esc(v.amount_usd) + '</b> on <b>your connected Stripe account</b>. No money moves until a customer pays; you can deactivate the link with Undo.'; },
      calcom_booking_link: function (v) { return 'This creates a bookable <b>' + esc(v.length_minutes) + '-minute "' + esc(v.title) +
        '"</b> session on <b>your Cal.com account</b>. You can delete it with Undo.'; },
    };

    function runPanel(s) {
      const r = s.last_run;
      if (r && r.status === 'succeeded') {
        const url = r.response && r.response.url;
        return '<div class="aab-run aab-run-ok">✓ Done' + (url ? ' — <a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(url) + '</a>' : '') +
          ' <button type="button" class="av-btn aab-undo" data-run="' + esc(r.id) + '">Undo</button></div>';
      }
      if (!s.executable || s.approval_status !== 'approved') {
        return (r && r.status === 'undone') ? '<div class="aab-hint">Undone.</div>' : '';
      }
      const v = s.default_inputs || {};
      const fields = (RUN_FIELDS[s.tool_key] || []).map(function (f) {
        return '<label class="aab-field">' + esc(f[1]) + '<input class="aab-input aab-in" data-k="' + f[0] + '" type="' + f[2] +
               '" value="' + esc(v[f[0]] == null ? '' : v[f[0]]) + '"></label>';
      }).join('');
      const prev = (r && (r.status === 'failed' || r.status === 'blocked'))
        ? '<div class="aab-warn">Last attempt ' + esc(r.status) + ': ' + esc(r.error || r.guardrail_result || '') + '</div>' : '';
      return '<div class="aab-run" data-step="' + esc(s.id) + '" data-tool="' + esc(s.tool_key) + '">' + prev + fields +
        '<div class="aab-preview"></div>' +
        '<button type="button" class="av-btn primary aab-run-btn">Run this step</button></div>';
    }

    function wireRuns() {
      body.querySelectorAll('.aab-run[data-step]').forEach(function (panel) {
        const tool = panel.dataset.tool;
        const preview = panel.querySelector('.aab-preview');
        function values() {
          const v = {};
          panel.querySelectorAll('.aab-in').forEach(function (i) { v[i.dataset.k] = i.value; });
          return v;
        }
        function refresh() { preview.innerHTML = RUN_PREVIEW[tool] ? RUN_PREVIEW[tool](values()) : ''; }
        panel.querySelectorAll('.aab-in').forEach(function (i) { i.addEventListener('input', refresh); });
        refresh();
        panel.querySelector('.aab-run-btn').addEventListener('click', async function () {
          if (!confirm('Run this step now?\n\n' + preview.textContent)) return;
          this.disabled = true; this.textContent = 'Running…'; msg.textContent = '';
          try {
            const r = await fetch(root() + '/api/plan-steps/' + panel.dataset.step + '/execute', {
              method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ inputs: values() }) });
            const d = await r.json();
            if (!r.ok && r.status !== 502) throw new Error(d.message || d.error);
            if (d.status !== 'succeeded') msg.textContent = 'Step ' + d.status + ': ' + (d.error || d.guardrail_result || '');
          } catch (e) { msg.textContent = e.message; }
          await reload();
        });
      });
      body.querySelectorAll('.aab-undo').forEach(function (b) {
        b.addEventListener('click', async function () {
          if (!confirm('Undo this step? The link will stop working.')) return;
          msg.textContent = '';
          try { await postJson(root() + '/api/execution-runs/' + b.dataset.run + '/undo'); }
          catch (e) { msg.textContent = e.message; }
          await reload();
        });
      });
    }

    async function reload() {
      const r = await fetch(api(id, '/plan'), { credentials: 'same-origin' });
      render(await r.json());
    }

    function render(p) {
      modal.querySelector('#aabPlanNote').textContent = p.executes
        ? 'Only approved, reversible steps (a payment link, a booking link) can run here, on your own accounts. ' +
          'Sending, signing, posting and trading never run from a plan.'
        : 'Plans are suggestions at this autonomy level — approving a step records your decision; nothing runs. ' +
          'Raise the agent to L2 in Settings → Agent Autonomy to run reversible steps.';
      let html = '<div class="aab-label">' + esc(p.goal || '') + '</div>' +
        '<div class="aab-hint">Strategy <b>' + esc((p.method || '').replace(/_/g, ' ')) + '</b> · autonomy <b>' +
        esc(p.autonomy_level) + '</b> (' + esc(p.autonomy_label) + ') · plan <b>' + esc(p.status) + '</b></div>';
      html += p.steps.map(function (s) {
        const decide = (s.approval_status === 'pending' || s.approval_status === 'approved' || s.approval_status === 'rejected')
          ? '<button type="button" class="av-btn aab-dec" data-step="' + s.id + '" data-d="approve"' +
              (s.approval_status === 'approved' ? ' disabled' : '') + '>Approve</button> ' +
            '<button type="button" class="av-btn aab-dec" data-step="' + s.id + '" data-d="reject"' +
              (s.approval_status === 'rejected' ? ' disabled' : '') + '>Reject</button>'
          : '';
        return '<div class="aab-draft"><div class="aab-draft-head"><b>' + s.seq + '. ' +
          esc(s.method.replace(/_/g, ' ')) + '</b> · ' + esc(s.tool_key ? s.tool_key.replace(/_/g, ' ') : 'manual step') +
          ' <span class="aab-badge">' + esc(STATUS_LABEL[s.approval_status] || s.approval_status) + '</span></div>' +
          '<div>' + esc(s.expected || '') + '</div>' +
          '<div class="aab-hint">' + esc(s.reversibility) + ' · reach: ' + esc(BLAST[s.blast] || s.blast) +
          (s.est_cost_cents ? ' · est. ' + (s.est_cost_cents / 100).toFixed(2) + ' USD' : '') +
          (s.required_reason ? ' · <span class="aab-warn">' + esc(REASON_LABEL[s.required_reason] || s.required_reason) + '</span>' : '') +
          '</div>' + (decide ? '<div style="margin-top:6px">' + decide + '</div>' : '') + runPanel(s) + '</div>';
      }).join('');
      body.innerHTML = html;
      wireRuns();
      body.querySelectorAll('.aab-dec').forEach(function (b) {
        b.addEventListener('click', async function () {
          msg.textContent = '';
          try { render(await postJson(root() + '/api/plan-steps/' + b.dataset.step + '/decision', { decision: b.dataset.d })); }
          catch (e) { msg.textContent = e.message; }
        });
      });
    }

    modal.querySelector('#aabPlanRegen').onclick = async function () {
      msg.textContent = '';
      try { render(await postJson(api(id, '/plan/regenerate'))); } catch (e) { msg.textContent = e.message; }
    };
    msg.textContent = ''; body.textContent = 'Loading…';
    modal.style.display = 'flex';
    try {
      const r = await fetch(api(id, '/plan'), { credentials: 'same-origin' });
      const d = await r.json();
      if (!r.ok) throw new Error(d.message || d.error);
      render(d);
    } catch (e) { body.textContent = ''; msg.textContent = e.message; }
  }

  /* ── Send to contacts: drafts (EXEC_PLAN P2) + confirmed send (P5) ───── */
  const NOTHING_SENT = 'Preparing makes drafts only. Nothing is sent until you review the drafts and confirm the send.';

  async function openSendDrafts(id, getEditorText, btn) {
    let modal = document.getElementById('aabSendModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'aabSendModal';
      modal.className = 'aab-modal';
      modal.innerHTML =
        '<div class="aab-dialog">' +
          '<h3>Send to contacts</h3>' +
          '<p class="aab-hint aab-nosend">' + NOTHING_SENT + '</p>' +
          '<div id="aabSendStep1">' +
            '<input id="aabContactSearch" class="aab-input" placeholder="Search contacts by name, email or company">' +
            '<div id="aabContactList" class="aab-list">Loading…</div>' +
            '<div class="aab-label">Subject</div>' +
            '<input id="aabSubject" class="aab-input" maxlength="300" placeholder="e.g. Quick intro, [First Name]">' +
            '<div class="aab-label">Message <span class="aab-hint">— merge fields: [First Name], [Company], {{first_name}} …</span></div>' +
            '<textarea id="aabBody" rows="10"></textarea>' +
          '</div>' +
          '<div id="aabSendStep2" style="display:none"></div>' +
          '<div id="aabSendMsg" class="aab-msg"></div>' +
          '<div class="aab-actions">' +
            '<button type="button" class="av-btn" id="aabSendClose">Close</button>' +
            '<button type="button" class="av-btn" id="aabSendBack" style="display:none">Back</button>' +
            '<button type="button" class="av-btn" id="aabSendDiscard" style="display:none">Discard these drafts</button>' +
            '<button type="button" class="av-btn primary" id="aabSendPrepare">Prepare drafts</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(modal);
      modal.querySelector('#aabSendClose').addEventListener('click', function () { modal.style.display = 'none'; });
    }
    const $ = function (sel) { return modal.querySelector(sel); };
    const step1 = $('#aabSendStep1'), step2 = $('#aabSendStep2'), msg = $('#aabSendMsg');
    const prepare = $('#aabSendPrepare'), back = $('#aabSendBack'), discard = $('#aabSendDiscard');
    let maxRecipients = 50, lastBatch = null;

    function showStep(n) {
      step1.style.display = n === 1 ? '' : 'none';
      step2.style.display = n === 2 ? '' : 'none';
      prepare.style.display = n === 1 ? '' : 'none';
      back.style.display = n === 2 ? '' : 'none';
      discard.style.display = n === 2 ? '' : 'none';
    }

    async function loadContacts(q) {
      const list = $('#aabContactList');
      try {
        const r = await fetch(api(id, '/contacts') + '?q=' + encodeURIComponent(q || ''), { credentials: 'same-origin' });
        const d = await r.json();
        if (!r.ok) throw new Error(d.message || d.error);
        maxRecipients = d.max_recipients || maxRecipients;
        if (!d.contacts.length) { list.innerHTML = '<div class="aab-hint">No contacts match.</div>'; return; }
        list.innerHTML = d.contacts.map(function (c) {
          return '<label class="aab-row' + (c.eligible ? '' : ' aab-off') + '">' +
            '<input type="checkbox" value="' + esc(c.id) + '"' + (c.eligible ? '' : ' disabled') + '> ' +
            '<span><b>' + esc(c.name || '(no name)') + '</b> · ' + esc(c.email || '') +
            (c.company ? ' · ' + esc(c.company) : '') +
            (c.eligible ? '' : ' <em>— ' + esc(c.reason) + '</em>') + '</span></label>';
        }).join('');
      } catch (e) { list.innerHTML = '<div class="aab-msg">' + esc(e.message) + '</div>'; }
    }

    function renderDrafts(drafts, skipped) {
      let html = '<div class="aab-label">' + drafts.length + ' draft' + (drafts.length === 1 ? '' : 's') +
                 ' prepared · not sent yet</div>';
      if (skipped && skipped.length) {
        html += '<div class="aab-hint">Skipped: ' + skipped.map(function (s) {
          return esc((s.name || s.id) + ' (' + s.reason + ')'); }).join(', ') + '</div>';
      }
      html += drafts.map(function (u) {
        return '<div class="aab-draft">' +
          '<div class="aab-draft-head">To <b>' + esc(u.name || '') + '</b> &lt;' + esc(u.to || '') + '&gt;' +
          ' <span class="aab-badge">Draft — not sent</span></div>' +
          '<div><b>' + esc(u.subject || '') + '</b></div>' +
          (u.warnings && u.warnings.length ? '<div class="aab-warn">⚠ ' + esc(u.warnings.join('; ')) + '</div>' : '') +
          '<pre class="aab-body">' + esc(u.body || '') + '</pre></div>';
      }).join('');
      step2.innerHTML = html + '<div id="aabSendPanel" class="aab-send-panel"></div>';
      renderSendPanel();
    }

    // Confirm-and-send (P5): one panel per open batch, bound to the exact
    // drafts shown via the batch hash the server returns.
    async function renderSendPanel() {
      const panel = $('#aabSendPanel');
      if (!panel) return;
      let d;
      try {
        const r = await fetch(api(id, '/send-drafts'), { credentials: 'same-origin' });
        d = await r.json();
        if (!r.ok) throw new Error(d.message || d.error);
      } catch (e) { panel.innerHTML = '<div class="aab-msg">' + esc(e.message) + '</div>'; return; }
      const sender = d.sender || {};
      const batches = (d.batches || []).filter(function (b) { return !lastBatch || b.batch_id === lastBatch; });
      if (!batches.length) { panel.innerHTML = ''; return; }
      if (!sender.ready) {
        let html = '<div class="aab-label">Sending account</div><div class="aab-hint">' + esc(sender.message || '') + '</div>';
        if (sender.provider === 'own_sendgrid') {
          html += '<input id="aabSgKey" class="aab-input" type="password" autocomplete="off" placeholder="SendGrid API key (SG.…)">' +
                  '<input id="aabSgFrom" class="aab-input" placeholder="Sender email verified in your SendGrid">' +
                  '<input id="aabSgName" class="aab-input" placeholder="Sender name (optional)">' +
                  '<button type="button" class="av-btn" id="aabSgConnect">Connect SendGrid</button>';
        }
        panel.innerHTML = html;
        const c = panel.querySelector('#aabSgConnect');
        if (c) c.onclick = async function () {
          msg.textContent = ''; c.disabled = true;
          try {
            await postJson(root() + '/api/send-account', { api_key: panel.querySelector('#aabSgKey').value,
              from_email: panel.querySelector('#aabSgFrom').value, from_name: panel.querySelector('#aabSgName').value });
            renderSendPanel();
          } catch (e) { msg.textContent = e.message; c.disabled = false; }
        };
        return;
      }
      panel.innerHTML = batches.map(function (b) {
        return '<div class="aab-send-batch" data-batch="' + esc(b.batch_id) + '" data-hash="' + esc(b.hash) + '" data-count="' + b.count + '">' +
          '<label class="aab-row"><input type="checkbox" class="aab-send-ok"> <span>I reviewed ' +
          (b.count === 1 ? 'this email' : 'these ' + b.count + ' emails') + '. Send from <b>' + esc(sender.from_name || '') +
          '</b> &lt;' + esc(sender.from_email || '') + '&gt; via ' + esc(sender.label || '') + '.</span></label>' +
          '<div class="aab-hint">Each email includes an unsubscribe link. Drafts with unresolved merge fields, and contacts who became ineligible, are skipped.</div>' +
          '<button type="button" class="av-btn primary aab-send-go" disabled>Send ' + b.count + ' email' + (b.count === 1 ? '' : 's') + '</button>' +
          '</div>';
      }).join('');
      panel.querySelectorAll('.aab-send-batch').forEach(function (el) {
        const ok = el.querySelector('.aab-send-ok'), go = el.querySelector('.aab-send-go');
        ok.onchange = function () { go.disabled = !ok.checked; };
        go.onclick = async function () {
          const n = el.dataset.count;
          if (!confirm('Send ' + n + ' email' + (n === '1' ? '' : 's') + ' now? This cannot be undone.')) return;
          msg.textContent = ''; go.disabled = true; go.textContent = 'Sending…';
          try {
            const r = await postJson(api(id, '/send-drafts/send'),
                                     { batch_id: el.dataset.batch, confirm_hash: el.dataset.hash, confirm: true });
            el.innerHTML = '<div class="aab-label">Sent ' + r.sent + ' of ' + r.total + '</div>' +
              (r.blocked ? '<div class="aab-msg">Stopped by a guardrail: ' + esc(r.blocked) + '</div>' : '') +
              r.results.filter(function (x) { return x.status !== 'sent'; }).map(function (x) {
                return '<div class="aab-hint">' + esc(x.to || x.contact_id) + ' — ' + esc(x.status) + (x.error ? ': ' + esc(x.error) : '') + '</div>';
              }).join('');
            step2.querySelectorAll('.aab-badge').forEach(function (bd) { bd.textContent = 'Processed'; });
            discard.style.display = 'none';
            if (btn) btn.dataset.drafts = '0';
          } catch (e) { msg.textContent = e.message; go.disabled = false; go.textContent = 'Send ' + n + ' email' + (n === '1' ? '' : 's'); }
        };
      });
    }

    msg.textContent = '';
    showStep(1);
    if (!$('#aabBody').value) {
      let text = getEditorText();
      if (text == null) {
        try { text = (await (await fetch(api(id), { credentials: 'same-origin' })).json()).current_payload || ''; }
        catch (e) { text = ''; }
      }
      $('#aabBody').value = text;
    }
    modal.style.display = 'flex';
    loadContacts('');
    let t = null;
    $('#aabContactSearch').oninput = function () {
      clearTimeout(t); const q = this.value; t = setTimeout(function () { loadContacts(q); }, 250);
    };

    // Existing drafts open straight on the preview.
    if (btn && btn.dataset.drafts && parseInt(btn.dataset.drafts, 10) > 0) {
      try {
        const d = await (await fetch(api(id, '/send-drafts'), { credentials: 'same-origin' })).json();
        if (d.drafts && d.drafts.length) { renderDrafts(d.drafts, []); lastBatch = null; showStep(2); }
      } catch (e) { /* stay on step 1 */ }
    }

    prepare.onclick = async function () {
      msg.textContent = '';
      const ids = Array.prototype.map.call(modal.querySelectorAll('#aabContactList input:checked'),
                                           function (el) { return el.value; });
      if (ids.length > maxRecipients) { msg.textContent = 'Pick at most ' + maxRecipients + ' contacts.'; return; }
      prepare.disabled = true; prepare.textContent = 'Preparing…';
      try {
        const d = await postJson(api(id, '/send-drafts'), {
          contact_ids: ids, subject: $('#aabSubject').value, body: $('#aabBody').value });
        lastBatch = d.batch_id;
        renderDrafts(d.drafts, d.skipped);
        showStep(2);
        if (btn) btn.dataset.drafts = String((parseInt(btn.dataset.drafts || '0', 10) || 0) + d.drafts.length);
      } catch (e) { msg.textContent = e.message; }
      prepare.disabled = false; prepare.textContent = 'Prepare drafts';
    };
    back.onclick = function () { showStep(1); };
    discard.onclick = async function () {
      if (!confirm('Discard ' + (lastBatch ? 'these drafts' : 'all open drafts') + '?')) return;
      try {
        await postJson(api(id, '/send-drafts/discard'), lastBatch ? { batch_id: lastBatch } : {});
        if (btn) btn.dataset.drafts = '0';
        step2.innerHTML = '<div class="aab-hint">Drafts discarded.</div>';
        discard.style.display = 'none';
      } catch (e) { msg.textContent = e.message; }
    };
  }

  function flash(btn, label) {
    const old = btn.textContent;
    btn.textContent = label;
    setTimeout(function () { btn.textContent = old; }, 1400);
  }

  /* ── AI refine: propose → review → accept as a new version ───────────── */
  function openRefine(id, getEditorText) {
    let modal = document.getElementById('aabRefineModal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'aabRefineModal';
      modal.className = 'aab-modal';
      modal.innerHTML =
        '<div class="aab-dialog">' +
          '<h3>Refine with AI</h3>' +
          '<p class="aab-hint">Describe the change. You will see the revision before anything is saved; ' +
          'accepting it creates a new version, and the agent\'s original is kept.</p>' +
          '<textarea id="aabInstruction" rows="3" maxlength="2000" placeholder="e.g. Make the tiers more concise and raise Tier 3 to $95,000"></textarea>' +
          '<div id="aabProposalWrap" style="display:none"><div class="aab-label">Proposed revision</div>' +
          '<textarea id="aabProposal" rows="14"></textarea></div>' +
          '<div id="aabRefineMsg" class="aab-msg"></div>' +
          '<div class="aab-actions">' +
            '<button type="button" class="av-btn" id="aabRefineCancel">Cancel</button>' +
            '<button type="button" class="av-btn" id="aabRefineGo">Generate revision</button>' +
            '<button type="button" class="av-btn primary" id="aabRefineAccept" style="display:none">Accept as new version</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(modal);
      modal.querySelector('#aabRefineCancel').addEventListener('click', function () { modal.style.display = 'none'; });
    }
    const go = modal.querySelector('#aabRefineGo');
    const accept = modal.querySelector('#aabRefineAccept');
    const msg = modal.querySelector('#aabRefineMsg');
    const instr = modal.querySelector('#aabInstruction');
    const wrap = modal.querySelector('#aabProposalWrap');
    const proposal = modal.querySelector('#aabProposal');
    wrap.style.display = 'none'; accept.style.display = 'none'; msg.textContent = '';
    go.disabled = false; go.textContent = 'Generate revision';
    modal.style.display = 'flex';
    instr.focus();

    go.onclick = async function () {
      msg.textContent = ''; go.disabled = true; go.textContent = 'Working…';
      try {
        const body = { instruction: instr.value };
        const editor = getEditorText();
        if (editor != null) body.content = editor;      // refine what is on screen
        const d = await postJson(api(id, '/refine'), body);
        proposal.value = d.proposed;
        wrap.style.display = 'block'; accept.style.display = '';
        go.textContent = 'Try again';
      } catch (e) {
        msg.textContent = e.message; go.textContent = 'Generate revision';
      }
      go.disabled = false;
    };

    accept.onclick = async function () {
      accept.disabled = true;
      try {
        await postJson(api(id, '/save'), { payload: proposal.value, origin: 'agent_refine',
                                            instruction: instr.value });
        window.location.reload();
      } catch (e) { msg.textContent = e.message; accept.disabled = false; }
    };
  }

  window.ArtifactActions = { compact: compact, initPage: initPage };
})();
