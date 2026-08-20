"""
PubliAudit API — FastAPI backend para el frontend PubliAudit.
Conecta a DO Managed PostgreSQL via VPC privada.

Este archivo solo arma la app (config, CORS, middleware, health) y monta los
routers de cada dominio (ver routers/). La lógica de cada área vive en su
propio router; lo compartido entre routers (DB, S3, auth de paneles internos,
hora Honduras) vive en core/.
"""
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from core.db import get_db
from routers import (
    auth, campaigns, clients, cobertura, dashboard, detections,
    evidence_portal, mediadev, reports, streams, webhooks,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('publiaudit-api')

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title='PubliAudit API',
    description='API para la plataforma PubliAudit de verificación de pauta publicitaria',
    version='1.0.0',
    docs_url='/api/docs',
    redoc_url='/api/redoc',
    openapi_url='/api/openapi.json',
)

# CORS — orígenes desde env var CORS_ORIGINS (CSV), fallback permisivo para dev local
_cors_origins_env = os.environ.get('CORS_ORIGINS', '*')
_cors_origins = [o.strip() for o in _cors_origins_env.split(',') if o.strip()] if _cors_origins_env != '*' else ['*']

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.middleware('http')
async def _debug_log_exceptions(request: Request, call_next):
    import traceback
    try:
        return await call_next(request)
    except Exception:
        log.error('Unhandled exception on %s %s\n%s', request.method, request.url.path, traceback.format_exc())
        raise


# ── HEALTH ────────────────────────────────────────────────────────────────────
@app.get('/api/health')
def health():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT 1')
        return {'status': 'ok', 'db': 'connected'}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f'DB error: {e}')


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(clients.router)
app.include_router(campaigns.router)
app.include_router(detections.router)
app.include_router(streams.router)
app.include_router(mediadev.router)
app.include_router(reports.router)
app.include_router(evidence_portal.router)
app.include_router(cobertura.router)
app.include_router(webhooks.router)
