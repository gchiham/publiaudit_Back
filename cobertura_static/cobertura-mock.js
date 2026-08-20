'use strict';
// ─── Datos / cliente API ─────────────────────────────────────────────────────
//
//  ┌─────────────────────────────────────────────────────────────────┐
//  │  DEMO_MODE = true  → datos de ejemplo (sin backend)             │
//  │  DEMO_MODE = false → llama al API real con ?token=… (producción)│
//  └─────────────────────────────────────────────────────────────────┘
//
//  Los renderers consumen la MISMA forma de JSON que expone el backend
//  FastAPI de media-app (mediaAPP) en /api/cobertura/*. Los generadores
//  mock de abajo emiten exactamente esa forma para que el modo demo se
//  vea igual que producción.
const DEMO_MODE = false;   // ← producción: lee de /api/cobertura/* con ?token=…

// ─── Token ───────────────────────────────────────────────────────────────────
function getToken() {
  return new URLSearchParams(window.location.search).get('token') || '';
}

// ─── Cliente API ─────────────────────────────────────────────────────────────
async function apiFetch(endpoint, params) {
  return DEMO_MODE
    ? _mockFetch(endpoint, params || {})
    : _realFetch(endpoint, params || {});
}

async function _realFetch(endpoint, params) {
  const url = new URL(endpoint, window.location.origin);
  const tok = getToken();
  if (tok) url.searchParams.set('token', tok);
  Object.keys(params).forEach(function (k) {
    url.searchParams.set(k, String(params[k]));
  });

  var res;
  try {
    res = await fetch(url.toString(), { headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new Error('Sin conexión al backend — ' + e.message);
  }

  if (res.status === 401 || res.status === 403) {
    document.dispatchEvent(
      new CustomEvent('cob:denied', { detail: 'Token inválido o expirado' })
    );
    throw new Error('Token inválido');
  }
  if (!res.ok) throw new Error('HTTP ' + res.status + ' · ' + endpoint);
  return res.json();
}

async function _mockFetch(endpoint, params) {
  await new Promise(function (r) { setTimeout(r, 120 + Math.random() * 200); });
  var ep = endpoint.split('?')[0];
  if (ep.endsWith('/coverage'))           return mockCoverage(params.days || 9);
  if (ep.endsWith('/gateways/uptime'))    return mockUptime(params.period || '24h');
  if (ep.endsWith('/gateways'))           return JSON.parse(JSON.stringify(MOCK_GATEWAYS));
  if (ep.endsWith('/destroyer/runs'))     return JSON.parse(JSON.stringify(MOCK_RUNS));
  if (ep.endsWith('/destroyer/active'))   return JSON.parse(JSON.stringify(MOCK_ACTIVE));
  if (ep.endsWith('/destroyer/stats'))    return JSON.parse(JSON.stringify(MOCK_STATS));
  if (ep.endsWith('/costs/summary'))      return JSON.parse(JSON.stringify(MOCK_COSTS_SUMMARY));
  if (ep.endsWith('/costs/daily'))        return JSON.parse(JSON.stringify(MOCK_COSTS_DAILY));
  throw new Error('Endpoint no reconocido: ' + endpoint);
}

// ─── Util compartido ─────────────────────────────────────────────────────────
function seededRand(seed) {
  var x = Math.sin(seed + 7) * 10000;
  return x - Math.floor(x);
}
function _nowHnIso() {
  // "ahora" desplazado a -06:00 para imitar el backend (display GMT-6).
  var d = new Date(Date.now() - 6 * 3600000);
  return d.toISOString().replace('Z', '-06:00');
}

// ─── Mock: coverage (forma backend: {streams, cells, summary, updated}) ───────
var MOCK_STREAMS = [
  { id: 'hch_tv',         name: 'HCH TV',          type: 'tv' },
  { id: 'teleceiba',      name: 'TeleCeiba',       type: 'tv' },
  { id: 'canal_11',       name: 'Canal 11',        type: 'radio' },
  { id: 'radio_america',  name: 'Radio América',   type: 'radio' },
  { id: 'radio_choluteca',name: 'Radio Choluteca', type: 'radio' },
  { id: 'radio_satelite', name: 'Radio Satélite',  type: 'radio' },
  { id: 'suave_fm',       name: 'Suave FM',        type: 'radio' },
  { id: 'radio_globo',    name: 'Radio Globo',     type: 'radio' },
  { id: 'radio_el_patio', name: 'Radio El Patio',  type: 'radio' },
  { id: 'fm_941',         name: 'FM 94.1',         type: 'radio' },
  { id: 'xy_hrn',         name: 'XY HRN',          type: 'radio' },
  { id: 'xy_sps',         name: 'XY SPS',          type: 'radio' },
];
function mockCoverage(days) {
  var base = new Date(Date.now() - 6 * 3600000);
  var cells = [], audio = 0, video = 0;
  for (var d = days - 1; d >= 0; d--) {
    var dt = new Date(base); dt.setUTCDate(dt.getUTCDate() - d);
    var day = dt.toISOString().split('T')[0];
    for (var h = 0; h < 24; h++) {
      for (var si = 0; si < MOCK_STREAMS.length; si++) {
        var s = MOCK_STREAMS[si];
        if (s.id === 'teleceiba') continue;            // disabled → sin celdas
        var r = seededRand((s.id.charCodeAt(0) + s.id.charCodeAt(2)) * 1000 + d * 100 + h);
        var thr = s.type === 'tv' ? 0.04 : 0.06;
        if (r < thr) continue;
        var kind = s.type === 'tv' ? 'av' : 'audio';
        if (kind === 'av') video++;
        audio++;
        cells.push({ stream_id: s.id, day: day, hour: h, kind: kind,
                     segs: 1, detections: (r < 0.10 ? Math.ceil(seededRand(r * 99) * 3) : 0) });
      }
    }
  }
  var tv = MOCK_STREAMS.filter(function (s) { return s.type === 'tv'; }).length;
  return {
    streams: MOCK_STREAMS,
    cells: cells,
    summary: { streams_active: MOCK_STREAMS.length, tv: tv, radio: MOCK_STREAMS.length - tv,
               audio_hours: audio, video_hours: video, period_days: days },
    updated: _nowHnIso(),
  };
}

// ─── Mock: gateways (forma backend, campos planos de salud) ───────────────────
var MOCK_GATEWAYS = {
  gateways: [
    { gateway_id:'hn02', name:'PC-LCE Gateway', city:'Honduras', device_type:'pc',
      wg_ip:'10.101.0.5', priority:1, max_streams:15, status:'healthy', score:92, maintenance:false,
      last_heartbeat:_nowHnIso(), agent_version:'1.0.0', cpu_pct:3.1, ram_pct:37.8, temp_c:null,
      uptime_s:864000, internet_ok:true, socks5_ok:true, external_ip:'181.115.99.87', latency_ms:224,
      packet_loss_pct:0.0, wg_handshake_age_s:4, health_at:_nowHnIso(), active_streams:0,
      heartbeat_age_s:5, online:true, display_status:'ONLINE' },
    { gateway_id:'hn01', name:'Honduras Raspberry 01', city:'Honduras', device_type:'raspberry_pi',
      wg_ip:'10.101.0.2', priority:2, max_streams:15, status:'healthy', score:72, maintenance:false,
      last_heartbeat:null, agent_version:null, cpu_pct:null, ram_pct:null, temp_c:null, uptime_s:null,
      internet_ok:null, socks5_ok:null, external_ip:null, latency_ms:null, packet_loss_pct:null,
      wg_handshake_age_s:null, health_at:null, active_streams:0, heartbeat_age_s:null,
      online:false, display_status:'OFFLINE' },
    { gateway_id:'hn03', name:'RPi-Levi', city:'Honduras', device_type:'raspberry_pi',
      wg_ip:'10.101.0.6', priority:3, max_streams:15, status:'failed', score:0, maintenance:false,
      last_heartbeat:null, agent_version:null, cpu_pct:null, ram_pct:null, temp_c:null, uptime_s:null,
      internet_ok:null, socks5_ok:null, external_ip:null, latency_ms:null, packet_loss_pct:null,
      wg_handshake_age_s:null, health_at:null, active_streams:0, heartbeat_age_s:null,
      online:false, display_status:'OFFLINE' },
  ],
  count: 3, updated: _nowHnIso(),
};
function mockUptime(period) {
  var hours = period === '24h' ? 24 : (period === '7d' ? 7 : 30);
  var timelines = {}, pct = {};
  MOCK_GATEWAYS.gateways.forEach(function (g) {
    var arr = [];
    for (var i = 0; i < hours; i++) {
      var up = g.online && seededRand((g.gateway_id.charCodeAt(2) || 1) * 99 + i) > 0.015;
      arr.push({ bucket: null, uptime_pct: up ? 100 : 0, online: g.online ? up : false, samples: g.online ? 60 : 0 });
    }
    timelines[g.gateway_id] = arr;
    pct[g.gateway_id] = g.online ? +(arr.filter(function (b) { return b.uptime_pct > 50; }).length / hours * 100).toFixed(1) : 0;
  });
  return { period: period, timelines: timelines, uptime_pct: pct, updated: _nowHnIso() };
}

// ─── Mock: destroyer (runs / active / stats — forma backend) ──────────────────
function _run(id, name, status, rel, boot, work, total, tf, fd, ferr, det, cost, anomaly) {
  return { id:id, droplet_name:name, status:status, release_version:'destroyer-'+rel,
    boot_seconds:boot, work_seconds:work, total_seconds:total, total_files:tf, files_done:fd,
    files_error:ferr, total_detections:det, cost_usd:cost,
    t1_deployed:_nowHnIso(), t2_started:_nowHnIso(), t3_completed:_nowHnIso(),
    t4_destroyed:_nowHnIso(), last_activity:_nowHnIso(),
    throughput_per_min: (work && fd) ? +(fd / (work / 60)).toFixed(1) : 0, anomaly:anomaly };
}
var MOCK_RUNS = { runs: [
  _run(50,'Destroyer0832','destroyed','v22',36,165,203,6,6,0,1,0.0126,false),
  _run(49,'Destroyer2115','destroyed','v22',26,324,352,100,100,0,7,0.0219,false),
  _run(48,'Destroyer1450','destroyed','v22',26,51,79,8,8,0,2,0.0049,false),
  _run(47,'Destroyer1930','killed','v21',null,null,null,287,187,0,21,null,true),
  _run(46,'Destroyer1145','destroyed','v21',45,86,132,18,18,0,20,0.0082,false),
  _run(45,'Destroyer2310','destroyed','v21',30,210,242,55,55,0,8,0.0151,false),
], count:6, updated:_nowHnIso() };
var MOCK_ACTIVE = { active: _run(51,'Destroyer-run-51','running','v22',31,202,null,100,42,0,3,0.0074,false), updated:_nowHnIso() };
var MOCK_STATS = {
  cards: { runs_month:50, runs_by_status:{destroyed:45,killed:3,timeout:2}, detections_month:147,
           cost_month_usd:0.89, avg_throughput:26.4, success_rate:96, last_run:MOCK_RUNS.runs[0] },
  detections_by_day: [
    {day:'2026-06-09',detections:6},{day:'2026-06-10',detections:12},{day:'2026-06-11',detections:8},
    {day:'2026-06-12',detections:41},{day:'2026-06-13',detections:7},{day:'2026-06-14',detections:1},
  ],
  throughput_by_release: { 'destroyer-v21': 14.8, 'destroyer-v22': 26.4 },
  time_distribution: { '<30s':2, '30-60s':3, '60-90s':4, '>90s':5 },
  period_days:30, hourly_rate:0.30, updated:_nowHnIso(),
};

// ─── Mock: costos AWS (forma backend) ─────────────────────────────────────────
var _mockDaily = (function () {
  var rows = [];
  for (var i = 0; i < 14; i++) {
    var dt = new Date('2026-06-01'); dt.setUTCDate(dt.getUTCDate() + i);
    var ec2 = +(0.04 + seededRand(i * 7) * 0.05).toFixed(4);
    var s3  = +(0.19 + seededRand(i * 11) * 0.04).toFixed(4);
    var ebs = 0.10, tr = +(0.01 + seededRand(i * 5) * 0.015).toFixed(4);
    rows.push({ date: dt.toISOString().split('T')[0],
      services: { EC2:ec2, S3:s3, Snapshots:ebs, DataTransfer:tr },
      total: +(ec2 + s3 + ebs + tr).toFixed(4) });
  }
  return rows;
}());
var _mockBySvc = { EC2:0.89, S3:3.20, Snapshots:1.40, DataTransfer:0.34 };
var MOCK_COSTS_SUMMARY = {
  summary: { today:0.24, month:5.83, projection:7.40, prev_month:6.97, vs_prev_pct:6.2,
             days_elapsed:14, days_in_month:30 },
  by_service: _mockBySvc, available:true, note:'Datos de AWS con hasta 24h de retraso',
  updated: new Date(Date.now() - 3 * 3600000 - 22 * 60000).toISOString().replace('Z','-06:00'),
};
var MOCK_COSTS_DAILY = {
  daily: _mockDaily, by_service: _mockBySvc, available:true,
  note:'Datos de AWS con hasta 24h de retraso',
  updated: MOCK_COSTS_SUMMARY.updated,
};
