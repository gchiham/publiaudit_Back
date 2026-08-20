# media-app — Backend de PubliAudit

Backend **FastAPI** del producto **PubliAudit** (plataforma de monitoreo de medios de AdSignal):
la API autenticada que consume el frontend + el **Evidence Portal** público (reportes con QR,
reproducción de audio/video de evidencia) + el panel interno de **Cobertura** (monitoreo de las
señales/streams que capturan los "destroyers").

Corre en el nodo **mediaAPP** (`137.184.53.234`) del ecosistema MediaDEV. Comparte la DB managed
PostgreSQL (`destroyer_db`) y el bucket S3 con el nodo de captura **mediaCAP**.
Ver [`MediaDEV-Honduras`](https://github.com/gchiham/MediaDEV-Honduras) (`live_mediaDEV.md`) para el ecosistema completo.

## Estructura

| Archivo / carpeta | Qué es |
|---|---|
| `main.py` | La API completa (64 endpoints): auth, dashboard, campañas, anunciantes, detecciones, reportes, evidence portal, panel de cobertura |
| `auth/clerk.py` | Verificación de JWT de Clerk (RS256, multi-issuer) — el sistema de auth actual |
| `mail.py` | Envío de emails vía Amazon SES (SMTP) |
| `report_ids.py` | Codifica/decodifica IDs presentables de evidencias (`EVD-XXXXXXX`) |
| `portal.html` | Frontend del evidence portal público (lo que ve el anunciante al escanear el QR) |
| `cobertura.html` + `cobertura_static/*.js` | Panel interno de cobertura de señales (streams, gateways, costos, destroyers) |
| `web/` | Build estático del frontend principal (Vite/React) — se sirve directo por nginx, no por FastAPI |
| `setup_admin.py` | Utilidad de línea de comandos para crear/resetear el usuario admin |
| `auto_baja.py` | Script batch: da de baja campañas vencidas tras el período de gracia |
| `mediadev_logs.py` / `mediadev_metrics.py` | Agentes que reportan logs/métricas del nodo al sistema de monitoreo de MediaDEV |
| `migration_stream_plans.sql`, `migrations/*.sql` | Migraciones SQL sueltas (no hay herramienta de migraciones tipo Alembic — se aplican a mano) |
| `docs/PUBLIC_EVIDENCE_PORTAL.md` | Documentación del flujo del evidence portal |
| `FRONTEND_DEV_GUIDE.md` | Guía de endpoints/tipos para el dev de frontend |
| `deploy/app.publiaudit.com.conf` | Config de nginx para servir `web/` (referencia, se despliega a mano en el droplet) |
| `run.sh` | Arranque local contra la DB local (`dbdestroyer`) — **no versionar con secretos dentro** |

## Stack

- **Python 3.14**, FastAPI, Uvicorn
- **PostgreSQL** vía `psycopg2` puro (sin ORM) con pool de conexiones propio (`ThreadedConnectionPool` + semáforo en `main.py`)
- **Auth:** Clerk (JWT RS256, multi-issuer). El login con JWT propio (`/api/auth/login`) se mantiene solo por compatibilidad
- **S3** (boto3) para clips de audio/video y logos/masters
- **WeasyPrint** para generar PDFs de reportes; **qrcode** para los QR de los links públicos
- **SES (SMTP)** para envío de emails de reportes

## Áreas de la API (64 endpoints en `main.py`)

- **Auth** (`/api/auth/*`) — sesión actual vía Clerk; login JWT legacy mantenido por compatibilidad
- **Dashboard** (`/api/dashboard/*`) — KPIs, airings por medio
- **Clientes y campañas** (`/api/clients`, `/api/campaigns/*`) — CRUD, jerarquía tenant → client → campaign → ad, upload de logo/master
- **Detecciones** (`/api/detections/*`, `/api/timeline`, `/api/comprobante/*`) — lista, detalle, timeline, comprobantes, clips
- **Streams / medios** (`/api/streams`, `/api/my-streams`, `/api/media-sources*`) — fuentes de captura por campaña
- **Reportes + Evidence Portal** (`/api/reports/*`, `/api/public/{token}/*`, `/report/{token}`) — links públicos con token + QR, PDF, evidencias con clips presigned de S3
- **Panel de Cobertura** (`/api/cobertura/*`, `/cobertura`) — estado de streams, gateways, uptime, runs y costos de los destroyers
- **MediaDEV** (`/api/mediadev/*`) — resumen del ecosistema (streams, estaciones, detecciones agregadas)
- **Webhooks** (`/api/webhooks/clerk`) — sincroniza la tabla `users` con eventos de Clerk
- **Salud** (`/api/health`)

## Instalación

### Dependencias del sistema (requeridas por WeasyPrint para generar PDFs)

```bash
sudo apt install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
                  libcairo2 libffi-dev shared-mime-info fonts-dejavu-core
```

### Entorno Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Correr

### Local (contra DB local)

```bash
./run.sh
```

> `run.sh` no está en el repo por defecto — cada dev arma el suyo localmente con sus propias
> credenciales de desarrollo. **Nunca** le agregues credenciales reales (AWS, Clerk) ni lo
> subas a git — usa siempre valores `sk_test_...` / claves de una cuenta AWS de desarrollo.

### Producción

```bash
# Credenciales en /etc/media-app.env (chmod 600), NO en el código.
set -a && source /etc/media-app.env && set +a
/opt/media-app/venv/bin/uvicorn main:app --host 127.0.0.1 --port 9001 --workers 2
```

Corre como servicio systemd `media-app` detrás de nginx (`:8080`, locations `/api/` y `/report/`).
El build de `web/` se sirve directo por nginx (ver `deploy/app.publiaudit.com.conf`).

## Variables de entorno

### Base de datos
```
PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASS, PG_SSLMODE
PG_POOL_MIN, PG_POOL_MAX        # tamaño del pool de conexiones (default 1 / 10)
```

### Auth (Clerk)
```
CLERK_ISSUERS                   # CSV de issuers aceptados (default: prod + dev)
CLERK_SECRET_KEY                # Backend API de Clerk (lookup de email en primer login)
CLERK_WEBHOOK_SECRET            # valida el webhook /api/webhooks/clerk
JWT_SECRET, JWT_EXP_HOURS       # legacy — solo para /api/auth/login (compatibilidad)
```

### AWS / S3 / Email
```
AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
S3_BUCKET, S3_REGION
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
EMAIL_FROM, EMAIL_FROM_NAME
```

### Otras
```
PUBLIC_BASE_URL                 # base de las URLs públicas del evidence portal
CORS_ORIGINS                    # orígenes permitidos, CSV
COBERTURA_TOKEN                 # token de acceso al panel /cobertura
CW_SERVER_NAME                  # nombre del nodo reportado por mediadev_logs.py / mediadev_metrics.py
DESTROYER_HOURLY_RATE           # costo/hora usado en /api/cobertura/costs/*
CAMPAIGN_GRACE_DAYS             # días de gracia antes de dar de baja campañas vencidas (auto_baja.py)
EVD_SALT                        # sal para codificar IDs de evidencia (report_ids.py)
```

> **Seguridad:** ningún secreto va hardcodeado en el código — todo se lee de env vars.
> `.env`, `*.pem`, `*.key` y los backups están en `.gitignore`. Si armas un `run.sh` local,
> agrégalo también a tu `.gitignore` si va a contener claves reales.

## Estado actual / limitaciones conocidas

- Sin tests automatizados y sin CI/CD — los cambios se validan manualmente antes de desplegar.
- `main.py` concentra los 64 endpoints en un solo archivo; no hay separación en routers todavía.
- No hay herramienta de migraciones (Alembic u otra) — los `.sql` en `migrations/` y en la raíz se aplican a mano.
- El frontend está repartido en tres enfoques distintos: `web/` (build de Vite/React), `portal.html` (HTML servido por FastAPI) y `cobertura_static/*.js` (JS plano) — no están unificados.
