/**
 * Simulacrum SSE Client
 * Handles real-time streaming of simulation layers via Server-Sent Events.
 */

function startSSEStream(simulationId) {
  const statusEl = document.getElementById('streamStatus');
  const statusText = document.getElementById('streamStatusText');
  const container = document.getElementById('layersContainer');

  const evtSource = new EventSource((window.ROOT || '') + `/api/simulations/${simulationId}/stream`);
  let reconnectAttempts = 0;

  evtSource.addEventListener('simulation_start', (e) => {
    const data = JSON.parse(e.data);
    if (statusText) statusText.textContent = `Generating "${data.name}"…`;
  });

  evtSource.addEventListener('layer_start', (e) => {
    const data = JSON.parse(e.data);
    const skeleton = document.getElementById(`layer-skeleton-${data.layer_number}`);
    if (skeleton) {
      skeleton.querySelector('.skeleton-text').textContent = `Generating ${data.layer_name}…`;
    }
    if (statusText) statusText.textContent = `Generating Layer ${data.layer_number}: ${data.layer_name}…`;
  });

  evtSource.addEventListener('layer_data', (e) => {
    const layer = JSON.parse(e.data);
    const skeleton = document.getElementById(`layer-skeleton-${layer.layer_number}`);
    if (skeleton) skeleton.remove();

    const html = buildLayerHTML(layer);
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = html;
    const layerEl = tempDiv.firstElementChild;
    layerEl.classList.add('layer-appear');
    container.appendChild(layerEl);
  });

  evtSource.addEventListener('simulation_complete', (e) => {
    evtSource.close();
    if (statusEl) statusEl.classList.add('hidden');
    // Update status badge without reload
    const badge = document.querySelector('.status-badge');
    if (badge) { badge.textContent = 'Complete'; badge.className = 'status-badge status-complete'; }
  });

  evtSource.addEventListener('simulation_error', (e) => {
    const data = JSON.parse(e.data);
    evtSource.close();
    if (statusEl) {
      statusEl.innerHTML = `<span class="stream-error">Generation failed: ${data.error}${data.refunded ? ' — payment refunded automatically.' : ''}</span>`;
    }
  });

  evtSource.onerror = () => {
    reconnectAttempts++;
    if (reconnectAttempts > 5) {
      evtSource.close();
      if (statusText) statusText.textContent = 'Connection lost. Refresh to check status.';
    }
  };
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
          <button class="btn btn-ghost btn-sm" onclick="showRefine(${layer.layer_number}, '${escapeHtml(layer.simulation_id || '')}')">Refine this layer →</button>
        </div>
      </div>
    </div>
  `;
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
