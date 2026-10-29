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
