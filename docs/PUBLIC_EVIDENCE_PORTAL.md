# Public Evidence Portal

## Overview

The Public Evidence Portal converts any PDF report into a gateway for a richer online experience. When an agency generates a report, PubliAudit automatically creates a unique, cryptographically secure public URL and QR code. The advertiser scans the QR code — no login, no account, no friction — and lands on a fully interactive evidence portal showing all detections, filters, and audio playback.

---

## Architecture

```
Agency generates report
        ↓
POST /api/reports  (JWT required)
        ↓
Token generated (secrets.token_hex(8) → 16 hex chars)
Stored in report_public_links
QR PNG generated (qrcode + Pillow)
QR base64 returned for PDF embedding
        ↓
Advertiser scans QR → opens browser
        ↓
GET /report/{token}  (no auth)   → HTML portal shell
GET /api/public/{token}          → metadata + summary cards + filter options
GET /api/public/{token}/detections → paginated detections + presigned S3 clip URLs
```

The portal is served from the same origin as the PubliAudit API (port 8080), so there are no CORS or mixed-content issues. Evidence audio clips are served via 2-hour presigned S3 URLs generated on demand.

---

## Database

### New table: `report_public_links`

```sql
CREATE TABLE report_public_links (
    id               UUID         DEFAULT gen_random_uuid() PRIMARY KEY,
    token            CHAR(16)     NOT NULL UNIQUE,       -- cryptographic random, URL-safe hex
    client_id        UUID         NOT NULL,              -- owner (no FK to avoid cascade issues)
    campaign_id      UUID         NOT NULL,              -- required: scopes all detections
    ad_id            UUID,                               -- optional: narrow to single ad
    title            VARCHAR(255) NOT NULL,              -- display title on portal header
    date_from        DATE,                               -- optional date range filter
    date_to          DATE,
    expires_at       TIMESTAMPTZ,                        -- NULL = never expires
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    access_count     INTEGER      NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMPTZ,
    created_by       UUID,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX rpl_token_idx  ON report_public_links(token);
CREATE INDEX rpl_client_idx ON report_public_links(client_id);
```

### Modified: `clients`

```sql
ALTER TABLE clients ADD COLUMN IF NOT EXISTS logo_url VARCHAR(500);
```

---

## Token Security

- Generated with `secrets.token_hex(8)` → 16 lowercase hex characters
- 2^64 possible values (~1.8 × 10^19) — brute-force infeasible
- URL-safe: only `[0-9a-f]`, no encoding required
- Non-sequential: no relation to database IDs or timestamps
- Example: `cc50177d91a7d667`

---

## API Endpoints

### Authenticated (JWT required)

#### `POST /api/reports`
Creates a public evidence link for a campaign/ad.

**Request body:**
```json
{
  "campaign_id":     "uuid",
  "ad_id":           "uuid",         // optional — scope to single ad
  "title":           "Campaña Vacunación — Informe Q2 2026",
  "date_from":       "2026-04-01",   // optional
  "date_to":         "2026-06-30",   // optional
  "expires_in_days": 90              // null = never expires
}
```

**Response:**
```json
{
  "id":         "uuid",
  "token":      "cc50177d91a7d667",
  "url":        "http://publiaudit.com/report/cc50177d91a7d667",
  "qr_base64":  "iVBORw0KGgo...",   // PNG QR code, base64 encoded — embed in PDF
  "created_at": "2026-06-12T00:00:00+00:00"
}
```

#### `GET /api/reports`
Lists all public links created by the authenticated client.

#### `PATCH /api/reports/{token}`
Toggle a link active/inactive.
```json
{ "is_active": false }
```

#### `DELETE /api/reports/{token}`
Permanently deletes a link.

#### `GET /api/reports/{token}/qr.png`
Returns the QR code as a PNG image (for display or download).

---

### Public (no auth required)

#### `GET /api/public/{token}`
Returns report metadata + summary cards + filter options.

```json
{
  "report": {
    "title":         "Campaña Vacunación — Informe Q2 2026",
    "client_name":   "MediaAI",
    "client_logo":   null,
    "campaign_name": "Historical Data Migration",
    "ad_name":       null,
    "date_from":     "2026-04-01",
    "date_to":       "2026-06-30",
    "expires_at":    "2026-09-10T00:00:00+00:00"
  },
  "summary": {
    "total_detections": 127,
    "media_sources":    8,
    "first_detection":  "2026-06-07T18:58:35",
    "last_detection":   "2026-06-11T19:59:19"
  },
  "filters": {
    "streams":   [{"id": "radio_america", "name": "radio_america"}, ...],
    "campaigns": [{"id": "uuid", "name": "Historical Data Migration"}],
    "ads":       [{"id": "uuid", "name": "molinero_ad"}, ...]
  }
}
```

Returns **404** if token doesn't exist.
Returns **410** if link is inactive or expired (portal shows "This report is no longer available").

Each call increments `access_count` and updates `last_accessed_at`.

#### `GET /api/public/{token}/detections`
Paginated detections with optional filters. All filters are scoped to the report's campaign/date range — users cannot escape the report boundary.

**Query params:**
| Param        | Type   | Description                    |
|--------------|--------|-------------------------------|
| `stream_id`  | string | Filter by media source         |
| `campaign_id`| string | Filter by campaign             |
| `ad_id`      | string | Filter by advertisement        |
| `date_from`  | date   | Additional date narrowing      |
| `date_to`    | date   | Additional date narrowing      |
| `page`       | int    | Page number (default: 1)       |
| `page_size`  | int    | Results per page (default: 50, max: 200) |

**Response:**
```json
{
  "total":   127,
  "page":    1,
  "pages":   43,
  "results": [
    {
      "id":               127,
      "stream_id":        "radio_america",
      "stream_name":      "Radio America",
      "ad_name":          "Vacunacion_Amp_v2",
      "campaign_name":    "Historical Data Migration",
      "air_time_hn":      "2026-06-11T19:59:19.670748-06:00",
      "score":            1477,
      "confidence_level": "very_high",
      "clip_url":         "https://mediadev-recordings.s3.amazonaws.com/clips/...?Expires=...",
      "clip_type":        "audio"
    }
  ]
}
```

`clip_url` is a presigned S3 URL (2-hour TTL). If no clip exists, `null`.
`clip_type` is `"video"` (.mp4/.ts), `"audio"` (.mp3), or `null` (no clip) — the portal
uses it to render a `<video>` or `<audio>` player. `stream_name` is the human-readable
station name from `stream_catalog` (e.g. "Radio America"), `stream_id` is the slug.
All `air_time_hn` values carry the `-06:00` offset explicitly (Honduras, no DST).

---

### Portal HTML

#### `GET /report/{token}`
Serves the self-contained HTML Evidence Portal. No authentication. Designed for direct browser access from QR code or URL.

The page bootstraps itself by calling `/api/public/{token}` and `/api/public/{token}/detections` on load.

---

## Portal UX

The portal uses the PubliAudit design system (CSS variables from tokens/colors.css, Montserrat + Hanken Grotesk fonts).

**Sections:**
1. **Header** — client logo (initials fallback), client name, campaign name, reporting period chip, "PubliAudit" badge, theme toggle (dark/light)
2. **Summary Cards** — Total Detections, Medios Monitoreados, Primera Detección, Última Detección
3. **Filters** — Media Source, Campaign, Ad (only shown when >1 option), Date From, Date To, Reset button
4. **Evidence Table** — Date | Time | Media Source | Campaign | Ad | Evidence (Play button)
5. **Pagination** — shows page range with ellipsis; max 50 results per page
6. **Audio Player** — fixed bottom bar, shows ad name + source/time, HTML5 `<audio>` controls
7. **Footer** — "Powered by PubliAudit · An AdSignal Platform"

**Responsive:** collapses columns and KPI grid on mobile.
**Theme:** defaults to dark (user preference saved to localStorage).

---

## Embedding QR Code in PDF

The `POST /api/reports` response includes `qr_base64` — a base64-encoded PNG of the QR code. To embed in a PDF:

**HTML/WeasyPrint:**
```html
<img src="data:image/png;base64,{{ qr_base64 }}" width="120" height="120">
<p>Ver evidencia completa en línea</p>
<p>{{ url }}</p>
```

**ReportLab (Python):**
```python
from PIL import Image
import io, base64
qr_bytes = base64.b64decode(response['qr_base64'])
img = Image.open(io.BytesIO(qr_bytes))
# draw to canvas...
```

---

## Security Model

| Concern               | Mitigation |
|-----------------------|------------|
| Unauthorized access   | 16-char hex token (2^64 space) — brute force takes ~585 million years at 1M req/s |
| Cross-report leakage  | All queries scope `client_id` from the token's stored value — users cannot navigate to other clients' data |
| Privilege escalation  | Portal is read-only; no mutation endpoints are public |
| Link revocation       | Agency can toggle `is_active=false` or delete the link instantly |
| Expiration            | `expires_at` checked on every request; expired links return 410 |
| S3 clip exposure      | Presigned URLs have 2-hour TTL; S3 bucket is private |

No rate limiting is currently implemented at the application layer. If needed, add nginx `limit_req_zone` per IP.

---

## Evidence Storage

> **Timezone note:** `air_time` is stored as UTC in the database (migration: 13 jun 2026). The portal API converts to Honduras time (`AT TIME ZONE America/Tegucigalpa`) before returning `air_time_hn`. Honduras has no DST — offset is always UTC-6.

Clips are stored in S3 at `s3://mediadev-recordings/clips/{stream_id}/{date}_{hour}.mp3__{offset}s.mp3`. The portal does **not** duplicate files — it generates presigned URLs pointing to the existing storage created by the Destroyer detection pipeline.

---

## File Locations

| File | Purpose |
|------|---------|
| `/opt/publiaudit-api/main.py` | FastAPI backend — all endpoints |
| `/opt/publiaudit-api/portal.html` | Evidence Portal HTML template |
| `/opt/publiaudit-api/docs/PUBLIC_EVIDENCE_PORTAL.md` | This document |
| `/etc/nginx/sites-enabled/publiaudit-api` | Nginx: routes `/api/` and `/report/` to FastAPI |
| `/etc/systemd/system/publiaudit-api.service` | Systemd: uses EnvironmentFile=/etc/publiaudit-api.env (credentials not exposed in systemctl show) |
| `/etc/publiaudit-api.env` | All secrets: PG, JWT, AWS, CORS_ORIGINS (chmod 600, root:root) |

---

## Future Enhancements

- **Video playback**: `clip_url` can already point to `.ts` or `.mp4` files — add `<video>` to the portal alongside `<audio>`
- **Domain / TLS**: Point `publiaudit.com` to `159.223.104.91` and install Let's Encrypt; update `PUBLIC_BASE_URL` in systemd
- **Rate limiting**: `nginx limit_req_zone $binary_remote_addr zone=portal:10m rate=30r/m` on the `/report/` and `/api/public/` locations
- **Password-protected links**: Add optional `access_password` field + bcrypt check in `_resolve_link`
- **Download CSV**: Add `GET /api/public/{token}/detections.csv` for advertiser export
