# PubliAudit — Guía para el Dev de Frontend

> Estado: Junio 2026. API lista. Frontend por construir.

---

## 1. Qué es PubliAudit

SaaS de auditoría de publicidad en medios hondureños. Los clientes (agencias, anunciantes, radios) suben sus spots de audio/video, el sistema los detecta automáticamente en 16 señales de radio y TV que se graban 24/7, y expone un dashboard de evidencias con timeline, comprobantes descargables y links públicos para compartir con clientes finales.

**Flujo resumido:**
```
Tenant sube spot → Destroyer analiza grabaciones → Detecciones en DB → Frontend las muestra
```

---

## 2. Repos Git

| Repo | Qué contiene | Servidor |
|------|-------------|---------|
| `gchiham/MediaDEV-Honduras` (público) | Captura 24/7 (daemon, scripts ffmpeg, config) | mediaCAP `159.223.104.91` |
| `gchiham/media-app` (privado) | **Backend FastAPI** + portal de evidencia | mediaAPP `137.184.53.234` |
| `gchiham/destroyer` (privado) | Motor de detección de anuncios (AWS) | — |
| `gchiham/mediadev-infra` (privado) | Config operativa (nginx, systemd, wireguard) | ambos nodos |

**El frontend es un proyecto nuevo** — no existe todavía en ningún repo. El único archivo que te importa ahora es `gchiham/media-app/main.py` (la API).

---

## 3. API

### Base URL
```
http://137.184.53.234:8080
```
> Aún no hay dominio configurado. Cuando lo haya, se actualiza aquí.

### Docs interactivas (Swagger)
```
http://137.184.53.234:8080/api/docs
```

### Autenticación
JWT Bearer token. Todas las rutas (salvo `/api/auth/login` y las públicas `/api/public/*`) requieren:
```
Authorization: Bearer <token>
```
El token expira en **24 horas**. No hay refresh token — cuando expire, el usuario vuelve a hacer login.

---

## 4. Endpoints por módulo

### 4.1 Auth

#### `POST /api/auth/login`
```json
// Request
{ "email": "user@example.com", "password": "..." }

// Response 200
{
  "access_token": "eyJ...",
  "token_type":   "bearer",
  "role":         "admin"   // "admin" | "viewer"
}
```

#### `GET /api/auth/me`
```json
// Response 200
{
  "id":           "uuid",
  "email":        "user@example.com",
  "role":         "admin",
  "last_login":   "2026-06-17T14:00:00+00:00",
  "client_name":  "Agencia XYZ",
  "client_slug":  "agencia-xyz",
  "tier":         "professional"
}
```

#### `POST /api/auth/change-password`
```json
// Request
{ "current_password": "...", "new_password": "..." }
// Response 200
{ "ok": true }
```

---

### 4.2 Dashboard

#### `GET /api/dashboard/kpis`
```json
// Response 200
{
  "total_airings":    1247,
  "medios_activos":   12,
  "dias_con_datos":   30,
  "score_promedio":   84,
  "muy_alta":         820,
  "alta":             310,
  "moderada":         97,
  "baja":             20,
  "serie_7d": [
    { "fecha": "2026-06-10", "airings": 43 }
  ],
  "ultimo_analisis": {
    "status":            "completed",
    "files_done":        48,
    "total_files":       48,
    "total_detections":  127,
    "t2_started":        "2026-06-17T06:00:00+00:00",
    "cost_usd":          0.12
  },
  "campanas_activas": 3
}
```

#### `GET /api/dashboard/airings-by-medium?days=30`
```json
// Response 200
[
  { "medio": "xy_hrn", "airings": 312, "score_prom": 87 }
]
```
Parámetros: `days` (1–90, default 30).

---

### 4.3 Clientes (anunciantes)

Un **Tenant** puede tener múltiples **Clientes** (anunciantes). Cada cliente tiene sus propias campañas.

#### `GET /api/clients?active=true`
```json
[{
  "id":               "uuid",
  "name":             "Coca-Cola HN",
  "industry":         "Bebidas",
  "country":          "HN",
  "logo_url":         null,
  "active":           true,
  "created_at":       "2026-05-01T00:00:00+00:00",
  "total_campaigns":  2
}]
```

#### `POST /api/clients`
```json
// Request
{ "name": "Coca-Cola HN", "industry": "Bebidas", "country": "HN", "logo_url": null }
// Response 201 — mismo shape que GET individual
```

#### `GET /api/clients/{client_id}`
Igual que el item del listado pero con `total_airings` adicional.

#### `PATCH /api/clients/{client_id}`
Campos opcionales: `name`, `industry`, `country`, `logo_url`, `active`.

#### `DELETE /api/clients/{client_id}`
Falla con 409 si el cliente tiene campañas activas.

---

### 4.4 Campañas

#### `GET /api/campaigns?status=active&client_id=uuid`
```json
[{
  "id":             "uuid",
  "name":           "Salud SESAL 2026",
  "description":    "Campaña de vacunación",
  "status":         "active",
  "campaign_type":  "scheduled",
  "start_date":     "2026-06-01",
  "end_date":       "2026-08-31",
  "created_at":     "2026-05-15T00:00:00+00:00",
  "client_id":      "uuid",
  "client_name":    "SESAL",
  "total_ads":      2,
  "total_airings":  418
}]
```
Filtros: `status` (`active`|`paused`|`archived`), `client_id`.

#### `POST /api/campaigns`
```json
// Request
{
  "client_id":     "uuid",
  "name":          "Campaña Q3",
  "description":   "opcional",
  "campaign_type": "scheduled",   // "scheduled" | "ongoing"
  "start_date":    "2026-07-01",  // opcional
  "end_date":      "2026-09-30",  // REQUERIDO si scheduled, NULO si ongoing
  "budget_usd":    5000
}
// Response 201
{ "id": "uuid", "name": "...", "status": "active", "campaign_type": "scheduled", ... }
```
> ⚠️ Regla de negocio: `scheduled` requiere `end_date`; `ongoing` no puede tenerlo.

#### `GET /api/campaigns/{id}`
Igual que el listado pero con `score_promedio` adicional.

#### `PATCH /api/campaigns/{id}`
Campos opcionales: `client_id`, `name`, `description`, `status`, `campaign_type`, `start_date`, `end_date`, `budget_usd`.

#### `DELETE /api/campaigns/{id}`
Soft-delete: pone `status = 'archived'`. Las detecciones no se borran.

---

### 4.5 Anuncios (Ads)

#### `GET /api/campaigns/{campaign_id}/ads`
```json
[{
  "id":               "uuid",
  "name":             "Vacunacion_Amp_v2",
  "description":      null,
  "status":           "active",
  "duration_seconds": 30,
  "match_min":        0.75,
  "clip_pad_seconds": 5,
  "total_airings":    208,
  "ultimo_airing":    "2026-06-17T08:15:00-06:00"
}]
```

#### `POST /api/campaigns/{campaign_id}/ads`
```json
// Request
{
  "name":             "Spot Radio 30s",
  "description":      "opcional",
  "duration_seconds": 30,
  "match_min":        0.75,   // optional, umbral de coincidencia
  "clip_pad_seconds": 5       // segundos de padding en clip de evidencia
}
// Response 201 — mismo shape que item del listado
```

#### `PATCH /api/campaigns/{campaign_id}/ads/{ad_id}`
Campos opcionales: `name`, `description`, `duration_seconds`, `match_min`, `clip_pad_seconds`, `status`.

#### `DELETE /api/campaigns/{campaign_id}/ads/{ad_id}`
Soft-delete por `deleted_at`.

---

### 4.6 Estaciones por Campaña

Qué señales monitorea cada campaña (el Destroyer las escanea).

#### `GET /api/campaigns/{campaign_id}/media-sources`
```json
[{
  "id":                      "uuid",
  "slug":                    "xy_hrn",
  "name":                    "XY HRN",
  "media_type":              "radio",
  "frequency_channel":       null,
  "health_status":           "healthy",
  "lifecycle_status":        "active",
  "detecciones_en_campana":  312
}]
```

#### `PUT /api/campaigns/{campaign_id}/media-sources`
Reemplaza la lista completa (idempotente):
```json
// Request
{ "media_source_ids": ["uuid1", "uuid2", "uuid3"] }
// Response 200
{ "ok": true, "assigned": 3 }
```
> Las IDs deben pertenecer a estaciones asignadas al tenant.

---

### 4.7 Detecciones

#### `GET /api/detections`
Parámetros opcionales:
- `campaign_id`, `ad_id`, `stream_id` — filtros
- `confidence` — `very_high` | `high` | `medium` | `low`
- `date_from`, `date_to` — formato `YYYY-MM-DD`
- `page` (default 1), `page_size` (default 50, max 200)

```json
// Response 200
{
  "total":   1247,
  "page":    1,
  "pages":   25,
  "results": [{
    "id":              12345,
    "stream_id":       "xy_hrn",
    "ad_name":         "Vacunacion_Amp_v2",
    "campaign_name":   "Salud SESAL 2026",
    "air_time_hn":     "2026-06-17T08:15:00-06:00",
    "ts_label":        "2026-06-17T14Z",
    "score":           91,
    "confidence_level":"very_high",
    "clip_s3_key":     "detections/12345.mp3",
    "algorithm":       "fingerprint_v2",
    "created_at":      "2026-06-17T14:16:03+00:00"
  }]
}
```

#### `GET /api/detections/{id}`
Retorna todos los campos de `fingerprint_detections` + `ad_name`, `duration_seconds`, `campaign_name`, `air_time_hn`.

#### `GET /api/detections/{id}/clip`
URL pre-firmada S3 (válida 5 minutos) para reproducir el clip de audio/video:
```json
{ "clip_url": "https://s3.amazonaws.com/...?X-Amz-...", "expires_in": 300 }
```

---

### 4.8 Timeline (vista Gantt)

#### `GET /api/timeline?campaign_id=uuid&date_from=2026-06-01&date_to=2026-06-17`
```json
{
  "xy_hrn": {
    "nombre": "XY HRN",
    "tipo":   "radio",
    "airings": [
      {
        "ad_name":         "Vacunacion_Amp_v2",
        "air_time_hn":     "2026-06-17T08:15:00-06:00",
        "score":           91,
        "confidence_level":"very_high"
      }
    ]
  }
}
```

---

### 4.9 Comprobante de pauta

Vista de evidencia para un anuncio específico.

#### `GET /api/comprobante/{ad_id}?date_from=2026-06-01&date_to=2026-06-17`
```json
{
  "ad": { "id": "uuid", "name": "...", "duration_seconds": 30 },
  "airings": [...],
  "total":   208,
  "periodo": { "desde": "2026-06-01", "hasta": "2026-06-17" }
}
```

---

### 4.10 Estaciones de Medios (Media Sources)

#### `GET /api/media-sources`
Estaciones activas asignadas al tenant. Usar para selectores y filtros.
```json
[{
  "id":              "uuid",
  "slug":            "xy_hrn",
  "name":            "XY HRN",
  "media_type":      "radio",     // "radio" | "tv"
  "country":         "HN",
  "frequency_channel": null,
  "description":     null,
  "logo_url":        null,
  "health_status":   "healthy",   // "healthy" | "degraded" | "offline"
  "lifecycle_status":"active",
  "assigned_at":     "2026-06-17T00:00:00+00:00",
  "total_detecciones": 1247
}]
```

#### `POST /api/media-sources/request`
El tenant solicita agregar una nueva señal:
```json
// Request
{
  "signal_name":       "Radio Uno HN",
  "media_type":        "radio",
  "country":           "HN",
  "city":              "Tegucigalpa",
  "frequency_channel": "90.9 FM",
  "stream_url_hint":   "https://...",  // opcional
  "notes":             "La escuchan mucho en TGU"
}
// Response 201
{ "id": "uuid", "signal_name": "Radio Uno HN", "status": "pending", "created_at": "..." }
```

#### `GET /api/media-sources/requests`
Historial de solicitudes del tenant (status: `pending` | `approved` | `rejected`).

---

### 4.11 Links de Reporte Público

Permiten compartir evidencia con terceros sin login.

#### `POST /api/reports`
```json
// Request
{
  "campaign_id":     "uuid",
  "ad_id":           "uuid",      // opcional, filtra por anuncio
  "title":           "Pauta Claro Junio 2026",
  "date_from":       "2026-06-01",
  "date_to":         "2026-06-30",
  "expires_in_days": 30           // null = no expira
}
// Response 201
{ "token": "abc123def456", "url": "/report/abc123def456", ... }
```

#### `GET /api/reports`
Lista de links del tenant.

#### `PATCH /api/reports/{token}`
```json
{ "is_active": false }
```

#### `DELETE /api/reports/{token}`
Elimina el link permanentemente.

#### `GET /api/reports/{token}/qr.png`
Imagen PNG del QR del link (para imprimir).

---

### 4.12 Vista Pública (sin auth)

#### `GET /api/public/{token}`
Metadata del reporte: título, campaña, fechas, total de airings.

#### `GET /api/public/{token}/detections`
Detecciones del reporte (paginadas, igual que `/api/detections`).

---

### 4.13 Plan del Tenant

#### `GET /api/plan`
```json
{
  "id":                  "uuid",
  "name":                "professional",
  "display_name":        "Professional",
  "max_streams":         20,
  "price_monthly":       299.00,
  "streams_activos":     16,
  "streams_disponibles": 4,
  "ilimitado":           false
}
```

---

## 5. Modelos TypeScript sugeridos

```typescript
// Auth
interface AuthToken {
  access_token: string;
  token_type:   'bearer';
  role:         'admin' | 'viewer';
}

interface Me {
  id:          string;
  email:       string;
  role:        'admin' | 'viewer';
  last_login:  string;
  client_name: string;
  client_slug: string;
  tier:        string;
}

// Campaigns
type CampaignStatus = 'active' | 'paused' | 'archived';
type CampaignType   = 'scheduled' | 'ongoing';

interface Campaign {
  id:            string;
  name:          string;
  description:   string | null;
  status:        CampaignStatus;
  campaign_type: CampaignType;
  start_date:    string | null;   // YYYY-MM-DD
  end_date:      string | null;
  budget_usd:    number | null;
  created_at:    string;
  client_id:     string;
  client_name:   string;
  total_ads:     number;
  total_airings: number;
}

// Ads
interface Ad {
  id:               string;
  name:             string;
  description:      string | null;
  status:           'active' | 'paused';
  duration_seconds: number;
  match_min:        number | null;
  clip_pad_seconds: number;
  total_airings:    number;
  ultimo_airing:    string | null;
}

// Detections
type ConfidenceLevel = 'very_high' | 'high' | 'medium' | 'low';

interface Detection {
  id:               number;
  stream_id:        string;
  ad_name:          string;
  campaign_name:    string;
  air_time_hn:      string;   // ISO con offset -06:00
  ts_label:         string;
  score:            number;   // 0–100
  confidence_level: ConfidenceLevel;
  clip_s3_key:      string | null;
  algorithm:        string;
  created_at:       string;
}

interface DetectionPage {
  total:   number;
  page:    number;
  pages:   number;
  results: Detection[];
}

// Media Sources
type MediaType    = 'radio' | 'tv';
type HealthStatus = 'healthy' | 'degraded' | 'offline';

interface MediaSource {
  id:                string;
  slug:              string;
  name:              string;
  media_type:        MediaType;
  country:           string;
  frequency_channel: string | null;
  description:       string | null;
  logo_url:          string | null;
  health_status:     HealthStatus;
  lifecycle_status:  'active' | 'discontinued';
  assigned_at:       string;
  total_detecciones: number;
}

// Clients
interface Client {
  id:              string;
  name:            string;
  industry:        string | null;
  country:         string;
  logo_url:        string | null;
  active:          boolean;
  created_at:      string;
  total_campaigns: number;
}
```

---

## 6. Señales disponibles (16 en total)

| Slug | Nombre | Tipo | Ruta |
|------|--------|------|------|
| `xy_hrn` | XY HRN | Radio | SOCKS5 |
| `xy_tgu` | XY TGU | Radio | SOCKS5 |
| `xy_sps` | XY SPS | Radio | SOCKS5 |
| `radio_satelite` | Radio Satelite | Radio | SOCKS5 |
| `fm_941` | 94.1 FM | Radio | SOCKS5 |
| `suave_fm` | Suave FM | Radio | SOCKS5 |
| `radio_america` | Radio America | Radio | Direct |
| `radio_globo` | Radio Globo | Radio | Direct |
| `radio_el_patio` | Radio El Patio | Radio | Direct |
| `radio_choluteca` | Radio Choluteca | Radio | SOCKS5 |
| `hch_tv` | HCH TV | TV | Direct |
| `teleceiba` | TeleCeiba | TV | Direct |
| `canal_11` | Canal 11 | TV | Direct |
| `canal_6` | Canal 6 | TV | Direct |
| `canal_5` | Canal 5 | TV | Direct |
| `tsi` | TSI | TV | Direct |

---

## 7. Reglas de negocio clave

### Campaña
- `campaign_type = 'scheduled'` → `end_date` **requerido**
- `campaign_type = 'ongoing'` → `end_date` debe ser `null`
- `status` válidos: `active`, `paused`, `archived`
- DELETE es soft (status → archived), nunca borra detecciones

### Anuncio
- `clip_pad_seconds`: segundos de contexto que se agregan al clip de evidencia (default 5, max 20)
- `match_min`: umbral de coincidencia 0.0–1.0 (null = usa el default del sistema)
- DELETE es soft (`deleted_at`)

### Detecciones
- `score` 0–100 (qué tan seguro está el sistema)
- `confidence_level` derivado del score:
  - `very_high` ≥ 90
  - `high` 75–89
  - `medium` 60–74
  - `low` < 60
- `air_time_hn` ya viene en zona horaria de Honduras (GMT-6, sin DST)
- El clip pre-firmado (`/api/detections/{id}/clip`) expira en **5 minutos**

### Media Sources
- Solo las asignadas al tenant aparecen en `/api/media-sources`
- Para campañas, solo puedes asignar estaciones que ya estén en tu tenant
- `health_status` refleja el estado de captura en tiempo real

---

## 8. Configurar el proyecto de frontend

### Headers recomendados
```typescript
const api = axios.create({
  baseURL: 'http://137.184.53.234:8080',
  headers: { 'Content-Type': 'application/json' },
});

// Interceptor para añadir el token
api.interceptors.request.use(config => {
  const token = localStorage.getItem('access_token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Interceptor para manejar 401
api.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401) {
      localStorage.removeItem('access_token');
      window.location.href = '/login';
    }
    return Promise.reject(err);
  }
);
```

### Credenciales de prueba (cuenta DEMO)
Pídelas al backend dev — el tenant `MediaAI DEMO` ya tiene:
- 3 campañas con anuncios reales
- 16 señales asignadas
- Detecciones históricas para development/QA

### CORS
El backend ya tiene CORS abierto (`allow_origins=['*']`). Cualquier origen puede hacer requests.

---

## 9. Contacto y acceso

- **Backend API**: `gchiham/media-app` (privado) — pedir acceso a `gchiham`
- **Pregunta técnica**: revisar primero `http://137.184.53.234:8080/api/docs`
- **DB**: no hay acceso directo para el frontend; todo pasa por la API
- **Infra invisible**: el frontend NO debe exponer nada del sistema de captura (IPs de gateways, servidores, etc.)
