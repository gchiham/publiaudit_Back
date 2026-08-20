from typing import Optional

import boto3
from fastapi import APIRouter, Depends, HTTPException, Query

from auth.clerk import verify_clerk_token as verify_token
from core.db import get_db
from core.s3 import S3_BUCKET, S3_REGION

router = APIRouter(prefix='/api', tags=['detections'])

# ── DETECTIONS ────────────────────────────────────────────────────────────────
@router.get('/detections')
def list_detections(
    campaign_id: Optional[str]  = None,
    ad_id:       Optional[str]  = None,
    stream_id:   Optional[str]  = None,
    confidence:  Optional[str]  = None,
    date_from:   Optional[str]  = None,
    date_to:     Optional[str]  = None,
    q:           Optional[str]  = None,
    page:        int            = Query(1, ge=1),
    page_size:   int            = Query(50, ge=1, le=200),
    ctx = Depends(verify_token)
):
    offset = (page - 1) * page_size
    conditions = ['fd.tenant_id = %s', 'fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)']
    params = [ctx['tenant_id']]

    if campaign_id:
        conditions.append('fd.campaign_id = %s'); params.append(campaign_id)
    if ad_id:
        conditions.append('fd.ad_id = %s'); params.append(ad_id)
    if stream_id:
        conditions.append('fd.stream_id = %s'); params.append(stream_id)
    if confidence:
        conditions.append('fd.confidence_level = %s'); params.append(confidence)
    if date_from:
        conditions.append("fd.air_time >= %s::date"); params.append(date_from)
    if date_to:
        conditions.append("fd.air_time < (%s::date + INTERVAL '1 day')"); params.append(date_to)
    if q:
        conditions.append('(ad.name ILIKE %s OR camp.name ILIKE %s OR fd.stream_id ILIKE %s)')
        like = f'%{q}%'
        params.extend([like, like, like])

    where = ' AND '.join(conditions)

    with get_db() as conn:
        with conn.cursor() as cur:
            # Total (mismos JOINs que la consulta de datos: q busca en ad.name/camp.name)
            cur.execute(f'''
                SELECT COUNT(*) as total
                FROM fingerprint_detections fd
                JOIN advertisements ad ON fd.ad_id = ad.id
                JOIN campaigns camp    ON fd.campaign_id = camp.id
                WHERE {where}
            ''', params)
            total = cur.fetchone()['total']

            # Datos
            cur.execute(f'''
                SELECT fd.id, fd.stream_id,
                       ad.name as ad_name,
                       camp.name as campaign_name,
                       (fd.air_time AT TIME ZONE 'America/Tegucigalpa') as air_time_hn,
                       fd.ts_label, fd.score, fd.confidence_level,
                       fd.clip_s3_key, fd.algorithm, fd.created_at,
                       (camp.end_date IS NOT NULL
                        AND (fd.air_time AT TIME ZONE 'America/Tegucigalpa')::date > camp.end_date)
                          AS over_airing   -- sobre-pauta: emitido después de la fecha de fin
                FROM fingerprint_detections fd
                JOIN advertisements ad  ON fd.ad_id = ad.id
                JOIN campaigns camp     ON fd.campaign_id = camp.id
                WHERE {where}
                ORDER BY fd.air_time DESC
                LIMIT %s OFFSET %s
            ''', params + [page_size, offset])

            return {
                'total':    total,
                'page':     page,
                'pages':    (total + page_size - 1) // page_size,
                'results':  [dict(r) for r in cur.fetchall()],
            }

@router.get('/detections/{detection_id}')
def get_detection(detection_id: int, ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT fd.*,
                       ad.name as ad_name, ad.duration_seconds,
                       camp.name as campaign_name,
                       (fd.air_time AT TIME ZONE 'America/Tegucigalpa') as air_time_hn
                FROM fingerprint_detections fd
                JOIN advertisements ad ON fd.ad_id = ad.id
                JOIN campaigns camp    ON fd.campaign_id = camp.id
                WHERE fd.id = %s AND fd.tenant_id = %s AND fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)
            ''', (detection_id, ctx['tenant_id']))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail='Detección no encontrada')
            return dict(row)

# ── TIMELINE ──────────────────────────────────────────────────────────────────
@router.get('/timeline')
def timeline(
    campaign_id: Optional[str] = None,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
    ctx = Depends(verify_token)
):
    """Datos para vista Gantt: airings agrupados por medio y hora."""
    conditions = ['fd.tenant_id = %s', 'fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)']
    params = [ctx['tenant_id']]

    if campaign_id:
        conditions.append('fd.campaign_id = %s'); params.append(campaign_id)
    if date_from:
        conditions.append("fd.air_time >= %s::date"); params.append(date_from)
    if date_to:
        conditions.append("fd.air_time < (%s::date + INTERVAL '1 day')"); params.append(date_to)
    else:
        conditions.append("fd.air_time >= NOW() - INTERVAL '7 days'")

    where = ' AND '.join(conditions)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f'''
                SELECT
                    fd.stream_id,
                    ad.name as ad_name,
                    (fd.air_time AT TIME ZONE 'America/Tegucigalpa') as air_time_hn,
                    fd.score, fd.confidence_level
                FROM fingerprint_detections fd
                JOIN advertisements ad ON fd.ad_id = ad.id
                WHERE {where}
                ORDER BY fd.stream_id, fd.air_time
            ''', params)
            rows = [dict(r) for r in cur.fetchall()]

    # Agrupar por medio para estructura de Gantt
    medios = {}
    for r in rows:
        m = r['stream_id']
        if m not in medios:
            medios[m] = []
        medios[m].append({
            'ad_name':    r['ad_name'],
            'air_time':   r['air_time_hn'].isoformat() if r['air_time_hn'] else None,
            'score':      r['score'],
            'confidence': r['confidence_level'],
        })

    return {
        'medios': [{'stream_id': k, 'airings': v} for k, v in medios.items()],
        'total_airings': len(rows),
    }

# ── COMPROBANTE ───────────────────────────────────────────────────────────────
@router.get('/comprobante/{ad_id}')
def comprobante(
    ad_id:     str,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    ctx = Depends(verify_token)
):
    """Comprobante de transmisión por anuncio — datos para generar PDF."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # Info del anuncio
            cur.execute('''
                SELECT a.name, a.duration_seconds,
                       camp.name as campaign_name,
                       c.name as client_name
                FROM advertisements a
                JOIN campaigns camp ON a.campaign_id = camp.id
                JOIN tenants c      ON a.tenant_id = c.id
                WHERE a.id = %s AND a.tenant_id = %s
            ''', (ad_id, ctx['tenant_id']))
            ad = cur.fetchone()
            if not ad:
                raise HTTPException(status_code=404, detail='Anuncio no encontrado')

            conditions = ['fd.ad_id = %s', 'fd.tenant_id = %s', 'fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)']
            params = [ad_id, ctx['tenant_id']]
            if date_from:
                conditions.append("fd.air_time >= %s::date"); params.append(date_from)
            if date_to:
                conditions.append("fd.air_time < (%s::date + INTERVAL '1 day')"); params.append(date_to)
            where = ' AND '.join(conditions)

            cur.execute(f'''
                SELECT
                    fd.stream_id as medio,
                    DATE(fd.air_time AT TIME ZONE 'America/Tegucigalpa') as fecha,
                    TO_CHAR(fd.air_time AT TIME ZONE 'America/Tegucigalpa', 'HH24:MI') as hora,
                    fd.score, fd.confidence_level
                FROM fingerprint_detections fd
                WHERE {where}
                ORDER BY fd.stream_id, fd.air_time
            ''', params)
            airings = [dict(r) for r in cur.fetchall()]

    return {
        'ad':       dict(ad),
        'airings':  airings,
        'total':    len(airings),
        'periodo':  {'desde': date_from, 'hasta': date_to},
    }

# ── DETECTIONS — clip presigned URL ──────────────────────────────────────────

@router.get('/detections/{detection_id}/clip')
def get_detection_clip(detection_id: int, ctx = Depends(verify_token)):
    """URL pre-firmada S3 (5 min) para reproducir el clip de evidencia."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT clip_s3_key FROM fingerprint_detections
                WHERE id = %s AND tenant_id = %s AND deleted_at IS NULL
            ''', (detection_id, ctx['tenant_id']))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, 'Detección no encontrada')
            if not row['clip_s3_key']:
                raise HTTPException(404, 'Esta detección no tiene clip de evidencia')
    s3 = boto3.client('s3', region_name=S3_REGION)
    url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': S3_BUCKET, 'Key': row['clip_s3_key']},
        ExpiresIn=300,
    )
