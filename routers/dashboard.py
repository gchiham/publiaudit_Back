from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from auth.clerk import verify_clerk_token as verify_token
from core.db import get_db

router = APIRouter(prefix='/api', tags=['dashboard'])

# ── DASHBOARD ─────────────────────────────────────────────────────────────────
@router.get('/dashboard/kpis')
def dashboard_kpis(ctx = Depends(verify_token)):
    tenant_id = ctx['tenant_id']
    with get_db() as conn:
        with conn.cursor() as cur:
            # KPIs principales
            cur.execute('''
                SELECT
                    COUNT(*)                                                as total_airings,
                    COUNT(DISTINCT stream_id)                               as medios_activos,
                    COUNT(DISTINCT DATE(air_time AT TIME ZONE 'America/Tegucigalpa')) as dias_con_datos,
                    ROUND(AVG(score)::numeric, 0)                          as score_promedio,
                    COUNT(CASE WHEN confidence_level = 'very_high' THEN 1 END) as muy_alta,
                    COUNT(CASE WHEN confidence_level = 'high'      THEN 1 END) as alta,
                    COUNT(CASE WHEN confidence_level = 'medium'    THEN 1 END) as moderada,
                    COUNT(CASE WHEN confidence_level = 'low'       THEN 1 END) as baja
                FROM fingerprint_detections
                WHERE tenant_id = %s AND deleted_at IS NULL
                  AND (confidence_score IS NULL OR confidence_score >= 0.7)
            ''', (tenant_id,))
            kpis = dict(cur.fetchone())

            # Airings últimos 7 días por día
            cur.execute('''
                SELECT
                    DATE(air_time AT TIME ZONE 'America/Tegucigalpa') as fecha,
                    COUNT(*) as airings
                FROM fingerprint_detections
                WHERE tenant_id = %s
                  AND air_time >= NOW() - INTERVAL '7 days'
                  AND deleted_at IS NULL
                  AND (confidence_score IS NULL OR confidence_score >= 0.7)
                GROUP BY 1 ORDER BY 1
            ''', (tenant_id,))
            kpis['serie_7d'] = [dict(r) for r in cur.fetchall()]

            # Último run del Destroyer
            cur.execute('''
                SELECT status, files_done, total_files, total_detections,
                       t2_started, cost_usd
                FROM destroyer_runs
                ORDER BY t2_started DESC LIMIT 1
            ''')
            run = cur.fetchone()
            kpis['ultimo_analisis'] = dict(run) if run else None

            # Campañas activas
            cur.execute('''
                SELECT COUNT(*) as total FROM campaigns
                WHERE tenant_id = %s AND status = 'active'
            ''', (tenant_id,))
            kpis['campanas_activas'] = cur.fetchone()['total']

    return kpis

@router.get('/dashboard/airings-by-medium')
def airings_by_medium(
    days: int = Query(30, ge=1, le=90),
    ctx = Depends(verify_token)
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT stream_id as medio, COUNT(*) as airings,
                       ROUND(AVG(score)::numeric, 0) as score_prom
                FROM fingerprint_detections
                WHERE tenant_id = %s
                  AND air_time >= NOW() - INTERVAL '%s days'
                  AND deleted_at IS NULL
                  AND (confidence_score IS NULL OR confidence_score >= 0.7)
                GROUP BY stream_id ORDER BY airings DESC
            ''', (ctx['tenant_id'], days))
            return [dict(r) for r in cur.fetchall()]

# ── PLAN ──────────────────────────────────────────────────────────────────────
@router.get('/plan')
def get_plan(ctx = Depends(verify_token)):
    """Plan activo del cliente: límite de streams, uso actual, slots disponibles."""
    tenant_id = ctx['tenant_id']
    if not tenant_id:
        return {
            'id': None, 'name': 'free', 'display_name': 'Free',
            'max_streams': 0, 'price_monthly': 0,
            'streams_activos': 0, 'streams_disponibles': 0, 'ilimitado': False,
        }
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT p.id, p.name, p.display_name, p.max_streams, p.price_monthly
                FROM tenants c
                JOIN plans p ON p.id = c.plan_id
                WHERE c.id = %s
            ''', (tenant_id,))
            plan = cur.fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail='Plan no configurado')

            cur.execute(
                'SELECT COUNT(*) as streams_activos FROM client_streams WHERE tenant_id = %s',
                (tenant_id,)
            )
            usage = cur.fetchone()['streams_activos']

    plan = dict(plan)
    unlimited = plan['max_streams'] == -1
    plan['streams_activos']    = usage
    plan['streams_disponibles'] = None if unlimited else max(0, plan['max_streams'] - usage)
    plan['ilimitado']           = unlimited
    return plan


