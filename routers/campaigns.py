import os
import tempfile, subprocess, shutil
from typing import Optional

import boto3
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from auth.clerk import verify_clerk_token as verify_token
from core.db import get_db
from core.s3 import AWS_KEY, AWS_SECRET, S3_BUCKET, S3_REGION, s3_put as _s3_put

router = APIRouter(prefix='/api/campaigns', tags=['campaigns'])

# ── CAMPAIGNS ─────────────────────────────────────────────────────────────────
@router.get('')
def list_campaigns(
    status: Optional[str] = None,
    client_id: Optional[str] = None,
    ctx = Depends(verify_token)
):
    with get_db() as conn:
        with conn.cursor() as cur:
            sql = '''
                SELECT c.id, c.name, c.description, c.status,
                       c.start_date, c.end_date, c.created_at,
                       c.client_id, cl.name AS client_name,
                       COUNT(DISTINCT a.id)  as total_ads,
                       COUNT(DISTINCT fd.id) as total_airings
                FROM campaigns c
                JOIN clients cl ON cl.id = c.client_id
                LEFT JOIN advertisements a  ON a.campaign_id = c.id AND a.deleted_at IS NULL
                LEFT JOIN fingerprint_detections fd ON fd.campaign_id = c.id AND fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)
                WHERE c.tenant_id = %s
            '''
            params = [ctx['tenant_id']]
            if status:
                sql += ' AND c.status = %s'; params.append(status)
            if client_id:
                sql += ' AND c.client_id = %s'; params.append(client_id)
            sql += ' GROUP BY c.id, cl.name ORDER BY c.created_at DESC'
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

@router.get('/{campaign_id}')
def get_campaign(campaign_id: str, ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT c.id, c.name, c.description, c.status,
                       c.start_date, c.end_date, c.created_at,
                       c.client_id, cl.name AS client_name,
                       COUNT(DISTINCT a.id)  as total_ads,
                       COUNT(DISTINCT fd.id) as total_airings,
                       ROUND(AVG(fd.score)::numeric, 0) as score_promedio
                FROM campaigns c
                JOIN clients cl ON cl.id = c.client_id
                LEFT JOIN advertisements a  ON a.campaign_id = c.id
                LEFT JOIN fingerprint_detections fd ON fd.campaign_id = c.id AND fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)
                WHERE c.id = %s AND c.tenant_id = %s
                GROUP BY c.id, cl.name
            ''', (campaign_id, ctx['tenant_id']))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail='Campaña no encontrada')
            return dict(row)

class UpdateCampaignRequest(BaseModel):
    client_id:     Optional[str]   = None
    name:          Optional[str]   = None
    description:   Optional[str]   = None
    status:        Optional[str]   = None
    campaign_type: Optional[str]   = None
    start_date:    Optional[str]   = None
    end_date:      Optional[str]   = None
    budget_usd:    Optional[float] = None

@router.patch('/{campaign_id}')
def update_campaign(campaign_id: str, body: UpdateCampaignRequest, ctx = Depends(verify_token)):
    """Actualiza campaña. Permite reasignar el anunciante (client_id)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM campaigns WHERE id = %s AND tenant_id = %s',
                        (campaign_id, ctx['tenant_id']))
            if not cur.fetchone():
                raise HTTPException(404, 'Campaña no encontrada')
            if body.client_id is not None:
                cur.execute('SELECT 1 FROM clients WHERE id = %s AND tenant_id = %s',
                            (body.client_id, ctx['tenant_id']))
                if not cur.fetchone():
                    raise HTTPException(400, 'El anunciante no existe o no es de tu cuenta')
            fields, params = [], []
            if body.client_id     is not None: fields.append('client_id = %s');     params.append(body.client_id)
            if body.name          is not None: fields.append('name = %s');          params.append(body.name)
            if body.description   is not None: fields.append('description = %s');   params.append(body.description)
            if body.status        is not None: fields.append('status = %s');        params.append(body.status)
            if body.campaign_type is not None: fields.append('campaign_type = %s'); params.append(body.campaign_type)
            if body.start_date    is not None: fields.append('start_date = %s');    params.append(body.start_date)
            if body.end_date      is not None: fields.append('end_date = %s');      params.append(body.end_date)
            if body.budget_usd    is not None: fields.append('budget_usd = %s');    params.append(body.budget_usd)
            if not fields:
                raise HTTPException(400, 'Nada que actualizar')
            fields.append('updated_at = NOW()')
            params += [campaign_id, ctx['tenant_id']]
            cur.execute('UPDATE campaigns SET ' + ', '.join(fields) +
                        ' WHERE id = %s AND tenant_id = %s'
                        ' RETURNING id, name, status, client_id', params)
            return dict(cur.fetchone())

# ── ADVERTISEMENTS ────────────────────────────────────────────────────────────
@router.get('/{campaign_id}/ads')
def list_ads(campaign_id: str, ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT a.id, a.name, a.description, a.status,
                       a.duration_seconds, a.match_min, a.clip_pad_seconds,
                       COUNT(fd.id) as total_airings,
                       MAX(fd.air_time) as ultimo_airing
                FROM advertisements a
                LEFT JOIN fingerprint_detections fd ON fd.ad_id = a.id AND fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)
                WHERE a.campaign_id = %s AND a.tenant_id = %s AND a.deleted_at IS NULL
                GROUP BY a.id ORDER BY a.name
            ''', (campaign_id, ctx['tenant_id']))
            return [dict(r) for r in cur.fetchall()]

# ── Modelos nuevos ────────────────────────────────────────────────────────────

class CreateCampaignRequest(BaseModel):
    client_id:     str
    name:          str
    description:   str | None = None
    campaign_type: str = 'scheduled'   # 'scheduled' | 'ongoing'
    start_date:    str | None = None
    end_date:      str | None = None   # requerido si scheduled, nulo si ongoing
    budget_usd:    float | None = None

# Padding de evidencia: presets cerrados (mismo valor antes/después). Default 5.
VALID_CLIP_PAD = (5, 10, 15, 30)

class CreateAdRequest(BaseModel):
    name:             str
    description:      str | None = None
    duration_seconds: int | None = None   # la determina el backend al generar la huella
    match_min:        float | None = None
    clip_pad_seconds: int = 5

class UpdateAdRequest(BaseModel):
    name:             str | None = None
    description:      str | None = None
    duration_seconds: int | None = None
    match_min:        float | None = None
    clip_pad_seconds: int | None = None
    status:           str | None = None

class SetCampaignSourcesRequest(BaseModel):
    media_source_ids: list[str]

# ── CAMPAIGNS v2 ──────────────────────────────────────────────────────────────

@router.post('', status_code=201)
def create_campaign(body: CreateCampaignRequest, ctx = Depends(verify_token)):
    """Crea campaña. 'scheduled' requiere end_date; 'ongoing' lo prohíbe."""
    if body.campaign_type == 'scheduled' and not body.end_date:
        raise HTTPException(400, "campaign_type='scheduled' requiere end_date")
    if body.campaign_type == 'ongoing' and body.end_date:
        raise HTTPException(400, "campaign_type='ongoing' no puede tener end_date")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM clients WHERE id = %s AND tenant_id = %s',
                        (body.client_id, ctx['tenant_id']))
            if not cur.fetchone():
                raise HTTPException(400, 'Anunciante no encontrado')
            cur.execute('''
                INSERT INTO campaigns
                    (tenant_id, client_id, name, description,
                     campaign_type, start_date, end_date, budget_usd)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, name, status, campaign_type,
                          start_date, end_date, budget_usd, created_at
            ''', (ctx['tenant_id'], body.client_id, body.name.strip(),
                  body.description, body.campaign_type,
                  body.start_date, body.end_date, body.budget_usd))
            return dict(cur.fetchone())


@router.delete('/{campaign_id}')
def archive_campaign(campaign_id: str, ctx = Depends(verify_token)):
    """Soft-delete: status → archived. Las detecciones existentes se conservan."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                UPDATE campaigns
                SET status = 'archived', updated_at = NOW()
                WHERE id = %s AND tenant_id = %s AND status != 'archived'
                RETURNING id
            ''', (campaign_id, ctx['tenant_id']))
            if not cur.fetchone():
                raise HTTPException(404, 'Campaña no encontrada o ya archivada')
    return {'ok': True}


# ── ADVERTISEMENTS v2 ─────────────────────────────────────────────────────────

@router.post('/{campaign_id}/ads', status_code=201)
def create_ad(campaign_id: str, body: CreateAdRequest, ctx = Depends(verify_token)):
    if body.clip_pad_seconds not in VALID_CLIP_PAD:
        raise HTTPException(400, 'clip_pad_seconds debe ser 5, 10, 15 o 30')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT client_id FROM campaigns WHERE id = %s AND tenant_id = %s',
                        (campaign_id, ctx['tenant_id']))
            camp = cur.fetchone()
            if not camp:
                raise HTTPException(404, 'Campaña no encontrada')
            cur.execute('''
                INSERT INTO advertisements
                    (tenant_id, campaign_id, client_id, name, description,
                     duration_seconds, match_min, clip_pad_seconds)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, name, description, status,
                          duration_seconds, match_min, clip_pad_seconds, created_at
            ''', (ctx['tenant_id'], campaign_id, camp['client_id'],
                  body.name.strip(), body.description,
                  body.duration_seconds, body.match_min, body.clip_pad_seconds))
            return dict(cur.fetchone())


@router.patch('/{campaign_id}/ads/{ad_id}')
def update_ad(campaign_id: str, ad_id: str, body: UpdateAdRequest, ctx = Depends(verify_token)):
    fields, params = [], []
    if body.name             is not None: fields.append('name = %s');             params.append(body.name.strip())
    if body.description      is not None: fields.append('description = %s');      params.append(body.description)
    if body.duration_seconds is not None: fields.append('duration_seconds = %s'); params.append(body.duration_seconds)
    if body.match_min        is not None: fields.append('match_min = %s');        params.append(body.match_min)
    if body.clip_pad_seconds is not None:
        if body.clip_pad_seconds not in VALID_CLIP_PAD:
            raise HTTPException(400, 'clip_pad_seconds debe ser 5, 10, 15 o 30')
        fields.append('clip_pad_seconds = %s'); params.append(body.clip_pad_seconds)
    if body.status           is not None: fields.append('status = %s');           params.append(body.status)
    if not fields:
        raise HTTPException(400, 'Nada que actualizar')
    fields.append('updated_at = NOW()')
    params += [ad_id, campaign_id, ctx['tenant_id']]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'UPDATE advertisements SET ' + ', '.join(fields) +
                ' WHERE id = %s AND campaign_id = %s AND tenant_id = %s AND deleted_at IS NULL'
                ' RETURNING id, name, description, status, duration_seconds, match_min, clip_pad_seconds',
                params)
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, 'Anuncio no encontrado')
            return dict(row)


@router.delete('/{campaign_id}/ads/{ad_id}')
def delete_ad(campaign_id: str, ad_id: str, ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                UPDATE advertisements SET deleted_at = NOW()
                WHERE id = %s AND campaign_id = %s AND tenant_id = %s AND deleted_at IS NULL
            ''', (ad_id, campaign_id, ctx['tenant_id']))
            if cur.rowcount == 0:
                raise HTTPException(404, 'Anuncio no encontrado')
    return {'ok': True}


@router.post('/{campaign_id}/ads/{ad_id}/master')
def upload_ad_master(campaign_id: str, ad_id: str,
                     file: UploadFile = File(...), ctx = Depends(verify_token)):
    """Sube el máster del anuncio (audio O video, cualquier formato). Guarda el original en
    masters/ (auditoría), extrae el audio a mp3 con ffmpeg y lo coloca en
    references/<ad_id>.mp3 — que es lo que lee el worker del Destroyer (liga por ad_id).
    El worker, en su próximo arranque, fingerprintea ese mp3 y rellena duration_seconds +
    match_min (señal de 'huella lista'). NO se toca 'status' (queda 'active' para que el
    worker lo tome; la conversión es rara y barata → se hace aquí, no en el Destroyer)."""
    import tempfile, subprocess, shutil
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM advertisements '
                        'WHERE id = %s AND campaign_id = %s AND tenant_id = %s AND deleted_at IS NULL',
                        (ad_id, campaign_id, ctx['tenant_id']))
            if not cur.fetchone():
                raise HTTPException(404, 'Anuncio no encontrado')
    if not AWS_KEY:
        raise HTTPException(503, 'Almacenamiento S3 no configurado')
    ext = (os.path.splitext(file.filename or '')[1] or '.bin').lower()
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, 'master' + ext)
        with open(src, 'wb') as out:                 # stream a disco (no 200MB en RAM)
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        size = os.path.getsize(src)
        if size == 0:
            raise HTTPException(400, 'Archivo vacío')
        if size > 200 * 1024 * 1024:
            raise HTTPException(413, 'Archivo demasiado grande (máx 200MB)')
        s3 = boto3.client('s3', region_name=S3_REGION,
                          aws_access_key_id=AWS_KEY, aws_secret_access_key=AWS_SECRET)
        # original (auditoría) — sube desde disco, sin cargar a RAM
        master_key = 'masters/%s/%s%s' % (ctx['tenant_id'], ad_id, ext)
        s3.upload_file(src, S3_BUCKET, master_key,
                       ExtraArgs={'ContentType': file.content_type or 'application/octet-stream'})
        # extraer audio → mp3 mono 22050 (lo que fingerprintea el worker)
        mp3 = os.path.join(td, 'ref.mp3')
        try:
            subprocess.run(['ffmpeg', '-y', '-i', src, '-vn', '-ac', '1', '-ar', '22050',
                            '-b:a', '128k', mp3], check=True, capture_output=True, timeout=300)
        except FileNotFoundError:
            raise HTTPException(503, 'ffmpeg no disponible en el servidor')
        except subprocess.CalledProcessError:
            raise HTTPException(422, 'No se pudo extraer audio del archivo (¿formato inválido?)')
        ref_key = 'references/%s.mp3' % ad_id
        with open(mp3, 'rb') as fh:
            _s3_put(ref_key, fh.read(), 'audio/mpeg')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE advertisements SET audio_url = %s, updated_at = NOW() '
                        'WHERE id = %s AND campaign_id = %s AND tenant_id = %s',
                        (ref_key, ad_id, campaign_id, ctx['tenant_id']))
    return {'ok': True, 'reference_key': ref_key, 'master_key': master_key,
            'note': 'Audio extraído a references/<ad_id>.mp3; el worker lo fingerprinteará '
                    'en su próxima corrida y rellenará la duración.'}


# ── CAMPAIGN MEDIA SOURCES ────────────────────────────────────────────────────

@router.get('/{campaign_id}/media-sources')
def get_campaign_media_sources(campaign_id: str, ctx = Depends(verify_token)):
    """Estaciones que esta campaña monitorea, con conteo de detecciones."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM campaigns WHERE id = %s AND tenant_id = %s',
                        (campaign_id, ctx['tenant_id']))
            if not cur.fetchone():
                raise HTTPException(404, 'Campaña no encontrada')
            cur.execute('''
                SELECT ms.id, ms.slug, ms.name, ms.media_type,
                       ms.frequency_channel, ms.health_status, ms.lifecycle_status,
                       COUNT(fd.id) AS detecciones_en_campana
                FROM campaign_media_sources cms
                JOIN media_sources ms ON ms.id = cms.media_source_id
                LEFT JOIN fingerprint_detections fd
                    ON fd.campaign_id = %s
                   AND (fd.media_source_id = ms.id OR fd.stream_id = ms.stream_catalog_id)
                   AND fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)
                WHERE cms.campaign_id = %s
                GROUP BY ms.id
                ORDER BY ms.media_type, ms.name
            ''', (campaign_id, campaign_id))
            return [dict(r) for r in cur.fetchall()]


@router.put('/{campaign_id}/media-sources')
def set_campaign_media_sources(campaign_id: str, body: SetCampaignSourcesRequest, ctx = Depends(verify_token)):
    """Reemplaza la lista completa de estaciones de la campaña (idempotente)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM campaigns WHERE id = %s AND tenant_id = %s',
                        (campaign_id, ctx['tenant_id']))
            if not cur.fetchone():
                raise HTTPException(404, 'Campaña no encontrada')
            if body.media_source_ids:
                cur.execute('''
                    SELECT COUNT(*) AS n FROM tenant_media_source_assignments
                    WHERE tenant_id = %s AND media_source_id = ANY(%s::uuid[]) AND is_active = true
                ''', (ctx['tenant_id'], body.media_source_ids))
                if cur.fetchone()['n'] != len(body.media_source_ids):
                    raise HTTPException(400, 'Una o más estaciones no están asignadas a tu cuenta')
            cur.execute('DELETE FROM campaign_media_sources WHERE campaign_id = %s', (campaign_id,))
            if body.media_source_ids:
                cur.executemany(
                    'INSERT INTO campaign_media_sources (campaign_id, media_source_id)'
                    ' VALUES (%s, %s) ON CONFLICT DO NOTHING',
                    [(campaign_id, sid) for sid in body.media_source_ids])
    return {'ok': True, 'assigned': len(body.media_source_ids)}


