from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth.clerk import verify_clerk_token as verify_token
from core.db import get_db

router = APIRouter(prefix='/api', tags=['streams'])

# ── STREAMS CATALOG ───────────────────────────────────────────────────────────
@router.get('/streams')
def list_streams(ctx = Depends(verify_token)):
    """Catálogo completo de streams. Incluye is_subscribed para el cliente."""
    tenant_id = ctx['tenant_id']
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT
                    sc.id, sc.name, sc.type, sc.callsign, sc.frequency,
                    sc.city, sc.coverage, sc.logo_url, sc.stream_url, sc.status,
                    (cs.tenant_id IS NOT NULL)       AS is_subscribed,
                    cs.added_at                      AS subscribed_at,
                    COUNT(fd.id)                     AS total_detecciones
                FROM stream_catalog sc
                LEFT JOIN client_streams cs
                    ON cs.stream_id = sc.id AND cs.tenant_id = %s
                LEFT JOIN fingerprint_detections fd
                    ON fd.stream_id = sc.id AND fd.tenant_id = %s AND fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)
                GROUP BY sc.id, sc.name, sc.type, sc.callsign, sc.frequency,
                         sc.city, sc.coverage, sc.logo_url, sc.stream_url, sc.status,
                         cs.tenant_id, cs.added_at
                ORDER BY is_subscribed DESC, sc.type, sc.name
            ''', (tenant_id, tenant_id))
            return [dict(r) for r in cur.fetchall()]


@router.get('/my-streams')
def my_streams(ctx = Depends(verify_token)):
    """Solo los streams activos del cliente + estado del plan."""
    tenant_id = ctx['tenant_id']
    with get_db() as conn:
        with conn.cursor() as cur:
            # Plan info
            cur.execute('''
                SELECT p.name, p.display_name, p.max_streams
                FROM tenants c JOIN plans p ON p.id = c.plan_id
                WHERE c.id = %s
            ''', (tenant_id,))
            plan = dict(cur.fetchone())

            # Streams activos
            cur.execute('''
                SELECT sc.id, sc.name, sc.type, sc.callsign, sc.frequency,
                       sc.city, sc.coverage, sc.logo_url, sc.status,
                       cs.added_at,
                       COUNT(fd.id) AS total_detecciones,
                       MAX(fd.air_time) AS ultimo_airing
                FROM client_streams cs
                JOIN stream_catalog sc ON sc.id = cs.stream_id
                LEFT JOIN fingerprint_detections fd
                    ON fd.stream_id = sc.id AND fd.tenant_id = %s AND fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)
                WHERE cs.tenant_id = %s
                GROUP BY sc.id, sc.name, sc.type, sc.callsign, sc.frequency,
                         sc.city, sc.coverage, sc.logo_url, sc.status, cs.added_at
                ORDER BY sc.name
            ''', (tenant_id, tenant_id))
            streams = [dict(r) for r in cur.fetchall()]

    unlimited = plan['max_streams'] == -1
    return {
        'plan':             plan['display_name'],
        'max_streams':      plan['max_streams'],
        'ilimitado':        unlimited,
        'streams_activos':  len(streams),
        'slots_libres':     None if unlimited else max(0, plan['max_streams'] - len(streams)),
        'streams':          streams,
    }


@router.post('/my-streams/{stream_id}', status_code=201)
def subscribe_stream(stream_id: str, ctx = Depends(verify_token)):
    """Agrega un stream a la suscripción activa del cliente."""
    tenant_id = ctx['tenant_id']
    with get_db() as conn:
        with conn.cursor() as cur:
            # Verificar que el stream existe
            cur.execute('SELECT id, name FROM stream_catalog WHERE id = %s', (stream_id,))
            stream = cur.fetchone()
            if not stream:
                raise HTTPException(status_code=404, detail='Stream no encontrado')

            # Verificar si ya está suscrito
            cur.execute(
                'SELECT id FROM client_streams WHERE tenant_id = %s AND stream_id = %s',
                (tenant_id, stream_id)
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail='Ya estás suscrito a este stream')

            # Verificar límite del plan
            cur.execute('''
                SELECT p.max_streams,
                       (SELECT COUNT(*) FROM client_streams WHERE tenant_id = %s) AS activos
                FROM tenants c JOIN plans p ON p.id = c.plan_id
                WHERE c.id = %s
            ''', (tenant_id, tenant_id))
            row = cur.fetchone()
            if row['max_streams'] != -1 and row['activos'] >= row['max_streams']:
                raise HTTPException(
                    status_code=402,
                    detail=(
                        f"Límite del plan alcanzado ({row['max_streams']} streams). "
                        "Actualiza tu plan para agregar más streams."
                    )
                )

            # Agregar suscripción
            cur.execute('''
                INSERT INTO client_streams (tenant_id, stream_id, added_by)
                VALUES (%s, %s, %s)
                RETURNING id, added_at
            ''', (tenant_id, stream_id, ctx['sub']))
            result = cur.fetchone()

    return {
        'ok': True,
        'stream_id':  stream_id,
        'stream_name': stream['name'],
        'added_at':   result['added_at'].isoformat(),
    }


@router.delete('/my-streams/{stream_id}')
def unsubscribe_stream(stream_id: str, ctx = Depends(verify_token)):
    """Quita un stream de la suscripción activa del cliente."""
    tenant_id = ctx['tenant_id']
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                DELETE FROM client_streams
                WHERE tenant_id = %s AND stream_id = %s
            ''', (tenant_id, stream_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail='No estás suscrito a este stream')
    return {'ok': True, 'stream_id': stream_id}

# ── DESTROYER RUNS ────────────────────────────────────────────────────────────
@router.get('/runs')
def list_runs(limit: int = Query(10, ge=1, le=50), ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT droplet_name, status, files_done, total_files,
                       total_detections, t2_started, t3_completed, cost_usd
                FROM destroyer_runs
                ORDER BY t2_started DESC NULLS LAST
                LIMIT %s
            ''', (limit,))
            return [dict(r) for r in cur.fetchall()]

class RequestMediaSourceBody(BaseModel):
    signal_name:       str
    media_type:        str | None = None   # 'radio' | 'tv'
    country:           str | None = 'HN'
    city:              str | None = None
    frequency_channel: str | None = None
    stream_url_hint:   str | None = None
    notes:             str | None = None


# ── MEDIA SOURCES (nuevo schema) ──────────────────────────────────────────────

@router.get('/media-sources')
def list_media_sources(ctx = Depends(verify_token)):
    """Estaciones activas asignadas al tenant, con stats de detecciones."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT ms.id, ms.slug, ms.name, ms.media_type, ms.country,
                       ms.frequency_channel, ms.description, ms.logo_url,
                       ms.health_status, ms.lifecycle_status,
                       tmsa.assigned_at,
                       COUNT(DISTINCT fd.id) AS total_detecciones
                FROM tenant_media_source_assignments tmsa
                JOIN media_sources ms ON ms.id = tmsa.media_source_id
                LEFT JOIN fingerprint_detections fd
                    ON (fd.media_source_id = ms.id OR fd.stream_id = ms.stream_catalog_id)
                   AND fd.tenant_id = %s
                   AND fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)
                WHERE tmsa.tenant_id = %s
                  AND tmsa.is_active = true
                  AND ms.lifecycle_status = 'active'
                GROUP BY ms.id, tmsa.assigned_at
                ORDER BY ms.media_type, ms.name
            ''', (ctx['tenant_id'], ctx['tenant_id']))
            return [dict(r) for r in cur.fetchall()]


@router.post('/media-sources/request', status_code=201)
def request_media_source(body: RequestMediaSourceBody, ctx = Depends(verify_token)):
    """Solicita agregar una nueva estación. Queda pendiente para revisión del staff."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                INSERT INTO media_source_requests
                    (tenant_id, requested_by_user_id, signal_name, media_type,
                     country, city, frequency_channel, stream_url_hint, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, signal_name, status, created_at
            ''', (ctx['tenant_id'], ctx['sub'],
                  body.signal_name.strip(), body.media_type,
                  body.country, body.city, body.frequency_channel,
                  body.stream_url_hint, body.notes))
            return dict(cur.fetchone())


@router.get('/media-sources/requests')
def list_media_source_requests(ctx = Depends(verify_token)):
    """Historial de solicitudes de nuevas estaciones del tenant."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT id, signal_name, media_type, country, city,
                       frequency_channel, stream_url_hint, notes,
                       status, review_notes, reviewed_at, created_at
                FROM media_source_requests
                WHERE tenant_id = %s
                ORDER BY created_at DESC
            ''', (ctx['tenant_id'],))
            return [dict(r) for r in cur.fetchall()]


