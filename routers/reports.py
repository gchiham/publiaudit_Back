import base64
import secrets
from datetime import datetime, timedelta, timezone

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Response

from auth.clerk import verify_clerk_token as verify_token
from core.db import get_db, pg_arr as _pg_arr
from report_ids import report_ref
from routers.reports_common import (
    PUBLIC_BASE_URL, REPORT_METRICS,
    CreateReportLinkRequest, UpdateReportLinkRequest,
    _make_qr,
)

router = APIRouter(prefix='/api/reports', tags=['reports'])

@router.post('', status_code=201)
def create_report_link(body: CreateReportLinkRequest, ctx = Depends(verify_token)):
    tenant_id = ctx['tenant_id']
    camp_ids = [c for c in (body.campaign_ids or []) if c] or ([body.campaign_id] if body.campaign_id else [])
    ad_ids   = [a for a in (body.ad_ids or []) if a]       or ([body.ad_id]       if body.ad_id       else [])
    if not camp_ids:
        raise HTTPException(400, 'Debe seleccionar al menos una campaña')
    visibility = 'private' if body.visibility == 'private' else 'public'
    metrics = body.metrics if body.metrics is not None else REPORT_METRICS
    metrics = [m for m in metrics if m in REPORT_METRICS] or REPORT_METRICS
    config  = {'metrics': metrics, 'branding': body.branding or {}}

    with get_db() as conn:
        with conn.cursor() as cur:
            # Validar campañas del tenant y resolver el cliente destino
            cur.execute('SELECT id, client_id FROM campaigns '
                        'WHERE id = ANY(%s::uuid[]) AND tenant_id = %s',
                        (camp_ids, tenant_id))
            found = cur.fetchall()
            if len(found) != len(set(camp_ids)):
                raise HTTPException(404, 'Una o más campañas no encontradas')
            client_id = body.client_id
            if not client_id:
                client_ids = {str(r['client_id']) for r in found if r['client_id']}
                client_id = client_ids.pop() if len(client_ids) == 1 else None
            if ad_ids:
                cur.execute('SELECT COUNT(*) AS n FROM advertisements '
                            'WHERE id = ANY(%s::uuid[]) AND tenant_id = %s',
                            (ad_ids, tenant_id))
                if int(cur.fetchone()['n']) != len(set(ad_ids)):
                    raise HTTPException(404, 'Uno o más anuncios no encontrados')

            token = secrets.token_hex(8)
            for _ in range(5):
                cur.execute('SELECT 1 FROM report_public_links WHERE token = %s', (token,))
                if not cur.fetchone():
                    break
                token = secrets.token_hex(8)
            exp   = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
                     if body.expires_in_days else None)
            cur.execute('''
                INSERT INTO report_public_links
                    (token, tenant_id, client_id, campaign_id, ad_id,
                     campaign_ids, ad_ids, title, date_from, date_to,
                     expires_at, visibility, config, is_active, created_by)
                VALUES (%s,%s,%s,%s,%s,%s::uuid[],%s::uuid[],%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id, token, created_at
            ''', (token, tenant_id, client_id, camp_ids[0], (ad_ids[0] if ad_ids else None),
                  camp_ids, ad_ids, body.title, body.date_from or None, body.date_to or None,
                  exp, visibility, psycopg2.extras.Json(config),
                  (visibility == 'public'), ctx['sub']))
            result = dict(cur.fetchone())
    url    = f'{PUBLIC_BASE_URL}/report/{token}'
    qr_png = _make_qr(url)
    return {
        'id':         str(result['id']),
        'token':      token,
        'ref':        report_ref(result.get('created_at'), token),  # folio legible PA-AAAA-XXXXXX (etiqueta, no llave) — ver report_ids.py
        'url':        url,
        'visibility': visibility,
        'config':     config,
        'qr_base64':  base64.b64encode(qr_png).decode(),
        'created_at': result['created_at'].isoformat(),
    }

@router.get('')
def list_report_links(ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT rpl.id, rpl.token, rpl.title, rpl.date_from, rpl.date_to,
                       rpl.expires_at, rpl.is_active, rpl.visibility, rpl.config,
                       rpl.access_count, rpl.last_accessed_at, rpl.created_at,
                       rpl.campaign_ids, rpl.ad_ids, rpl.client_id,
                       cl.name AS client_name, cl.logo_url AS client_logo,
                       COALESCE(array_length(rpl.campaign_ids, 1), 0) AS campaign_count,
                       camp.name AS campaign_name, a.name AS ad_name
                FROM   report_public_links rpl
                LEFT JOIN clients       cl   ON cl.id   = rpl.client_id
                LEFT JOIN campaigns     camp ON camp.id = rpl.campaign_id
                LEFT JOIN advertisements a   ON a.id    = rpl.ad_id
                WHERE  rpl.tenant_id = %s
                ORDER BY rpl.created_at DESC
            ''', (ctx['tenant_id'],))
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                d['campaign_ids'] = _pg_arr(d.get('campaign_ids'))
                d['ad_ids']       = _pg_arr(d.get('ad_ids'))
                d['url'] = f"{PUBLIC_BASE_URL}/report/{d['token']}"
                rows.append(d)
    return rows

@router.patch('/{token}')
def update_report_link(token: str, body: UpdateReportLinkRequest, ctx = Depends(verify_token)):
    """Edita un reporte: alcance (campañas/anuncios), fechas, métricas, marca blanca,
    caducidad y visibilidad. Acepta {is_active} solo para back-compat (toggle)."""
    sets, params = [], []
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT config FROM report_public_links '
                        'WHERE token = %s AND tenant_id = %s', (token, ctx['tenant_id']))
            existing = cur.fetchone()
            if not existing:
                raise HTTPException(404, 'Report link not found')
            config = dict(existing['config'] or {})

            if body.title is not None:
                sets.append('title = %s'); params.append(body.title)
            if body.client_id is not None:
                sets.append('client_id = %s'); params.append(body.client_id or None)
            if body.campaign_ids is not None:
                cids = [c for c in body.campaign_ids if c]
                if not cids:
                    raise HTTPException(400, 'Debe seleccionar al menos una campaña')
                cur.execute('SELECT COUNT(*) AS n FROM campaigns '
                            'WHERE id = ANY(%s::uuid[]) AND tenant_id = %s',
                            (cids, ctx['tenant_id']))
                if int(cur.fetchone()['n']) != len(set(cids)):
                    raise HTTPException(404, 'Una o más campañas no encontradas')
                sets.append('campaign_ids = %s::uuid[]'); params.append(cids)
                sets.append('campaign_id = %s');          params.append(cids[0])
            if body.ad_ids is not None:
                aids = [a for a in body.ad_ids if a]
                if aids:
                    cur.execute('SELECT COUNT(*) AS n FROM advertisements '
                                'WHERE id = ANY(%s::uuid[]) AND tenant_id = %s',
                                (aids, ctx['tenant_id']))
                    if int(cur.fetchone()['n']) != len(set(aids)):
                        raise HTTPException(404, 'Uno o más anuncios no encontrados')
                sets.append('ad_ids = %s::uuid[]'); params.append(aids)
                sets.append('ad_id = %s');          params.append(aids[0] if aids else None)
            if body.date_from is not None:
                sets.append('date_from = %s'); params.append(body.date_from or None)
            if body.date_to is not None:
                sets.append('date_to = %s');   params.append(body.date_to or None)
            if body.clear_expiry:
                sets.append('expires_at = NULL')
            elif body.expires_in_days is not None:
                sets.append('expires_at = %s')
                params.append(datetime.now(timezone.utc) + timedelta(days=body.expires_in_days))
            if body.metrics is not None:
                config['metrics'] = [m for m in body.metrics if m in REPORT_METRICS] or REPORT_METRICS
            if body.branding is not None:
                config['branding'] = body.branding
            if body.metrics is not None or body.branding is not None:
                sets.append('config = %s'); params.append(psycopg2.extras.Json(config))
            if body.visibility is not None:
                vis = 'private' if body.visibility == 'private' else 'public'
                sets.append('visibility = %s'); params.append(vis)
                sets.append('is_active = %s');  params.append(vis == 'public')
            elif body.is_active is not None:   # back-compat toggle
                sets.append('is_active = %s'); params.append(body.is_active)

            if not sets:
                raise HTTPException(400, 'Nada para actualizar')
            params += [token, ctx['tenant_id']]
            cur.execute(f'UPDATE report_public_links SET {", ".join(sets)} '
                        f'WHERE token = %s AND tenant_id = %s', params)
    return {'ok': True, 'token': token}

@router.delete('/{token}')
def delete_report_link(token: str, ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'DELETE FROM report_public_links WHERE token = %s AND tenant_id = %s',
                (token, ctx['tenant_id']))
            if cur.rowcount == 0:
                raise HTTPException(404, 'Report link not found')
    return {'ok': True}

@router.get('/{token}/qr.png')
def report_qr_png(token: str, ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT id FROM report_public_links WHERE token = %s AND tenant_id = %s',
                (token, ctx['tenant_id']))
            if not cur.fetchone():
                raise HTTPException(404, 'Report link not found')
    url = f'{PUBLIC_BASE_URL}/report/{token}'
    return Response(content=_make_qr(url), media_type='image/png',
                    headers={'Content-Disposition': f'inline; filename="qr_{token}.png"'})

