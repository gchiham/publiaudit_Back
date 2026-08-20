'use strict';
// ─── Tab 1: Streams — Heatmap de cobertura (datos reales de s3_scan_log) ──────
//  Consume la forma del backend: { streams:[{id,name,type}],
//  cells:[{stream_id, day(YYYY-MM-DD HN), hour(0-23 HN), kind('av'|'audio'),
//  segs, detections}], summary:{...} }. Las horas YA vienen en GMT-6.
(function () {
  let _period          = 7;
  let _data            = null;
  let _fetchedPeriod   = null;  // para detectar cambio de período

  window.renderStreams = async function (force) {
    const el           = document.getElementById('tab-streams');
    const periodChange = _fetchedPeriod !== null && _fetchedPeriod !== _period;
    const firstLoad    = !_data || periodChange;
    if (firstLoad) el.innerHTML = '<div class="loading-ph">Cargando cobertura…</div>';
    if (firstLoad || force) {
      _data          = await apiFetch('/api/cobertura/coverage', { days: _period });
      _fetchedPeriod = _period;
    }
    _render();
  };

  function _render() {
    const el      = document.getElementById('tab-streams');
    const streams = _data.streams || [];
    const tv      = streams.filter(s => s.type === 'tv');
    const radio   = streams.filter(s => s.type !== 'tv');
    const map     = buildMap(_data.cells || []);
    const dates   = getDates(_period);
    const sum     = _data.summary || {};
    const labels  = {}; streams.forEach(s => { labels[s.id] = s.name || s.id; });

    el.innerHTML = `
      <div class="section-content">
        <div class="kpi-row">
          ${kpi('Streams activos', String(sum.streams_active ?? streams.length),
                (sum.tv ?? tv.length) + ' TV · ' + (sum.radio ?? radio.length) + ' Radio', 'accent')}
          ${kpi('Audio grabado',   fmtH(sum.audio_hours || 0), 'últimos ' + _period + 'd')}
          ${kpi('Video grabado',   fmtH(sum.video_hours || 0), 'últimos ' + _period + 'd')}
          ${kpi('Período',         _period + 'd', fmtRange(dates))}
        </div>

        <div class="toolbar">
          <span class="section-title">Cobertura horaria — GMT-6</span>
          <div class="period-btns" id="period-btns">
            ${['7d','14d','30d'].map(p =>
              `<button class="period-btn ${p === _period+'d' ? 'active' : ''}"
                       data-p="${parseInt(p)}">${p}</button>`).join('')}
          </div>
          <div class="spacer"></div>
          <div class="legend">
            ${[['av','audio+video'],['audio','audio'],['video','video'],['none','sin datos']].map(
              ([k,l]) => `<span class="legend-item"><span class="ls ${k}"></span>${l}</span>`
            ).join('')}
          </div>
        </div>

        <div class="heatmap-wrap">
          <div class="hm-scroll">
            <div class="hm-table">
              <div class="hm-header">
                <div class="hm-lbl-col"></div>
                <div class="hm-days">
                  ${dates.map((d, i) =>
                    `<div class="hm-day-h" style="flex:24">${fmtDayLbl(d, i, dates.length)}</div>`
                  ).join('')}
                </div>
              </div>

              ${tv.length ? '<div class="hm-section-lbl">▶ TV</div>' : ''}
              ${tv.map(s => row(s, map, dates, labels)).join('')}

              ${radio.length ? '<div class="hm-section-lbl">📻 Radio</div>' : ''}
              ${radio.map(s => row(s, map, dates, labels)).join('')}

              ${streams.length ? '' : '<div class="empty-state">Sin streams con cobertura en el período.</div>'}
            </div>
          </div>
        </div>
      </div>
    `;

    document.querySelectorAll('.period-btn').forEach(btn => {
      btn.addEventListener('click', function () {
        _period = parseInt(this.dataset.p);
        _data   = null;
        renderStreams(true);
      });
    });

    setupTooltip();
  }

  // ─── Helpers ───────────────────────────────────────────────────────────────
  function row(stream, map, dates, labels) {
    let cells = '';
    for (const date of dates) {
      for (let h = 0; h < 24; h++) {
        const type = (map[stream.id] && map[stream.id][date] && map[stream.id][date][h]) || 'none';
        const tip  = `${stream.id} · ${fmtTipDate(date)} · ${pad(h)}:00 GMT-6 · ${typeStr(type)}`;
        cells += `<div class="hm-cell ${type}" data-tip="${tip}"></div>`;
      }
    }
    return `<div class="hm-row">
      <div class="hm-lbl" title="${labels[stream.id] || stream.id}">${stream.id}</div>
      <div class="hm-cells">${cells}</div>
    </div>`;
  }

  function buildMap(cells) {
    const map = {};
    for (const c of cells) {
      if (!map[c.stream_id]) map[c.stream_id] = {};
      if (!map[c.stream_id][c.day]) map[c.stream_id][c.day] = {};
      map[c.stream_id][c.day][c.hour] = c.kind;   // ya en GMT-6
    }
    return map;
  }

  function getDates(days) {
    const out = [];
    const base = new Date(Date.now() - 6 * 3600000); // aprox "hoy" en GMT-6
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(base);
      d.setUTCDate(d.getUTCDate() - i);
      out.push(d.toISOString().split('T')[0]);
    }
    return out;
  }

  function fmtDayLbl(dt, idx, total) {
    const d    = new Date(dt + 'T12:00:00Z');
    const days = ['Dom','Lun','Mar','Mié','Jue','Vie','Sáb'];
    const step = total <= 14 ? 1 : 3;
    return idx % step === 0 ? `${days[d.getUTCDay()]} ${d.getUTCDate()}` : '';
  }

  function fmtTipDate(dt) {
    const d  = new Date(dt + 'T12:00:00Z');
    const ds = ['Dom','Lun','Mar','Mié','Jue','Vie','Sáb'];
    const ms = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
    return `${ds[d.getUTCDay()]} ${d.getUTCDate()} ${ms[d.getUTCMonth()]}`;
  }

  function fmtRange(dates) {
    if (!dates.length) return '';
    const f = dt => { const d = new Date(dt + 'T12:00:00Z'); return d.getUTCDate() + ' ' + ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'][d.getUTCMonth()]; };
    return f(dates[0]) + ' — ' + f(dates[dates.length - 1]);
  }

  function fmtH(n)    { return n >= 1000 ? (n / 1000).toFixed(1) + 'k h' : n + 'h'; }
  function pad(n)     { return String(n).padStart(2, '0'); }
  function typeStr(t) { return { av:'Audio + Video', audio:'Audio', video:'Video', none:'Sin datos' }[t] || t; }

  function kpi(label, value, sub, accent) {
    return `<div class="kpi-card${accent ? ' kpi-' + accent : ''}">
      <span class="kpi-label">${label}</span>
      <span class="kpi-value">${value}</span>
      ${sub ? `<span class="kpi-sub">${sub}</span>` : ''}
    </div>`;
  }

  function setupTooltip() {
    const tip = document.getElementById('tooltip');
    document.querySelectorAll('.hm-cell[data-tip]').forEach(cell => {
      cell.addEventListener('mouseenter', function (e) {
        tip.textContent = this.dataset.tip;
        tip.classList.remove('hidden');
        moveTip(e);
      });
      cell.addEventListener('mousemove', moveTip);
      cell.addEventListener('mouseleave', () => tip.classList.add('hidden'));
    });
  }

  function moveTip(e) {
    const tip = document.getElementById('tooltip');
    const m = 12;
    let x = e.clientX + m, y = e.clientY + m;
    if (x + tip.offsetWidth  + m > window.innerWidth)  x = e.clientX - tip.offsetWidth  - m;
    if (y + tip.offsetHeight + m > window.innerHeight) y = e.clientY - tip.offsetHeight - m;
    tip.style.left = x + 'px';
    tip.style.top  = y + 'px';
  }
}());
