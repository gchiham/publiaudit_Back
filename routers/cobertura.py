"""Panel operativo interno 'Cobertura' para admin developers (4 pestañas).
Datos operativos, públicos (mismo criterio que /api/mediadev/*). El HTML se
protege con token simple en la URL. Horas de display en GMT-6 (HN, sin DST)."""
import logging
import os
import secrets
import time as _time
from datetime import datetime, timedelta, timezone

import boto3
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles as _StaticFiles

from core.db import get_db
from core.s3 import AWS_KEY, AWS_SECRET
from core.security import _COBERTURA_TOKEN, verify_cobertura_token
from core.timehn import month_start_utc as _month_start_utc, now_hn as _now_hn, to_hn as _to_hn

log = logging.getLogger('publiaudit-api')

_DESTROYER_RATE    = float(os.environ.get('DESTROYER_HOURLY_RATE', '0.30'))
# routers/ está un nivel bajo la raíz del proyecto — cobertura.html vive en la raíz.
_COBERTURA_HTML    = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cobertura.html')
_SUCCESS_STATUSES  = ('destroyed', 'completed')

router = APIRouter(tags=['cobertura'])

# ── Pestaña 1: Streams — grilla de cobertura (de s3_scan_log) ─────────────────
@router.get('/api/cobertura/coverage')
def cobertura_coverage(days: int = Query(9, ge=1, le=30),
                       _auth: bool = Depends(verify_cobertura_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.stream AS stream_id,
                       (COALESCE(s.hour_start_utc, s.scanned_at) AT TIME ZONE 'America/Tegucigalpa')::date AS day,
                       EXTRACT(HOUR FROM COALESCE(s.hour_start_utc, s.scanned_at) AT TIME ZONE 'America/Tegucigalpa')::int AS hour,
                       COUNT(*)                       AS segs,
                       COALESCE(SUM(s.detections), 0) AS detections,
                       MAX(COALESCE(ms.media_type, sc.type)) AS type,
                       MAX(COALESCE(ms.name,       sc.name))  AS name
                FROM s3_scan_log s
                LEFT JOIN media_sources ms ON ms.slug = s.stream AND ms.lifecycle_status = 'active'
                LEFT JOIN stream_catalog sc ON sc.id = s.stream
                WHERE COALESCE(s.hour_start_utc, s.scanned_at) >= NOW() - (%s || ' days')::interval
                GROUP BY 1, 2, 3
                ORDER BY 1, 2, 3
            """, (days,))
            rows = cur.fetchall()

            # Video REAL (medición, no inferencia): .ts efectivamente subidos a S3.
            # Compatibilidad: la tabla histórica mediadev_video_coverage alimenta el
            # panel viejo; recording_coverage es el ledger nuevo del recorder.
            video_set = {}
            try:
                cur.execute("""
                    WITH legacy_video AS (
                        SELECT s.stream AS stream_id,
                               date_trunc('hour', s.hour_utc) AS hour_utc,
                               SUM(s.segs)  AS segs,
                               SUM(s.bytes) AS bytes
                        FROM mediadev_video_coverage s
                        WHERE s.hour_utc >= NOW() - (%s || ' days')::interval
                        GROUP BY 1, 2
                    ),
                    ledger_video AS (
                        SELECT c.stream_id,
                               date_trunc('hour', c.period_start_utc) AS hour_utc,
                               CEIL(COALESCE(MAX(c.actual_seconds), 0) / 4.0)::int AS segs,
                               MAX(COALESCE(c.size_bytes, 0)) AS bytes
                        FROM recording_coverage c
                        WHERE c.media_type = 'video'
                          AND c.status = 'uploaded'
                          AND c.period_start_utc >= NOW() - (%s || ' days')::interval
                        GROUP BY 1, 2
                    ),
                    unioned AS (
                        SELECT * FROM legacy_video
                        UNION ALL
                        SELECT * FROM ledger_video
                    )
                    SELECT u.stream_id,
                           (u.hour_utc AT TIME ZONE 'America/Tegucigalpa')::date AS day,
                           EXTRACT(HOUR FROM u.hour_utc AT TIME ZONE 'America/Tegucigalpa')::int AS hour,
                           MAX(u.segs)  AS segs,
                           MAX(u.bytes) AS bytes
                    FROM unioned u
                    GROUP BY 1, 2, 3
                """, (days, days))
                for vr in cur.fetchall():
                    vday = vr['day'].isoformat() if vr['day'] else None
                    video_set[(vr['stream_id'], vday, vr['hour'])] = {
                        'segs': vr['segs'], 'bytes': int(vr['bytes'] or 0)}
            except Exception:
                # Tabla aún no existe / sin permisos → degradar a 0 video (no romper el panel)
                video_set = {}

    streams, cells = {}, []
    audio_hours = 0
    audio_keys = set()
    for r in rows:
        sid = r['stream_id']
        stype = r['type'] or 'radio'
        day = r['day'].isoformat() if r['day'] else None
        has_video = (sid, day, r['hour']) in video_set
        kind = 'av' if has_video else 'audio'
        audio_hours += 1
        audio_keys.add((sid, day, r['hour']))
        if sid not in streams:
            streams[sid] = {'id': sid, 'name': r['name'] or sid, 'type': stype}
        cells.append({
            'stream_id': sid,
            'day': day,
            'hour': r['hour'],
            'kind': kind,
            'segs': r['segs'],
            'detections': r['detections'],
        })

    # Horas de video reales = celdas con marcador en mediadev_video_coverage.
    video_hours = len(video_set)
    # Celdas con video pero SIN audio (raro): mostrarlas como 'video' (naranja).
    for (sid, day, hour), v in video_set.items():
        if (sid, day, hour) not in audio_keys:
            if sid not in streams:
                streams[sid] = {'id': sid, 'name': sid, 'type': 'tv'}
            cells.append({
                'stream_id': sid, 'day': day, 'hour': hour,
                'kind': 'video', 'segs': v['segs'], 'detections': 0,
            })

    stream_list = sorted(streams.values(), key=lambda s: (s['type'] != 'tv', s['id']))
    tv = sum(1 for s in stream_list if s['type'] == 'tv')
    return {
        'streams': stream_list,
        'cells': cells,
        'summary': {
            'streams_active': len(stream_list),
            'tv': tv,
            'radio': len(stream_list) - tv,
            'audio_hours': audio_hours,
            'video_hours': video_hours,
            'period_days': days,
        },
        'updated': _now_hn().isoformat(),
    }
@router.get('/api/cobertura/gateways')
def cobertura_gateways(_auth: bool = Depends(verify_cobertura_token)):
    now = datetime.now(timezone.utc)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT g.gateway_id, g.name, g.city, g.device_type, g.wg_ip,
                       g.priority, g.max_streams, g.status, g.score, g.maintenance,
                       g.last_heartbeat, g.agent_version, g.is_active,
                       h.cpu_pct, h.ram_pct, h.temp_c, h.uptime_s,
                       h.internet_ok, h.socks5_ok, h.external_ip, h.latency_ms,
                       h.packet_loss_pct, h.wg_handshake_age_s, h.recorded_at AS health_at,
                       CASE WHEN g.is_active THEN
                           (SELECT COUNT(*) FROM capture_config cc
                            JOIN media_sources ms ON ms.id = cc.media_source_id
                            WHERE cc.route = 'socks5'
                              AND cc.is_enabled = true
                              AND ms.lifecycle_status = 'active')
                       ELSE
                           (SELECT COUNT(*) FROM stream_assignments sa
                            WHERE sa.primary_gateway_id = g.gateway_id)
                       END AS active_streams
                FROM gateways g
                LEFT JOIN LATERAL (
                    SELECT * FROM gateway_health_log
                    WHERE gateway_id = g.gateway_id
                    ORDER BY recorded_at DESC LIMIT 1
                ) h ON true
                ORDER BY g.priority
            """)
            rows = cur.fetchall()

    gateways = []
    for r in rows:
        d = dict(r)
        # Dos fuentes de verdad, no solo el heartbeat del agente:
        #   1) heartbeat del agente (≤90s) → ONLINE con métricas ricas
        #   2) sonda SOCKS5 reciente de health_engine (≤120s y socks5_ok) → ONLINE sin agente
        hb_age = (now - r['last_heartbeat']).total_seconds() if r['last_heartbeat'] else None
        health_age = (now - r['health_at']).total_seconds() if r['health_at'] else None
        heartbeat_online = hb_age is not None and hb_age <= 90
        probe_online = (health_age is not None and health_age <= 120 and bool(r['socks5_ok']))
        online = bool(heartbeat_online or probe_online)
        agentless = bool(online and not heartbeat_online)
        if r['maintenance']:
            display = 'MAINTENANCE'
        elif online:
            display = 'ONLINE'
        else:
            display = 'OFFLINE'
        d['heartbeat_age_s'] = round(hb_age) if hb_age is not None else None
        d['health_age_s'] = round(health_age) if health_age is not None else None
        d['online'] = online
        d['agentless'] = agentless
        d['display_status'] = display
        d['last_heartbeat'] = _to_hn(r['last_heartbeat'])
        d['health_at'] = _to_hn(r['health_at'])
        gateways.append(d)

    return {'gateways': gateways, 'count': len(gateways), 'updated': _now_hn().isoformat()}
@router.get('/api/cobertura/gateways/uptime')
def cobertura_gateways_uptime(period: str = Query('24h', pattern='^(24h|7d|30d)$'),
                              _auth: bool = Depends(verify_cobertura_token)):
    if period == '24h':
        interval, trunc = '24 hours', 'hour'
    elif period == '7d':
        interval, trunc = '7 days', 'day'
    else:
        interval, trunc = '30 days', 'day'

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT gateway_id,
                       date_trunc('{trunc}', recorded_at AT TIME ZONE 'America/Tegucigalpa') AS bucket,
                       AVG(CASE WHEN internet_ok AND socks5_ok THEN 1.0 ELSE 0.0 END) AS uptime_ratio,
                       COUNT(*) AS samples
                FROM gateway_health_log
                WHERE recorded_at >= NOW() - %s::interval
                GROUP BY 1, 2
                ORDER BY 1, 2
            """, (interval,))
            rows = cur.fetchall()

    by_gw = {}
    for r in rows:
        ratio = float(r['uptime_ratio'])
        by_gw.setdefault(r['gateway_id'], []).append({
            'bucket': r['bucket'].isoformat() if r['bucket'] else None,
            'uptime_pct': round(ratio * 100, 1),
            'online': ratio >= 0.5 if period == '24h' else None,
            'samples': r['samples'],
        })

    summary = {}
    for gw, buckets in by_gw.items():
        if buckets:
            avg = sum(b['uptime_pct'] for b in buckets) / len(buckets)
            summary[gw] = round(avg, 1)
    return {'period': period, 'timelines': by_gw, 'uptime_pct': summary,
            'updated': _now_hn().isoformat()}
def _run_dict(r):
    d = dict(r)
    work = r['work_seconds']
    d['throughput_per_min'] = (round(r['files_done'] / (work / 60.0), 1)
                               if work and work > 0 and r['files_done'] else 0)
    d['cost_usd'] = float(r['cost_usd']) if r['cost_usd'] is not None else None
    d['anomaly'] = bool(
        r['status'] in ('killed', 'timeout', 'failed')
        or (r['total_detections'] == 0 and (r['files_done'] or 0) > 0)
        or ((r['boot_seconds'] or 0) > 300)
    )
    for k in ('t1_deployed', 't2_started', 't3_completed', 't4_destroyed', 'last_activity'):
        d[k + '_hn'] = _to_hn(r[k])
        d[k] = _to_hn(r[k])
    return d

_RUN_COLS = """id, droplet_name, status, release_version, boot_seconds, work_seconds,
               total_seconds, total_files, files_done, files_error, total_detections,
               cost_usd, t1_deployed, t2_started, t3_completed, t4_destroyed, last_activity"""

@router.get('/api/cobertura/destroyer/runs')
def cobertura_destroyer_runs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                             _auth: bool = Depends(verify_cobertura_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {_RUN_COLS} FROM destroyer_runs ORDER BY id DESC LIMIT %s OFFSET %s",
                        (limit, offset))
            runs = [_run_dict(r) for r in cur.fetchall()]
    return {'runs': runs, 'count': len(runs), 'updated': _now_hn().isoformat()}

@router.get('/api/cobertura/destroyer/active')
def cobertura_destroyer_active(_auth: bool = Depends(verify_cobertura_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT {_RUN_COLS} FROM destroyer_runs
                            WHERE status IN ('running', 'deploying')
                            ORDER BY id DESC LIMIT 1""")
            r = cur.fetchone()
    return {'active': _run_dict(r) if r else None, 'updated': _now_hn().isoformat()}

@router.get('/api/cobertura/destroyer/stats')
def cobertura_destroyer_stats(days: int = Query(30, ge=1, le=120),
                              _auth: bool = Depends(verify_cobertura_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""SELECT {_RUN_COLS} FROM destroyer_runs
                            WHERE COALESCE(t2_started, t1_deployed) >= NOW() - (%s || ' days')::interval
                            ORDER BY id DESC""", (days,))
            rows = [_run_dict(r) for r in cur.fetchall()]

    month0 = _month_start_utc().isoformat()
    runs_month = [r for r in rows if (r.get('t2_started') or r.get('t1_deployed') or '') >= month0]
    completed = [r for r in rows if r['status'] in _SUCCESS_STATUSES]

    det_month = sum((r['total_detections'] or 0) for r in runs_month)
    cost_month = sum((r['cost_usd'] or
                      ((r['total_seconds'] or 0) / 3600.0 * _DESTROYER_RATE))
                     for r in runs_month)
    last7 = [r['throughput_per_min'] for r in completed[:7] if r['throughput_per_min']]
    avg_thr = round(sum(last7) / len(last7), 1) if last7 else 0
    success_rate = round(len([r for r in rows if r['status'] in _SUCCESS_STATUSES]) / len(rows) * 100, 1) if rows else 0

    # Detecciones por día (HN)
    det_by_day = {}
    for r in rows:
        d = (r.get('t2_started') or r.get('t1_deployed') or '')[:10]
        if d:
            det_by_day[d] = det_by_day.get(d, 0) + (r['total_detections'] or 0)

    # Throughput promedio por release (solo completadas)
    thr_rel = {}
    for r in completed:
        rel = r['release_version'] or 'desconocido'
        if r['throughput_per_min']:
            thr_rel.setdefault(rel, []).append(r['throughput_per_min'])
    thr_by_release = {k: round(sum(v) / len(v), 1) for k, v in thr_rel.items()}

    # Distribución de tiempos (total_seconds)
    buckets = {'<30s': 0, '30-60s': 0, '60-90s': 0, '>90s': 0}
    for r in rows:
        t = r['total_seconds']
        if t is None:
            continue
        if t < 30:    buckets['<30s'] += 1
        elif t < 60:  buckets['30-60s'] += 1
        elif t < 90:  buckets['60-90s'] += 1
        else:         buckets['>90s'] += 1

    by_status = {}
    for r in runs_month:
        by_status[r['status']] = by_status.get(r['status'], 0) + 1

    return {
        'cards': {
            'runs_month': len(runs_month),
            'runs_by_status': by_status,
            'detections_month': det_month,
            'cost_month_usd': round(cost_month, 4),
            'avg_throughput': avg_thr,
            'success_rate': success_rate,
            'last_run': rows[0] if rows else None,
        },
        'detections_by_day': [{'day': k, 'detections': det_by_day[k]} for k in sorted(det_by_day)],
        'throughput_by_release': thr_by_release,
        'time_distribution': buckets,
        'period_days': days,
        'hourly_rate': _DESTROYER_RATE,
        'updated': _now_hn().isoformat(),
    }
_COST_CACHE = {}
_COST_TTL = 3600

_SERVICE_MAP = {
    'Amazon Elastic Compute Cloud - Compute': 'EC2',
    'EC2 - Other':                            'EC2',
    'Amazon Simple Storage Service':          'S3',
    'Amazon Elastic Block Store':             'Snapshots',
    'AWS Data Transfer':                      'DataTransfer',
    'Amazon Relational Database Service':     'RDS',
    'AWS Lambda':                             'Lambda',
}

def _ce_costs():
    now = _time.time()
    if 'data' in _COST_CACHE and now - _COST_CACHE['ts'] < _COST_TTL:
        return _COST_CACHE['data']

    client = boto3.client('ce', region_name='us-east-1',
                          aws_access_key_id=AWS_KEY or None,
                          aws_secret_access_key=AWS_SECRET or None)
    today = _now_hn().date()
    first = today.replace(day=1)
    tomorrow = today + timedelta(days=1)
    days_in_month = ((first + timedelta(days=32)).replace(day=1) - first).days
    days_elapsed = today.day

    monthly = client.get_cost_and_usage(
        TimePeriod={'Start': first.isoformat(), 'End': tomorrow.isoformat()},
        Granularity='DAILY',
        GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
        Metrics=['UnblendedCost'])

    prev_start = (first - timedelta(days=1)).replace(day=1)
    prev = client.get_cost_and_usage(
        TimePeriod={'Start': prev_start.isoformat(), 'End': first.isoformat()},
        Granularity='MONTHLY',
        GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
        Metrics=['UnblendedCost'])

    daily, by_service, month_total = [], {}, 0.0
    for day in monthly['ResultsByTime']:
        date = day['TimePeriod']['Start']
        svc = {}
        day_total = 0.0
        for g in day['Groups']:
            raw = g['Keys'][0]
            label = _SERVICE_MAP.get(raw, 'Otros')
            amt = float(g['Metrics']['UnblendedCost']['Amount'])
            svc[label] = round(svc.get(label, 0) + amt, 4)
            by_service[label] = round(by_service.get(label, 0) + amt, 4)
            day_total += amt
        month_total += day_total
        daily.append({'date': date, 'services': svc, 'total': round(day_total, 4)})

    prev_total = 0.0
    for day in prev['ResultsByTime']:
        for g in day['Groups']:
            prev_total += float(g['Metrics']['UnblendedCost']['Amount'])

    today_cost = daily[-1]['total'] if daily and daily[-1]['date'] == today.isoformat() else 0.0
    projection = round(month_total / days_elapsed * days_in_month, 2) if days_elapsed else month_total
    vs_pct = round((month_total - prev_total) / prev_total * 100, 1) if prev_total else None

    data = {
        'summary': {
            'today': round(today_cost, 2),
            'month': round(month_total, 2),
            'projection': projection,
            'prev_month': round(prev_total, 2),
            'vs_prev_pct': vs_pct,
            'days_elapsed': days_elapsed,
            'days_in_month': days_in_month,
        },
        'by_service': by_service,
        'daily': daily,
        'available': True,
        'note': 'Datos de AWS con hasta 24h de retraso',
        'updated': _now_hn().isoformat(),
    }
    _COST_CACHE['data'] = data
    _COST_CACHE['ts'] = now
    return data

@router.get('/api/cobertura/costs/summary')
def cobertura_costs_summary(_auth: bool = Depends(verify_cobertura_token)):
    try:
        d = _ce_costs()
        return {'summary': d['summary'], 'by_service': d['by_service'],
                'available': True, 'note': d['note'], 'updated': d['updated']}
    except Exception as e:
        log.warning('Cost Explorer summary error: %s', e)
        return {'available': False, 'error': str(e), 'updated': _now_hn().isoformat()}

@router.get('/api/cobertura/costs/daily')
def cobertura_costs_daily(_auth: bool = Depends(verify_cobertura_token)):
    try:
        d = _ce_costs()
        return {'daily': d['daily'], 'by_service': d['by_service'],
                'available': True, 'note': d['note'], 'updated': d['updated']}
    except Exception as e:
        log.warning('Cost Explorer daily error: %s', e)
        return {'available': False, 'error': str(e), 'updated': _now_hn().isoformat()}


# ── Página HTML (la diseña Claude Design → cobertura.html). Token simple. ──────
def _denied():
    return HTMLResponse(status_code=403, content=(
        '<!doctype html><meta charset=utf-8><title>Acceso denegado</title>'
        '<body style="font-family:system-ui;background:#0B0D10;color:#E4E9EF;'
        'display:flex;align-items:center;justify-content:center;height:100vh;margin:0">'
        '<div style="text-align:center"><h1 style="color:#FF8589">403 · Acceso denegado</h1>'
        '<p style="color:#7C8794">Token inválido o ausente.</p></div></body>'))

@router.get('/cobertura', response_class=HTMLResponse)
def cobertura_page(token: str = Query(None)):
    if not _COBERTURA_TOKEN or not token or not secrets.compare_digest(token, _COBERTURA_TOKEN):
        return _denied()
    try:
        with open(_COBERTURA_HTML) as fh:
            return HTMLResponse(content=fh.read())
    except FileNotFoundError:
        return HTMLResponse(status_code=503, content=(
            '<!doctype html><meta charset=utf-8><title>Cobertura</title>'
            '<body style="font-family:system-ui;background:#0B0D10;color:#E4E9EF;'
            'display:flex;align-items:center;justify-content:center;height:100vh;margin:0">'
            '<div style="text-align:center"><h1 style="color:#1CC0F9">Cobertura</h1>'
            '<p style="color:#7C8794">Falta <code>cobertura.html</code>. '
            'Genera el front con Claude Design y colócalo en /opt/media-app/.<br>'
            'Los endpoints <code>/api/cobertura/*</code> ya están activos.</p></div></body>'))


# ── Static JS de la página Cobertura (assets sin token) + alias /cobertura.html ─
# routers/ está un nivel bajo la raíz del proyecto — cobertura_static/ vive en la raíz.
_COBERTURA_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cobertura_static')
if os.path.isdir(_COBERTURA_STATIC_DIR):
    router.mount('/cobertura-static',
                 _StaticFiles(directory=_COBERTURA_STATIC_DIR),
                 name='cobertura_static')

@router.get('/cobertura.html', response_class=HTMLResponse)
def cobertura_page_html(token: str = Query(None)):
    return cobertura_page(token)
