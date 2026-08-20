from typing import Optional

from fastapi import APIRouter, Depends, Query

from core.db import get_db
from core.security import verify_cobertura_token
from core.timehn import now_hn

router = APIRouter(prefix='/api/mediadev', tags=['mediadev'])

# ── MEDIADEV (proxy de datos operativos para el frontend PubliAudit) ───────────
# Lee directo de media-db (misma DB que escribe MediaDEV). Mismo formato que los
# endpoints http://159.223.104.91/api/* — así el frontend los consume desde el
# mismo origen (sin mixed-content ni CORS). Públicos, sin auth (datos operativos).

@router.get('/summary')
def mediadev_summary(_auth: bool = Depends(verify_cobertura_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) c FROM mediadev_stream_status WHERE status='OK'")
            online = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) c FROM mediadev_stream_status")
            total_live = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) total, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active FROM stream_catalog")
            cat = cur.fetchone()
            today = now_hn().replace(hour=0, minute=0, second=0, microsecond=0)
            cur.execute("SELECT COUNT(*) c FROM fingerprint_detections WHERE deleted_at IS NULL AND air_time >= %s", (today,))
            det_today = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) c FROM fingerprint_detections WHERE deleted_at IS NULL")
            det_total = cur.fetchone()['c']
    return {
        'streams_live': {'online': online, 'total': total_live},
        'catalog':      {'active': cat['active'] or 0, 'total': cat['total'] or 0},
        'detections':   {'today': det_today, 'total': det_total},
        'updated':      now_hn().isoformat(),
    }

@router.get('/streams')
def mediadev_streams(_auth: bool = Depends(verify_cobertura_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mediadev_stream_status ORDER BY stream_id")
            rows = {r['stream_id']: dict(r) for r in cur.fetchall()}
    ok = sum(1 for r in rows.values() if r['status'] == 'OK')
    return {'streams': rows, 'summary': {'ok': ok, 'total': len(rows)},
            'updated': now_hn().isoformat()}

@router.get('/stations')
def mediadev_stations(status: Optional[str] = None, type: Optional[str] = None,
                      _auth: bool = Depends(verify_cobertura_token)):
    where, params = [], []
    if status:
        where.append('status = %s'); params.append(status)
    if type:
        where.append('type = %s'); params.append(type)
    sql = ("SELECT id, name, type, country_code, city, frequency, coverage,"
           " callsign, logo_url, stream_url, status FROM stream_catalog")
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY status, name'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = [dict(r) for r in cur.fetchall()]
    return {'stations': rows, 'count': len(rows), 'updated': now_hn().isoformat()}

@router.get('/detections')
def mediadev_detections(limit: int = Query(50, le=500, ge=1),
                        _auth: bool = Depends(verify_cobertura_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT d.id, d.stream_id, a.name AS ad_name, d.air_time, d.ts_label,"
                " d.score, d.confidence_level, d.clip_s3_key, d.detected_at"
                " FROM fingerprint_detections d"
                " LEFT JOIN advertisements a ON a.id = d.ad_id"
                " WHERE d.deleted_at IS NULL"
                " ORDER BY d.air_time DESC LIMIT %s", (limit,))
            rows = [dict(r) for r in cur.fetchall()]
    return {'detections': rows, 'count': len(rows), 'updated': now_hn().isoformat()}



