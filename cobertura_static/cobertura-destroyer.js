'use strict';
// ─── Tab 3: Destroyer — historial EC2 Spot (datos reales de destroyer_runs) ───
//  Backend: tres endpoints → /runs {runs:[_run]}, /active {active:_run|null},
//  /stats {cards, detections_by_day, throughput_by_release, time_distribution}.
//  _run = {id, droplet_name, status, release_version, boot_seconds, work_seconds,
//  total_seconds, total_files, files_done, total_detections, cost_usd,
//  throughput_per_min, anomaly, t1_deployed.._hn(ISO -06:00), last_activity}.
(function () {
  let _data = null;
  const _charts = {};

  function killChart(id) {
    if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
  }

  window.renderDestroyer = async function (force) {
    const el = document.getElementById('tab-destroyer');
    if (!_data) el.innerHTML = '<div class="loading-ph">Cargando Destroyer…</div>';
    if (!_data || force) {
      const [runs, active, stats] = await Promise.all([
        apiFetch('/api/cobertura/destroyer/runs',   { limit: 50 }),
        apiFetch('/api/cobertura/destroyer/active'),
        apiFetch('/api/cobertura/destroyer/stats',  { days: 30 }),
      ]);
      _data = { runs: runs.runs || [], active: active.active || null, stats: stats };
    }
    ['ch-det','ch-thr'].forEach(killChart);
    _render();
  };

  function _render() {
    const el   = document.getElementById('tab-destroyer');
    const { active, stats, runs } = _data;
    const cards = (stats && stats.cards) || {};
    const dark  = document.documentElement.getAttribute('data-theme') !== 'light';
    const gc    = dark ? '#2A323A' : '#E2E8F0';
    const tc    = dark ? '#A8B2BD' : '#4A5568';

    el.innerHTML = `
      <div class="section-content">
        ${active ? activePanel(active) : ''}

        <div class="chart-row">
          <div class="chart-card">
            <div class="chart-title">Detecciones por día</div>
            <canvas id="ch-det" height="130"></canvas>
          </div>
          <div class="chart-card">
            <div class="chart-title">Throughput por release (arch/min)</div>
            <canvas id="ch-thr" height="130"></canvas>
          </div>
        </div>

        <div class="kpi-row">
          ${kpi('Corridas este mes',  cards.runs_month ?? '—',                       null,  null)}
          ${kpi('Detecciones',        cards.detections_month ?? '—',                 null,  'ok')}
          ${kpi('Costo mes',          '$' + num(cards.cost_month_usd, 2),            null,  'accent')}
          ${kpi('Throughput avg',     num(cards.avg_throughput, 1) + '/min',        null,  null)}
          ${kpi('Tasa de éxito',      num(cards.success_rate, 1) + '%',             null,  'ok')}
          ${kpi('Última corrida',     cards.last_run ? fmtAgo(runActivity(cards.last_run)) : '—', null, null)}
        </div>

        <div class="runs-wrap">
          <table class="runs-tbl">
            <thead><tr>
              ${['#','Fecha HN','Boot','Trabajo','Total','Archivos','Dets','Arch/min','Costo','Release','Status']
                .map(h => `<th>${h}</th>`).join('')}
            </tr></thead>
            <tbody>
              ${runs.length ? runs.map(runRow).join('')
                            : '<tr><td colspan="11"><div class="empty-state">Sin corridas registradas.</div></td></tr>'}
            </tbody>
          </table>
        </div>
      </div>
    `;

    // Chart: detecciones por día
    const det = stats && stats.detections_by_day ? stats.detections_by_day : [];
    _charts['ch-det'] = new Chart(document.getElementById('ch-det'), {
      type: 'bar',
      data: {
        labels: det.map(d => d.day.slice(5)),
        datasets: [{ data: det.map(d => d.detections), backgroundColor: 'rgba(28,192,249,.55)', borderRadius: 3, borderSkipped: false }],
      },
      options: baseOpts(gc, tc, false),
    });

    // Chart: throughput por release
    const thrRel = (stats && stats.throughput_by_release) || {};
    const relLabels = Object.keys(thrRel).sort();
    const palette = ['rgba(91,217,138,.6)','rgba(251,197,106,.6)','rgba(28,192,249,.6)','rgba(255,133,137,.6)'];
    _charts['ch-thr'] = new Chart(document.getElementById('ch-thr'), {
      type: 'bar',
      data: {
        labels: relLabels.map(stripRel),
        datasets: [{ data: relLabels.map(k => thrRel[k]),
                     backgroundColor: relLabels.map((_, i) => palette[i % palette.length]),
                     borderRadius: 3, borderSkipped: false }],
      },
      options: baseOpts(gc, tc, false),
    });
  }

  // ─── Panel de corrida activa (null-safe) ─────────────────────────────────────
  function activePanel(r) {
    const tf   = r.total_files || 0;
    const pct  = tf > 0 ? Math.round((r.files_done / tf) * 100) : 0;
    const thr  = r.throughput_per_min ? r.throughput_per_min.toFixed(1) : '—';
    const t1   = r.t1_deployed || r.t1_deployed_hn;
    const elapsed = t1 ? Math.max(0, Math.round((Date.now() - new Date(t1).getTime()) / 1000)) : 0;
    return `
      <div class="active-run">
        <div class="active-run-left">
          <div class="ar-eyebrow">Corrida activa</div>
          <div class="ar-name">
            ${r.droplet_name}
            <span class="rel-tag">${r.release_version || ''}</span>
            <span class="rel-tag" style="color:var(--accent)">${r.status}</span>
          </div>
          <div class="ar-progress">
            <div class="progress-meta">
              <span class="mono">${r.files_done}/${tf} archivos</span>
              <span class="mono" style="color:var(--accent)">${pct}%</span>
            </div>
            <div class="progress-track">
              <div class="progress-fill" style="width:${pct}%"></div>
            </div>
          </div>
        </div>
        <div class="active-run-stats">
          ${sc('Throughput', thr + '/min')}
          ${sc('Acumulado',  '$' + num(r.cost_usd, 4))}
          ${sc('Tiempo',     fmtSec(elapsed))}
          ${sc('Dets',       r.total_detections ?? 0)}
        </div>
      </div>
    `;
  }

  // ─── Fila de la tabla ────────────────────────────────────────────────────────
  function runRow(r) {
    const colors = {
      destroyed: ['var(--success-fg)', 'var(--success-bg)'],
      completed: ['var(--success-fg)', 'var(--success-bg)'],
      killed:    ['var(--warning-fg)', 'var(--warning-bg)'],
      timeout:   ['var(--warning-fg)', 'var(--warning-bg)'],
      error:     ['var(--danger-fg)',  'var(--danger-bg)'],
      failed:    ['var(--danger-fg)',  'var(--danger-bg)'],
      running:   ['var(--accent)',     'var(--accent-soft)'],
      deploying: ['var(--accent)',     'var(--accent-soft)'],
    };
    const [scl, sbg] = colors[r.status] || ['var(--text-tertiary)', 'transparent'];
    return `
      <tr class="${r.anomaly ? 'anomaly' : ''}">
        <td class="mono">#${r.id}</td>
        <td class="mono sm">${hnLabel(r.t2_started || r.t1_deployed || r.t2_started_hn || r.t1_deployed_hn)}</td>
        <td class="mono">${r.boot_seconds  != null ? r.boot_seconds  + 's' : '—'}</td>
        <td class="mono">${r.work_seconds  != null ? r.work_seconds  + 's' : '—'}</td>
        <td class="mono">${r.total_seconds != null ? r.total_seconds + 's' : '—'}</td>
        <td class="mono">${r.files_done ?? 0}/${r.total_files ?? 0}</td>
        <td class="mono">${r.total_detections ?? 0}</td>
        <td class="mono">${r.throughput_per_min ? r.throughput_per_min : '—'}</td>
        <td class="mono">${r.cost_usd != null ? '$' + r.cost_usd.toFixed(4) : '—'}</td>
        <td class="mono">${stripRel(r.release_version)}</td>
        <td><span class="spill" style="color:${scl};background:${sbg};border-color:${scl}40">${r.status}</span></td>
      </tr>
    `;
  }

  // ─── Opciones Chart.js ───────────────────────────────────────────────────────
  function baseOpts(gc, tc, showLegend) {
    const font = { family: "'IBM Plex Mono', monospace", size: 10 };
    return {
      responsive: true,
      plugins: {
        legend: { display: showLegend, labels: { color: tc, boxWidth: 12, font } },
        tooltip: { bodyFont: font, titleFont: font },
      },
      scales: {
        x: { grid: { color: gc }, ticks: { color: tc, font } },
        y: { grid: { color: gc }, ticks: { color: tc, font }, beginAtZero: true },
      },
    };
  }

  // ─── Micro-helpers ───────────────────────────────────────────────────────────
  function kpi(label, value, sub, accent) {
    const c = { ok:'var(--success-fg)', accent:'var(--accent)' }[accent] || 'var(--text-strong)';
    return `<div class="kpi-card">
      <span class="kpi-label">${label}</span>
      <span class="kpi-value" style="color:${c}">${value}</span>
      ${sub ? `<span class="kpi-sub">${sub}</span>` : ''}
    </div>`;
  }

  function sc(label, value) {
    return `<div class="stat-cell">
      <div class="stat-lbl">${label}</div>
      <div class="stat-val mono">${value}</div>
    </div>`;
  }

  function num(v, dec)   { return (v == null || isNaN(v)) ? (dec ? (0).toFixed(dec) : '0') : Number(v).toFixed(dec); }
  function stripRel(v)   { return (v || '—').replace(/^destroyer-/, ''); }
  function runActivity(r){ return r.last_activity || r.t4_destroyed || r.t3_completed || r.t2_started || r.t1_deployed; }

  // ISO con offset -06:00 → "14 Jun · 20:15" usando las componentes locales (HN).
  function hnLabel(iso) {
    if (!iso) return '—';
    const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!m) return iso;
    const ms = ['Ene','Feb','Mar','Abr','May','Jun','Jul','Ago','Sep','Oct','Nov','Dic'];
    return `${parseInt(m[3])} ${ms[parseInt(m[2]) - 1]} · ${m[4]}:${m[5]}`;
  }

  function fmtSec(s) {
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
    return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm';
  }

  function fmtAgo(iso) {
    if (!iso) return '—';
    return 'hace ' + fmtSec(Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000)));
  }
}());
