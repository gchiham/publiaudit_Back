"""Helpers y modelos compartidos entre routers/reports.py (gestión autenticada de
links de reporte) y routers/evidence_portal.py (Evidence Portal público)."""
import io
import os
from datetime import datetime, timezone
from typing import Optional

import qrcode
import qrcode.constants
from fastapi import HTTPException
from pydantic import BaseModel

from core.db import get_db, pg_arr as _pg_arr
from core.s3 import presign as _presign
from core.timehn import hn_iso as _hn_iso

PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', 'http://159.223.104.91:8080')

def _make_qr(url: str) -> bytes:
    qr = qrcode.QRCode(version=1,
                       error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='#15181C', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()

def _resolve_link(cur, token: str) -> dict:
    cur.execute('''
        SELECT rpl.*,
               t.name     AS provider_name,  t.logo_url AS provider_logo,
               COALESCE(tcl.name, ccl.name)         AS advertiser_name,
               COALESCE(tcl.logo_url, ccl.logo_url) AS advertiser_logo,
               camp.name  AS campaign_name, camp.start_date, camp.end_date,
               a.name     AS ad_name
        FROM   report_public_links rpl
        JOIN   tenants      t    ON t.id    = rpl.tenant_id
        LEFT JOIN clients   tcl  ON tcl.id  = rpl.client_id        -- cliente destino (nuevo modelo)
        LEFT JOIN campaigns camp ON camp.id = rpl.campaign_id      -- back-compat: 1ra campaña
        LEFT JOIN clients   ccl  ON ccl.id  = camp.client_id       -- back-compat: advertiser legacy
        LEFT JOIN advertisements a ON a.id  = rpl.ad_id
        WHERE  rpl.token = %s
    ''', (token,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, 'Report not found')
    row = dict(row)
    if not row['is_active'] or row.get('visibility') == 'private':
        raise HTTPException(410, 'This report link is no longer active')
    if row['expires_at'] and row['expires_at'] < datetime.now(timezone.utc):
        raise HTTPException(410, 'This report link has expired')
    return row

def _report_scope(report: dict):
    """Construye las condiciones WHERE (sin 'AND' inicial) + params para el alcance
    de un reporte. Soporta múltiples campañas/anuncios y cae a la columna legacy
    (campaign_id/ad_id) si el reporte es anterior a la migración multi-campaña."""
    cond = ['fd.tenant_id = %s', 'fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)']
    p    = [str(report['tenant_id'])]
    camp_ids = _pg_arr(report.get('campaign_ids')) or ([str(report['campaign_id'])] if report.get('campaign_id') else [])
    ad_ids   = _pg_arr(report.get('ad_ids'))       or ([str(report['ad_id'])]       if report.get('ad_id')       else [])
    if camp_ids:
        cond.append('fd.campaign_id = ANY(%s::uuid[])'); p.append(camp_ids)
    if ad_ids:
        cond.append('fd.ad_id = ANY(%s::uuid[])');       p.append(ad_ids)
    if report.get('date_from'):
        cond.append("fd.air_time >= %s::date"); p.append(str(report['date_from']))
    if report.get('date_to'):
        cond.append("fd.air_time < (%s::date + INTERVAL '1 day')"); p.append(str(report['date_to']))
    return cond, p

def _report_kpis(cur, w: str, p: list) -> dict:
    """KPIs agregados (total, fuentes, primera/última detección, sobre-pauta)
    para el WHERE ya armado por _report_scope (+ filtros opcionales)."""
    cur.execute(f'''
        SELECT COUNT(*)                          AS total_detections,
               COUNT(DISTINCT fd.stream_id)      AS media_sources,
               MIN(fd.air_time AT TIME ZONE 'America/Tegucigalpa') AS first_detection,
               MAX(fd.air_time AT TIME ZONE 'America/Tegucigalpa') AS last_detection,
               COUNT(*) FILTER (
                   WHERE camp.end_date IS NOT NULL
                   AND (fd.air_time AT TIME ZONE 'America/Tegucigalpa')::date > camp.end_date
               )                                 AS over_airing
        FROM fingerprint_detections fd
        LEFT JOIN campaigns camp ON camp.id = fd.campaign_id
        WHERE {w}
    ''', p)
    return dict(cur.fetchone())

def _touch(token: str):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'UPDATE report_public_links '
                    'SET access_count = access_count+1, last_accessed_at = NOW() '
                    'WHERE token = %s', (token,))
    except Exception:
        pass

# Métricas/secciones que un reporte puede incluir (checklist del constructor).
REPORT_METRICS = [
    'total_emissions',    # KPI total de emisiones detectadas
    'by_medium',          # donut Radio/TV/Streaming/Podcast
    'timeline',           # emisiones por día / línea de tiempo
    'detections_detail',  # lista de detecciones con evidencia (clip)
    'by_ad',              # desglose por anuncio
    'by_campaign',        # desglose por campaña
    'by_daypart',         # franja horaria (mañana/tarde/prime/noche, GMT-6) — agregado en el front sobre air_time_hn
    'by_source',          # participación por fuente/emisora (% y conteo) — agregado en el front sobre stream_name
    'period_covered',     # primera / última detección
]

class CreateReportLinkRequest(BaseModel):
    title:           str
    client_id:       Optional[str]       = None    # cliente destino (marca blanca)
    campaign_ids:    list[str]           = []      # alcance: múltiples campañas
    ad_ids:          list[str]           = []      # filtro opcional por anuncios
    date_from:       Optional[str]       = None
    date_to:         Optional[str]       = None
    expires_in_days: Optional[int]       = None    # caducidad opcional
    visibility:      str                 = 'public' # 'public' | 'private'
    metrics:         Optional[list[str]] = None     # None => todas
    branding:        Optional[dict]      = None     # overrides de marca blanca
    # back-compat: clientes viejos que mandan una sola campaña/anuncio
    campaign_id:     Optional[str]       = None
    ad_id:           Optional[str]       = None

class UpdateReportLinkRequest(BaseModel):
    title:           Optional[str]       = None
    client_id:       Optional[str]       = None
    campaign_ids:    Optional[list[str]] = None
    ad_ids:          Optional[list[str]] = None
    date_from:       Optional[str]       = None
    date_to:         Optional[str]       = None
    expires_in_days: Optional[int]       = None    # setea/extiende caducidad desde ahora
    clear_expiry:    Optional[bool]      = None    # quita la caducidad
    visibility:      Optional[str]       = None
    metrics:         Optional[list[str]] = None
    branding:        Optional[dict]      = None
    is_active:       Optional[bool]      = None

class ToggleLinkRequest(BaseModel):
    is_active: bool
