/**
 * Simulacrum Simulation Poller
 * Polls GET /api/simulations/<id> every 3 s instead of holding an SSE connection open.
 * A long-lived SSE connection would monopolize Passenger's worker thread and block
 * navigation. Short polling releases the worker between requests.
 */

let _pollTimer = null;
let _seenLayerNums = new Set();

function startSSEStream(simulationId) {
  const statusEl  = document.getElementById('streamStatus');
  const statusText = document.getElementById('streamStatusText');
  const container = document.getElementById('layersContainer');

  // Kick a recovery request immediately so the server restarts generation
  // if the confirm-payment background thread died (STATUS_PROCESSING stuck).
  fetch((window.ROOT || '') + `/api/simulations/${simulationId}/recover`, { method: 'POST' })
    .catch(() => {});  // fire-and-forget — failures are non-fatal

  // Poll immediately, then every 3 s.
  _pollOnce(simulationId, statusEl, statusText, container);
  _pollTimer = setInterval(
    () => _pollOnce(simulationId, statusEl, statusText, container),
    3000,
  );
}

async function _pollOnce(simulationId, statusEl, statusText, container) {
  let data;
  try {
    const res = await fetch((window.ROOT || '') + `/api/simulations/${simulationId}`);
    if (!res.ok) return;
    data = await res.json();
  } catch (_) {
    return;  // network blip — try again next tick
  }

  // Render any newly arrived layers
  for (const layer of (data.layers || [])) {
    if (!_seenLayerNums.has(layer.layer_number)) {
      _seenLayerNums.add(layer.layer_number);

      // Remove the matching skeleton placeholder
      const skeleton = document.getElementById(`layer-skeleton-${layer.layer_number}`);
      if (skeleton) skeleton.remove();

      if (statusText) {
        statusText.textContent = `Generated Layer ${layer.layer_number}: ${layer.layer_name}…`;
      }

      const html = buildLayerHTML(layer);
      const tmp = document.createElement('div');
      tmp.innerHTML = html;
      const el = tmp.firstElementChild;
      el.classList.add('layer-appear');
      container.appendChild(el);
    }
  }

  if (data.status === 'complete') {
    _stopPolling();
    if (statusEl) statusEl.classList.add('hidden');
    const badge = document.querySelector('.status-badge');
    if (badge) { badge.textContent = 'Complete'; badge.className = 'status-badge status-complete'; }
    // Show header actions (Export / Share / GCC) without a full reload
    const headerActions = document.querySelector('.header-actions');
    if (headerActions && !headerActions.querySelector('.btn-primary')) {
      headerActions.innerHTML = `
        <button class="btn btn-ghost" onclick="exportSim('${simulationId}')">Export PDF</button>
        <button class="btn btn-ghost" onclick="shareSimulation('${simulationId}')">Share</button>
        <a href="${window.ROOT || ''}/simulations/${simulationId}/layer6" class="btn btn-primary">⚡ Growth Command Center</a>
      `;
    }
    return;
  }

  if (data.status === 'error' || data.status === 'refunded') {
    _stopPolling();
    if (statusEl) {
      statusEl.innerHTML = `<span class="stream-error">Generation failed.${data.status === 'refunded' ? ' Payment refunded automatically.' : ' Please contact support.'}</span>`;
    }
  }
}

function _stopPolling() {
  if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

function buildLayerHTML(layer) {
  const streams = (layer.income_streams || []).map(s => `
    <div class="stream-card">
      <div class="stream-header" onclick="toggleReasoning(this)">
        <div class="stream-title-row">
          <h4 class="stream-name">${escapeHtml(s.name)}</h4>
          ${s.est_monthly_low ? `<span class="stream-range">$${s.est_monthly_low.toLocaleString()}–$${s.est_monthly_high.toLocaleString()}/mo</span>` : ''}
        </div>
        <p class="stream-desc">${escapeHtml(s.description || '')}</p>
        ${s.platform ? `<span class="stream-platform">${escapeHtml(s.platform)}</span>` : ''}
      </div>
      <div class="stream-reasoning hidden">
        <div class="reasoning-label">AI Reasoning</div>
        <p class="reasoning-text">${escapeHtml(s.ai_reasoning)}</p>
        ${s.deliverable_refs && s.deliverable_refs.length ? `
          <div class="deliverable-refs">
            <strong>Evidence:</strong>
            <ul>${s.deliverable_refs.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
          </div>` : ''}
        <div class="stream-meta-row">
          ${s.automation_level ? `<span class="meta-tag">Automation: ${s.automation_level}</span>` : ''}
          ${s.launch_timeline_weeks ? `<span class="meta-tag">Launch: ${s.launch_timeline_weeks}w</span>` : ''}
        </div>
      </div>
    </div>
  `).join('');

  return `
    <div class="layer-card layer-${layer.layer_number}" id="layer-card-${layer.layer_number}">
      <div class="layer-card-header" onclick="toggleLayer(${layer.layer_number})">
        <div class="layer-title-row">
          <span class="layer-num-badge">L${layer.layer_number}</span>
          <h3 class="layer-title">${escapeHtml(layer.layer_name)}</h3>
        </div>
        <span class="layer-toggle">▾</span>
      </div>
      <div class="layer-card-body" id="layer-body-${layer.layer_number}">
        ${layer.ai_narrative ? `<p class="layer-narrative">${escapeHtml(layer.ai_narrative)}</p>` : ''}
        <div class="income-streams">${streams}</div>
        <div class="layer-refine">
          <button class="btn btn-ghost btn-sm" onclick="showRefine(${layer.layer_number}, '${escapeHtml(layer.id || '')}')">Refine this layer →</button>
        </div>
      </div>
    </div>
  `;
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
