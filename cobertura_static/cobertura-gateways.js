'use strict';
// ─── Tab 2: Gateways — Master-detail (datos reales de gateways + health) ──────
//  Backend: { gateways:[{gateway_id,name,device_type,wg_ip,priority,max_streams,
//  status,score,maintenance,last_heartbeat,online,display_status,active_streams,
//  heartbeat_age_s, cpu_pct,ram_pct,temp_c,external_ip,latency_ms,
//  wg_handshake_age_s,packet_loss_pct,socks5_ok,internet_ok,...}] }
//  Regla OFFLINE por heartbeat>90s ya viene resuelta (gw.online/display_status).
(function () {
  let _data     = null;
  let _uptime   = null;   // { timelines:{gw:[{uptime_pct,online}]}, uptime_pct:{gw:n} }
  let _selected = null;

  window.renderGateways = async function (force) {
    const el = document.getElementById('tab-gateways');
    if (!_data) el.innerHTML = '<div class="loading-ph">Cargando gateways…</div>';
    if (!_data || force) {
      const [gw, up] = await Promise.all([
        apiFetch('/api/cobertura/gateways'),
        apiFetch('/api/cobertura/gateways/uptime', { period: '24h' }).catch(() => null),
      ]);
      _data = gw; _uptime = up;
    }
    const list = _data.gateways || [];
    if (!_selected && list.length) _selected = list[0].gateway_id;
    _render();
  };

  function _render() {
    const el  = document.getElementById('tab-gateways');
    const gws = _data.gateways || [];
    const sel = gws.find(g => g.gateway_id === _selected) || gws[0];

    if (!gws.length) {
      el.innerHTML = '<div class="section-content"><div class="empty-state">No hay gateways configurados.</div></div>';
      return;
    }

    el.innerHTML = `
      <div class="section-content">
        <div class="gw-layout">
          <div class="gw-list">
            <div class="gw-list-hdr">
              Gateways <span class="count-badge">${gws.length}</span>
            </div>
            ${gws.map(g => listItem(g, g.gateway_id === _selected)).join('')}
          </div>
          <div class="gw-detail">
            ${sel ? detail(sel) : '<div class="empty-state">Selecciona un gateway</div>'}
          </div>
        </div>
      </div>
    `;

    el.querySelectorAll('.gw-item').forEach(item => {
      item.addEventListener('click', function () {
        _selected = this.dataset.id;
        _render();
      });
    });
  }

  // ─── List item ─────────────────────────────────────────────────────────────
  function listItem(gw, active) {
    const c = stateColor(gw);
    const statusLabel = gw.maintenance ? 'MAINT' : (gw.online ? 'ONLINE' : 'OFFLINE');
    const streams = gw.active_streams || 0;
    const streamsTxt = streams > 0
      ? `<span class="mono" style="font-size:10px;color:var(--txtt)">${streams} stream${streams===1?'':'s'}</span>`
      : '';
    return `
      <div class="gw-item${active ? ' active' : ''}" data-id="${gw.gateway_id}">
        <div class="gw-item-top">
          <span class="dot" style="background:${c};box-shadow:0 0 6px ${c}"></span>
          <span class="gw-item-name">${gw.name}</span>
          <span class="gw-id-chip mono">${gw.gateway_id}</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
          <span style="font-size:11px;font-weight:700;color:${c}">${statusLabel}</span>
          ${streamsTxt}
          <span class="mono txt-t" style="font-size:10px;margin-left:auto">score ${gw.score ?? '—'}</span>
        </div>
      </div>
    `;
  }

  // ─── Detail panel ──────────────────────────────────────────────────────────
  function detail(gw) {
    const tl        = uptime24(gw.gateway_id);
    const uptimePct = (_uptime && _uptime.uptime_pct && _uptime.uptime_pct[gw.gateway_id] != null)
      ? Number(_uptime.uptime_pct[gw.gateway_id]).toFixed(1)
      : (tl.filter(u => u === 'up').length / Math.max(tl.length, 1) * 100).toFixed(1);
    const downCount = tl.filter(u => u === 'down').length;
    const sc  = stateColor(gw);
    const sbg = gw.maintenance ? 'var(--warning-bg)' : (gw.online ? 'var(--success-bg)' : 'var(--danger-bg)');

    return `
      <div class="gw-detail-inner" style="border-color:${sc}40">
        <!-- Header -->
        <div class="gw-detail-hdr">
          <div class="dot lg" style="background:${sc};box-shadow:0 0 8px ${sc}"></div>
          <div>
            <div class="gw-detail-name">${gw.name}</div>
            <div class="mono txt-t" style="font-size:11px;margin-top:2px">
              Priority ${gw.priority ?? '—'} · max ${gw.max_streams ?? '—'} streams · ${(gw.device_type || '').toUpperCase()}
            </div>
          </div>
          <span class="status-badge" style="background:${sbg};border-color:${sc}40;color:${sc};margin-left:auto">
            ${gw.display_status || (gw.online ? 'ONLINE' : 'OFFLINE')}${gw.agentless ? ' · sin agente' : ''}
          </span>
        </div>

        ${gw.online ? onlineBody(gw, uptimePct) : offlineBody(gw)}

        <!-- Assigned streams -->
        <div>
          <div class="gw-section-lbl">Streams asignados</div>
          ${(gw.active_streams || 0) > 0
            ? `<span class="stream-tag">${gw.active_streams} stream${gw.active_streams === 1 ? '' : 's'} asignado${gw.active_streams === 1 ? '' : 's'}</span>`
            : '<div class="empty-state small">— sin streams asignados actualmente</div>'}
        </div>

        <!-- Timeline 24h -->
        <div>
          <div class="gw-section-lbl">
            Disponibilidad 24h
            <span class="mono" style="font-size:11px;font-weight:400;margin-left:8px;color:var(--text-secondary)">
              Uptime: ${uptimePct}% · Caídas: ${downCount}
            </span>
          </div>
          <div class="tl-24h">
            ${tl.map((u, i) =>
              `<div class="tl-cell ${u === 'none' ? '' : u}"${u === 'none' ? ' style="background:var(--s3);opacity:.5"' : ''}
                    title="${pad(i)}:00–${pad(i+1)}:00 · ${u === 'up' ? 'Operativo' : u === 'down' ? 'Caído' : 'Sin dato'}"></div>`
            ).join('')}
          </div>
          <div class="tl-labels">
            ${[0,4,8,12,16,20,23].map(h => `<span>${pad(h)}:00</span>`).join('')}
          </div>
        </div>
      </div>
    `;
  }

  function onlineBody(gw, uptimePct) {
    const wgOk = gw.wg_handshake_age_s == null ? !!gw.online : gw.wg_handshake_age_s < 300;
    return `
      <div class="metric-grid">
        ${mc('VPN IP',       gw.wg_ip || '—')}
        ${mc('IP Pública',   gw.external_ip || '—')}
        ${mc('Latencia',     gw.latency_ms != null ? gw.latency_ms + ' ms' : '—')}
        ${mc('WG handshake', gw.wg_handshake_age_s != null ? gw.wg_handshake_age_s + 's' : '—')}
        ${mc('Packet loss',  gw.packet_loss_pct != null ? gw.packet_loss_pct + '%' : '—')}
        ${mc('Uptime',       uptimePct + '%')}
      </div>
      <div class="bar-list">
        ${gw.cpu_pct != null ? bar('CPU', gw.cpu_pct, '%') : ''}
        ${gw.ram_pct != null ? bar('RAM', gw.ram_pct, '%') : ''}
        ${gw.temp_c  != null ? bar('Temp', gw.temp_c, '°C') : ''}
      </div>
      <div class="check-row">
        ${chk('WireGuard', wgOk)}
        ${chk('SOCKS5',    !!gw.socks5_ok)}
        ${chk('Internet',  !!gw.internet_ok)}
      </div>
    `;
  }

  function offlineBody(gw) {
    const age = gw.heartbeat_age_s != null ? gw.heartbeat_age_s + 's' : '∞ (sin latido)';
    return `
      <div class="offline-box">
        <div class="offline-icon">📡</div>
        <div style="color:var(--er);font-weight:600">Sin latido &gt; 90 s → OFFLINE</div>
        <div class="mono txt-t" style="font-size:11px">last_heartbeat: ${gw.last_heartbeat || 'null'} · edad: ${age}</div>
      </div>
    `;
  }

  // ─── Timeline helper: 24 celdas up/down/none desde el endpoint de uptime ─────
  function uptime24(gwId) {
    const buckets = _uptime && _uptime.timelines && _uptime.timelines[gwId];
    if (!buckets || !buckets.length) return Array(24).fill('none');
    // Tomamos las últimas 24 muestras horarias; online=true→up, false→down.
    const last = buckets.slice(-24);
    const out = last.map(b => (b.online === true || (b.online == null && b.uptime_pct >= 50)) ? 'up' : 'down');
    while (out.length < 24) out.unshift('none');
    return out;
  }

  // ─── Sub-componentes ───────────────────────────────────────────────────────
  function stateColor(gw) {
    if (gw.maintenance) return 'var(--wn)';
    return gw.online ? 'var(--ok)' : 'var(--er)';
  }

  function mc(label, value) {
    return `<div class="metric-cell">
      <div class="metric-lbl">${label}</div>
      <div class="metric-val mono">${value}</div>
    </div>`;
  }

  function bar(label, value, unit) {
    const max  = unit === '°C' ? 80 : 100;
    const pct  = Math.min(100, (value / max) * 100);
    const color = pct > 80 ? 'var(--er)' : pct > 60 ? 'var(--wn)' : 'var(--ok)';
    return `<div class="bar-row">
      <span class="bar-lbl">${label}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${pct.toFixed(1)}%;background:${color}"></div></div>
      <span class="bar-val mono">${value}${unit}</span>
    </div>`;
  }

  function chk(label, ok) {
    const c = ok ? 'var(--ok)' : 'var(--er)';
    return `<span class="chk-pill" style="color:${c};border-color:${c}40;background:${c}12">
      ${ok ? '✓' : '✗'} ${label}
    </span>`;
  }

  function pad(n) { return String(n).padStart(2, '0'); }
}());
