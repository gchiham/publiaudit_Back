'use strict';
// ─── Tab 4: Costos AWS (Cost Explorer, caché 1h en backend) ───────────────────
//  Backend: /costs/summary {summary:{today,month,projection,prev_month,vs_prev_pct,
//  days_elapsed,days_in_month}, by_service:{SVC:monto}, available, note, updated}
//          /costs/daily   {daily:[{date,services:{SVC:monto},total}], by_service,...}
//  Si available=false (sin permiso a Cost Explorer) → estado degradado elegante.
(function () {
  let _data = null;
  const _charts = {};

  const SVC_LABEL = {
    EC2: 'EC2 (Destroyer)', S3: 'S3 (grabaciones)', Snapshots: 'EBS / Snapshots',
    DataTransfer: 'Data Transfer', RDS: 'RDS', Lambda: 'Lambda', Otros: 'Otros',
  };

  function killChart(id) {
    if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
  }

  window.renderCosts = async function (force) {
    const el = document.getElementById('tab-costs');
    if (!_data) el.innerHTML = '<div class="loading-ph">Cargando costos AWS…</div>';
    if (!_data || force) {
      const [summary, daily] = await Promise.all([
        apiFetch('/api/cobertura/costs/summary'),
        apiFetch('/api/cobertura/costs/daily').catch(() => ({ available: false, daily: [] })),
      ]);
      _data = {
        available: summary.available !== false,
        summary: summary.summary || {},
        by_service: summary.by_service || {},
        daily: daily.daily || [],
        updated: summary.updated,
        error: summary.error,
      };
    }
    ['ch-accum','ch-daily'].forEach(killChart);
    _render();
  };

  function _render() {
    const el = document.getElementById('tab-costs');

    if (!_data.available) {
      el.innerHTML = `
        <div class="section-content">
          <div class="warn-banner">
            <span>⚠ Datos de AWS con hasta 24 h de retraso</span>
          </div>
          <div class="heatmap-wrap">
            <div class="empty-state">
              Cost Explorer no disponible en este momento${_data.error ? ' — ' + _data.error : ''}.
              <br>Verifica permisos <code class="mono">ce:GetCostAndUsage</code> en mediaAPP.
            </div>
          </div>
        </div>`;
      return;
    }

    const s     = _data.summary;
    const bySvc = _data.by_service;
    const daily = _data.daily;
    const dark  = document.documentElement.getAttribute('data-theme') !== 'light';
    const gc    = dark ? '#2A323A' : '#E2E8F0';
    const tc    = dark ? '#A8B2BD' : '#4A5568';
    const age   = cacheAge(_data.updated);

    // "hoy" por servicio = última fila diaria
    const todayRow = daily.length ? daily[daily.length - 1].services || {} : {};
    const svcKeys  = Object.keys(bySvc).sort((a, b) => (bySvc[b] || 0) - (bySvc[a] || 0));

    el.innerHTML = `
      <div class="section-content">
        <div class="warn-banner">
          <span>⚠ Datos de AWS con hasta 24 h de retraso</span>
          <span class="mono" style="font-size:11px">Actualizado hace ${age}</span>
        </div>

        <div class="kpi-row">
          ${kpi('Hoy (parcial)',         '$' + num(s.today, 2),      'día en curso',                            null)}
          ${kpi('Este mes',              '$' + num(s.month, 2),      monthLabel(s),                             'accent')}
          ${kpi('Proyección fin de mes', '$' + num(s.projection, 2), 'basado en acumulado',                     null)}
          ${kpi('vs Mes anterior',       vsPrev(s),                  'May: $' + num(s.prev_month, 2),           null)}
        </div>

        <div class="costs-main">
          <div class="chart-card flex2">
            <div class="chart-title">Costo acumulado — mes en curso</div>
            <canvas id="ch-accum" height="150"></canvas>
          </div>
          <div class="svc-cards">
            ${svcKeys.length
              ? svcKeys.map(k => svcCard(k, bySvc[k], todayRow[k])).join('')
              : '<div class="empty-state small">Sin desglose por servicio.</div>'}
          </div>
        </div>

        <div class="chart-card">
          <div class="chart-title">Desglose diario por servicio</div>
          <canvas id="ch-daily" height="110"></canvas>
        </div>
      </div>
    `;

    if (!daily.length) return; // sin series, ya pintamos cards/KPIs

    const font = { family: "'IBM Plex Mono', monospace", size: 10 };

    // ─── Área acumulada + proyección ──────────────────────────────────────────
    const daysInMonth = s.days_in_month || 30;
    let running = 0;
    const accum = daily.map(d => { running += (d.total || 0); return +running.toFixed(4); });
    const avgDay   = accum.length ? running / accum.length : 0;
    const daysLeft = Math.max(0, daysInMonth - daily.length);
    const proj = Array(daily.length).fill(null);
    if (accum.length) proj[accum.length - 1] = accum[accum.length - 1];
    for (let i = 1; i <= daysLeft; i++) proj.push(+(accum[accum.length - 1] + avgDay * i).toFixed(4));
    const accumNull = [...accum, ...Array(daysLeft).fill(null)];

    const lastDate = daily.length ? new Date(daily[daily.length - 1].date + 'T12:00:00Z') : new Date();
    const allLabels = [
      ...daily.map(d => d.date.slice(5)),
      ...Array.from({ length: daysLeft }, (_, i) => {
        const dt = new Date(lastDate); dt.setUTCDate(dt.getUTCDate() + i + 1);
        return dt.toISOString().split('T')[0].slice(5);
      }),
    ];

    _charts['ch-accum'] = new Chart(document.getElementById('ch-accum'), {
      type: 'line',
      data: {
        labels: allLabels,
        datasets: [
          { label: 'Acumulado', data: accumNull, fill: true,
            backgroundColor: 'rgba(28,192,249,.10)', borderColor: '#1CC0F9', borderWidth: 2,
            tension: 0.35, pointRadius: 0, spanGaps: false },
          { label: 'Proyección', data: proj, fill: false,
            borderColor: dark ? '#7C8794' : '#A0AEC0', borderWidth: 1.5, borderDash: [5, 4],
            tension: 0.35, pointRadius: 0, spanGaps: true },
        ],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: true, labels: { color: tc, boxWidth: 12, font } },
          tooltip: { bodyFont: font, titleFont: font, callbacks: { label: ctx => ' $' + ctx.parsed.y.toFixed(2) } },
        },
        scales: {
          x: { grid: { color: gc }, ticks: { color: tc, font, maxTicksLimit: 10 } },
          y: { grid: { color: gc }, ticks: { color: tc, font, callback: v => '$' + v.toFixed(2) } },
        },
      },
    });

    // ─── Barras apiladas por servicio ─────────────────────────────────────────
    const colors = { EC2:'rgba(28,192,249,.65)', S3:'rgba(91,217,138,.65)',
      Snapshots:'rgba(251,197,106,.65)', DataTransfer:'rgba(255,133,137,.65)',
      RDS:'rgba(160,160,200,.65)', Lambda:'rgba(200,140,255,.55)', Otros:'rgba(124,135,148,.5)' };
    const datasets = svcKeys.map((k, i) => ({
      label: k,
      data: daily.map(d => (d.services && d.services[k]) || 0),
      backgroundColor: colors[k] || ['rgba(28,192,249,.6)','rgba(91,217,138,.6)','rgba(251,197,106,.6)','rgba(255,133,137,.6)'][i % 4],
      borderRadius: 2,
    }));
    _charts['ch-daily'] = new Chart(document.getElementById('ch-daily'), {
      type: 'bar',
      data: { labels: daily.map(d => d.date.slice(5)), datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { display: true, labels: { color: tc, boxWidth: 12, font } },
          tooltip: { bodyFont: font, titleFont: font, callbacks: { label: ctx => ' ' + ctx.dataset.label + ': $' + ctx.parsed.y.toFixed(4) } },
        },
        scales: {
          x: { stacked: true, grid: { color: gc }, ticks: { color: tc, font } },
          y: { stacked: true, grid: { color: gc }, ticks: { color: tc, font, callback: v => '$' + v.toFixed(2) } },
        },
      },
    });
  }

  // ─── Componentes ─────────────────────────────────────────────────────────────
  function svcCard(key, month, today) {
    return `<div class="svc-card">
      <div>
        <div class="svc-name">${SVC_LABEL[key] || key}</div>
        <div class="txt-t" style="font-size:11px">acumulado del mes</div>
      </div>
      <div style="text-align:right">
        <div class="mono" style="font-size:16px;font-weight:700;color:var(--text-strong)">$${num(month, 2)}</div>
        <div class="mono txt-t" style="font-size:11px">$${num(today, 2)}/hoy</div>
      </div>
    </div>`;
  }

  function kpi(label, value, sub, accent) {
    const c = { accent: 'var(--accent)' }[accent] || 'var(--text-strong)';
    return `<div class="kpi-card">
      <span class="kpi-label">${label}</span>
      <span class="kpi-value" style="color:${c}">${value}</span>
      ${sub ? `<span class="kpi-sub">${sub}</span>` : ''}
    </div>`;
  }

  function vsPrev(s) {
    if (s.vs_prev_pct == null) return '—';
    const v = Number(s.vs_prev_pct);
    return (v >= 0 ? '+' : '') + v + '%';
  }

  function monthLabel(s) {
    return s.days_elapsed ? ('1–' + s.days_elapsed + ' del mes') : 'mes en curso';
  }

  function num(v, dec) { return (v == null || isNaN(v)) ? (0).toFixed(dec) : Number(v).toFixed(dec); }

  function cacheAge(iso) {
    if (!iso) return '—';
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }
}());
