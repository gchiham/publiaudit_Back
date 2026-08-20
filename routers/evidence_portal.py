import base64
import os
from typing import Optional

import jinja2
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from weasyprint import HTML as _WeasyHTML

from core.db import get_db
from core.s3 import presign as _presign
from core.timehn import hn_iso as _hn_iso, pdf_to_hn as _pdf_to_hn
from report_ids import evd, evd_decode, report_ref
from routers.reports_common import (
    PUBLIC_BASE_URL, REPORT_METRICS,
    _make_qr, _report_kpis, _report_scope, _resolve_link, _touch,
)

# routers/ está un nivel bajo la raíz del proyecto — portal.html y templates/ viven en la raíz.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
_PORTAL_HTML_PATH = os.path.join(_PROJECT_ROOT, 'portal.html')

_jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.join(_PROJECT_ROOT, 'templates')),
    autoescape=True,
    undefined=jinja2.StrictUndefined,  # una variable faltante en el contexto revienta el render, no queda en blanco
)

router = APIRouter(tags=['evidence-portal'])

@router.get('/api/public/{token}')
def public_report_meta(token: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            report = _resolve_link(cur, token)
            cond, p = _report_scope(report)
            w = ' AND '.join(cond)

            kpis = _report_kpis(cur, w, p)

            cur.execute(f'''SELECT DISTINCT fd.stream_id AS id,
                                   COALESCE(sc.name, fd.stream_id) AS name
                            FROM fingerprint_detections fd
                            LEFT JOIN stream_catalog sc ON sc.id = fd.stream_id
                            WHERE {w} ORDER BY name''', p)
            streams = [dict(r) for r in cur.fetchall()]

            cur.execute(f'SELECT DISTINCT camp.id::text AS id, camp.name '
                        f'FROM fingerprint_detections fd '
                        f'JOIN campaigns camp ON camp.id = fd.campaign_id '
                        f'WHERE {w} ORDER BY camp.name', p)
            campaigns = [dict(r) for r in cur.fetchall()]

            cur.execute(f'SELECT DISTINCT a.id::text AS id, a.name '
                        f'FROM fingerprint_detections fd '
                        f'JOIN advertisements a ON a.id = fd.ad_id '
                        f'WHERE {w} ORDER BY a.name', p)
            ads = [dict(r) for r in cur.fetchall()]

    _touch(token)

    iso = _hn_iso
    cfg = report.get('config') or {}

    return {
        'report': {
            'title':           report['title'],
            'ref':             report_ref(report.get('created_at'), report.get('token') or token),  # folio del reporte — ver report_ids.py
            'advertiser_name': report.get('advertiser_name'),
            'advertiser_logo': report.get('advertiser_logo'),
            'client_name':     report.get('advertiser_name') or report.get('provider_name'),
            'client_logo':     report.get('advertiser_logo'),
            'provider_name':   report.get('provider_name'),
            'provider_logo':   report.get('provider_logo'),
            'campaign_name':   report.get('campaign_name'),
            'ad_name':         report.get('ad_name'),
            'visibility':      report.get('visibility', 'public'),
            'date_from':     str(report['date_from']) if report['date_from'] else None,
            'date_to':       str(report['date_to'])   if report['date_to']   else None,
            'expires_at':    iso(report.get('expires_at')),
            'generated_at':  iso(report.get('created_at')),
        },
        'config': {
            'metrics':  cfg.get('metrics', REPORT_METRICS),
            'branding': cfg.get('branding', {}),
        },
        'summary': {
            'total_detections': int(kpis['total_detections'] or 0),
            'media_sources':    int(kpis['media_sources']    or 0),
            'first_detection':  iso(kpis['first_detection']),
            'last_detection':   iso(kpis['last_detection']),
            'over_airing':      int(kpis['over_airing'] or 0),   # emisiones fuera de contrato (sobre-pauta)
        },
        'filters': {'streams': streams, 'campaigns': campaigns, 'ads': ads},
    }

@router.get('/api/public/{token}/detections')
def public_detections(
    token:       str,
    stream_id:   Optional[str] = None,
    campaign_id: Optional[str] = None,
    ad_id:       Optional[str] = None,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
    page:        int = Query(1,  ge=1),
    page_size:   int = Query(50, ge=1, le=200),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            report = _resolve_link(cur, token)
            # Report scope (always enforced) — multi-campaña + back-compat
            cond, p = _report_scope(report)
            # User-applied filters (additional narrowing)
            if stream_id:   cond.append('fd.stream_id = %s');   p.append(stream_id)
            if campaign_id: cond.append('fd.campaign_id = %s'); p.append(campaign_id)
            if ad_id:       cond.append('fd.ad_id = %s');       p.append(ad_id)
            if date_from:   cond.append("fd.air_time >= %s::date"); p.append(date_from)
            if date_to:     cond.append("fd.air_time < (%s::date + INTERVAL '1 day')"); p.append(date_to)
            w = ' AND '.join(cond)
            offset = (page - 1) * page_size

            cur.execute(f'SELECT COUNT(*) AS total FROM fingerprint_detections fd WHERE {w}', p)
            total = int(cur.fetchone()['total'])

            cur.execute(f'''
                SELECT fd.id, fd.stream_id,
                       COALESCE(sc.name, fd.stream_id) AS stream_name,
                       COALESCE(sc.type, 'radio') AS stream_type,
                       sc.city   AS stream_city,
                       a.name    AS ad_name,
                       camp.name AS campaign_name,
                       (fd.air_time AT TIME ZONE 'America/Tegucigalpa') AS air_time_hn,
                       fd.score, fd.confidence_level, fd.clip_s3_key,
                       (camp.end_date IS NOT NULL
                        AND (fd.air_time AT TIME ZONE 'America/Tegucigalpa')::date > camp.end_date)
                          AS over_airing   -- sobre-pauta: emitido después de la fecha de fin
                FROM fingerprint_detections fd
                JOIN advertisements  a    ON a.id    = fd.ad_id
                JOIN campaigns       camp ON camp.id = fd.campaign_id
                LEFT JOIN stream_catalog sc ON sc.id = fd.stream_id
                WHERE {w}
                ORDER BY fd.air_time DESC
                LIMIT %s OFFSET %s
            ''', p + [page_size, offset])

            rows = []
            for r in cur.fetchall():
                d = dict(r)
                key = d.pop('clip_s3_key', None)
                d['clip_url']    = _presign(key) if key else None
                d['clip_type']   = (('video' if key.lower().endswith(('.mp4', '.ts', '.mov', '.webm'))
                                     else 'audio') if key else None)
                d['evd']         = evd(d['id'])   # ID de evidencia estable + verificable (reversible) — ver report_ids.py
                d['air_time_hn'] = _hn_iso(d['air_time_hn'])
                rows.append(d)

    return {
        'total':   total,
        'page':    page,
        'pages':   max(1, (total + page_size - 1) // page_size),
        'results': rows,
    }

@router.get('/api/public/{token}/qr.png')
def public_report_qr(token: str):
    """QR real (mismo generador que el endpoint autenticado) apuntando a la URL
    pública del reporte. Sin login — para el footer del Evidence Portal."""
    with get_db() as conn:
        with conn.cursor() as cur:
            _resolve_link(cur, token)   # valida token activo/no expirado
    url = f'{PUBLIC_BASE_URL}/report/{token}'
    return Response(content=_make_qr(url), media_type='image/png')

@router.get('/api/public/{token}/evidence/{evd_code}')
def public_evidence(token: str, evd_code: str):
    """Verificación pública de una evidencia: EVD -> detección, SOLO si cae
    dentro del alcance del reporte (token). Sin login."""
    det_id = evd_decode(evd_code)
    if det_id is None:
        raise HTTPException(404, 'Evidencia no encontrada')
    with get_db() as conn:
        with conn.cursor() as cur:
            report = _resolve_link(cur, token)            # valida token + visibilidad/caducidad
            cond, p = _report_scope(report)
            cond.append('fd.id = %s'); p.append(det_id)   # ámbito del reporte + esta detección
            w = ' AND '.join(cond)
            cur.execute(f'''
                SELECT fd.id, COALESCE(sc.name, fd.stream_id) AS stream_name,
                       a.name AS ad_name, camp.name AS campaign_name,
                       (fd.air_time AT TIME ZONE 'America/Tegucigalpa') AS air_time_hn,
                       fd.score, fd.confidence_level, fd.clip_s3_key
                FROM fingerprint_detections fd
                JOIN advertisements  a    ON a.id    = fd.ad_id
                JOIN campaigns       camp ON camp.id = fd.campaign_id
                LEFT JOIN stream_catalog sc ON sc.id = fd.stream_id
                WHERE {w}
            ''', p)
            r = cur.fetchone()
            if not r:
                raise HTTPException(404, 'Evidencia no encontrada')
            d = dict(r)
            key = d.pop('clip_s3_key', None)
            d['evd']         = evd(d['id'])
            d['clip_url']    = _presign(key) if key else None
            d['clip_type']   = (('video' if key.lower().endswith(('.mp4', '.ts', '.mov', '.webm'))
                                 else 'audio') if key else None)
            d['air_time_hn'] = _hn_iso(d['air_time_hn'])
            return d

_PDF_MONTHS = {
    'es': ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'],
    'en': ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
}

_PDF_STR = {
    'es': {
        'kind': 'Reporte público de evidencia', 'issuedBy': 'Emitido por', 'campaign': 'Campaña',
        'period': 'Período del reporte', 'generated': 'Reporte generado', 'ref': 'Folio', 'expires': 'Expira',
        'multiCampaign': 'Varias campañas',
        'summaryTitle': 'Resumen de campaña',
        'kpiTotal': 'Detecciones totales', 'kpiSources': 'Fuentes de medios',
        'kpiFirst': 'Primera detección', 'kpiLast': 'Última detección', 'overAiring': 'fuera de período',
        'libraryTitle': 'Biblioteca de evidencia', 'airings': 'emisiones',
        'colDate': 'Fecha', 'colTime': 'Hora', 'colAd': 'Anuncio', 'colCampaign': 'Campaña', 'colEvd': 'Evidencia',
        'filteredNote': 'Este PDF refleja los filtros activos en el portal al momento de la descarga.',
        'cappedNote': 'Mostrando {shown} de {total} detecciones — refiná los filtros del portal para exportar un subconjunto más pequeño.',
        'noResults': 'No hay detecciones que coincidan con estos filtros.',
        'page': 'Página',
    },
    'en': {
        'kind': 'Public evidence report', 'issuedBy': 'Issued by', 'campaign': 'Campaign',
        'period': 'Reporting period', 'generated': 'Report generated', 'ref': 'Reference', 'expires': 'Expires',
        'multiCampaign': 'Multiple campaigns',
        'summaryTitle': 'Campaign summary',
        'kpiTotal': 'Total detections', 'kpiSources': 'Media sources',
        'kpiFirst': 'First detection', 'kpiLast': 'Last detection', 'overAiring': 'outside period',
        'libraryTitle': 'Evidence library', 'airings': 'airings',
        'colDate': 'Date', 'colTime': 'Time', 'colAd': 'Ad', 'colCampaign': 'Campaign', 'colEvd': 'Evidence',
        'filteredNote': 'This PDF reflects the filters active in the portal at the time of download.',
        'cappedNote': 'Showing {shown} of {total} detections — refine the portal filters to export a smaller subset.',
        'noResults': 'No detections match these filters.',
        'page': 'Page',
    },
}

def _pdf_fmt_date(dt, lang, with_year=True):
    if not dt:
        return '—'
    s = f'{dt.day:02d} {_PDF_MONTHS[lang][dt.month - 1]}'
    return f'{s} {dt.year}' if with_year else s

def _pdf_fmt_time(dt, lang):
    if not dt:
        return '—'
    h12 = dt.hour % 12 or 12
    ampm = ('a. m.' if dt.hour < 12 else 'p. m.') if lang == 'es' else ('AM' if dt.hour < 12 else 'PM')
    return f'{h12}:{dt.minute:02d} {ampm}'

def _pdf_fmt_range(date_from, date_to, lang):
    if not date_from and not date_to:
        return None
    a = _pdf_fmt_date(date_from, lang) if date_from else '—'
    b = _pdf_fmt_date(date_to, lang) if date_to else '—'
    return f'{a} – {b}'

@router.get('/api/public/{token}/report.pdf')
def public_report_pdf(
    token:       str,
    stream_id:   Optional[str] = None,
    campaign_id: Optional[str] = None,
    ad_id:       Optional[str] = None,
    date_from:   Optional[str] = None,
    date_to:     Optional[str] = None,
    lang:        str = 'es',
):
    """Genera un PDF real (texto vectorial, no una captura de pantalla) del reporte,
    respetando los mismos filtros que el usuario tenga activos en el portal."""
    lang = 'en' if lang == 'en' else 'es'
    L = _PDF_STR[lang]
    cap = 3000

    with get_db() as conn:
        with conn.cursor() as cur:
            report = _resolve_link(cur, token)
            cond, p = _report_scope(report)
            if stream_id:   cond.append('fd.stream_id = %s');   p.append(stream_id)
            if campaign_id: cond.append('fd.campaign_id = %s'); p.append(campaign_id)
            if ad_id:       cond.append('fd.ad_id = %s');       p.append(ad_id)
            if date_from:   cond.append("fd.air_time >= %s::date"); p.append(date_from)
            if date_to:     cond.append("fd.air_time < (%s::date + INTERVAL '1 day')"); p.append(date_to)
            w = ' AND '.join(cond)

            kpis = _report_kpis(cur, w, p)

            cur.execute(f'SELECT COUNT(*) AS total FROM fingerprint_detections fd WHERE {w}', p)
            total_matching = int(cur.fetchone()['total'])

            cur.execute(f'''
                SELECT fd.id, fd.stream_id,
                       COALESCE(sc.name, fd.stream_id) AS stream_name,
                       COALESCE(sc.type, 'radio') AS stream_type,
                       sc.city AS stream_city,
                       a.name AS ad_name,
                       camp.name AS campaign_name,
                       (fd.air_time AT TIME ZONE 'America/Tegucigalpa') AS air_time_hn,
                       fd.score, fd.confidence_level,
                       (camp.end_date IS NOT NULL
                        AND (fd.air_time AT TIME ZONE 'America/Tegucigalpa')::date > camp.end_date) AS over_airing
                FROM fingerprint_detections fd
                JOIN advertisements  a    ON a.id    = fd.ad_id
                JOIN campaigns       camp ON camp.id = fd.campaign_id
                LEFT JOIN stream_catalog sc ON sc.id = fd.stream_id
                WHERE {w}
                ORDER BY stream_name, fd.air_time ASC
                LIMIT %s
            ''', p + [cap])
            rows = [dict(r) for r in cur.fetchall()]

    for d in rows:
        d['evd'] = evd(d['id'])
    capped = total_matching > len(rows)

    # Agrupar por stream — mismo criterio que buildLibrary() en portal.html:
    # más emisiones primero, luego nombre.
    groups_map = {}
    for d in rows:
        g = groups_map.setdefault(d['stream_id'], {
            'name': d['stream_name'], 'type': d['stream_type'], 'city': d['stream_city'], 'detections': [],
        })
        g['detections'].append(d)
    groups = sorted(groups_map.values(), key=lambda g: (-len(g['detections']), g['name'] or ''))

    filters_active = any([stream_id, campaign_id, ad_id, date_from, date_to])

    logo_key = report.get('advertiser_logo')
    logo_url = _presign(logo_key) if logo_key else None
    qr_data_uri = 'data:image/png;base64,' + base64.b64encode(_make_qr(f'{PUBLIC_BASE_URL}/report/{token}')).decode()

    client_name = report.get('advertiser_name') or report.get('provider_name') or '—'
    campaign_display = report.get('title') or report.get('campaign_name') or L['multiCampaign']
    ref = report_ref(report.get('created_at'), report.get('token') or token)
    generated_hn = _pdf_to_hn(report.get('created_at'))
    expires_hn = _pdf_to_hn(report.get('expires_at'))
    period_range = _pdf_fmt_range(report.get('date_from'), report.get('date_to'), lang)

    kpi_cards = [
        (L['kpiTotal'], f"{int(kpis['total_detections'] or 0):,}"),
        (L['kpiSources'], f"{int(kpis['media_sources'] or 0):,}"),
        (L['kpiFirst'], _pdf_fmt_date(kpis['first_detection'], lang)),
        (L['kpiLast'], _pdf_fmt_date(kpis['last_detection'], lang)),
    ]
    over_n = int(kpis['over_airing'] or 0)

    meta_items = []
    if period_range: meta_items.append((L['period'], period_range))
    if generated_hn: meta_items.append((L['generated'], _pdf_fmt_date(generated_hn, lang)))
    meta_items.append((L['ref'], ref))
    if expires_hn: meta_items.append((L['expires'], _pdf_fmt_date(expires_hn, lang)))

    # Datos de presentación (fechas/horas formateadas, conteos) — el markup en sí
    # vive en templates/report_pdf.html, no aquí.
    for d in rows:
        d['date_str'] = _pdf_fmt_date(d['air_time_hn'], lang, with_year=False)
        d['time_str'] = _pdf_fmt_time(d['air_time_hn'], lang)
    for g in groups:
        g['count_fmt'] = f"{len(g['detections']):,}"

    capped_note = (
        L['cappedNote'].format(shown=f'{len(rows):,}', total=f'{total_matching:,}')
        if capped else None
    )

    html_doc = _jinja_env.get_template('report_pdf.html').render(
        lang=lang,
        L=L,
        ref=ref,
        qr_data_uri=qr_data_uri,
        logo_url=logo_url,
        client_initial=(client_name or '?')[:1].upper(),
        client_name=client_name,
        provider_name=report.get('provider_name'),
        campaign_display=campaign_display,
        ad_name=report.get('ad_name'),
        meta_items=meta_items,
        kpi_cards=kpi_cards,
        over_n_fmt=f'{over_n:,}' if over_n else None,
        filters_active=filters_active,
        capped_note=capped_note,
        groups=groups,
        token=token,
        public_base_url=PUBLIC_BASE_URL,
        generated_date=_pdf_fmt_date(generated_hn, lang) if generated_hn else None,
        page_word=L['page'],
    )

    pdf_bytes = _WeasyHTML(string=html_doc, base_url=PUBLIC_BASE_URL).write_pdf()
    _touch(token)
    return Response(
        content=pdf_bytes,
        media_type='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{ref}.pdf"'},
    )

@router.get('/report/{token}', response_class=HTMLResponse)
def evidence_portal(token: str):
    try:
        with open(_PORTAL_HTML_PATH) as fh:
            html = fh.read()
    except FileNotFoundError:
        raise HTTPException(500, 'Portal template not found')
    return HTMLResponse(content=html.replace('__TOKEN__', token))
