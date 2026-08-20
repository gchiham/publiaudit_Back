-- ════════════════════════════════════════════════════════════════════════════
-- PubliAudit · Reportes unificados (constructor componible)
-- Fecha: 2026-06-27
-- Agrega a report_public_links: cliente destino, multi-campaña/anuncio,
-- visibilidad (privado/público) y config (métricas + marca blanca).
-- Idempotente y backward-compatible: backfilea filas existentes.
-- Correr en la PG managed (destroyer_db) ANTES de desplegar el main.py nuevo.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE report_public_links
    ADD COLUMN IF NOT EXISTS client_id    uuid REFERENCES clients(id),
    ADD COLUMN IF NOT EXISTS campaign_ids uuid[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS ad_ids       uuid[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS visibility   text   NOT NULL DEFAULT 'public',
    ADD COLUMN IF NOT EXISTS config       jsonb  NOT NULL DEFAULT '{}'::jsonb;

-- campaign_id deja de ser obligatorio (ahora vive en campaign_ids; se mantiene
-- poblado con la 1ra campaña para back-compat de lectores legacy).
ALTER TABLE report_public_links ALTER COLUMN campaign_id DROP NOT NULL;

-- Backfill: campañas / anuncios single -> array
UPDATE report_public_links
   SET campaign_ids = ARRAY[campaign_id]
 WHERE campaign_id IS NOT NULL
   AND (campaign_ids IS NULL OR cardinality(campaign_ids) = 0);

UPDATE report_public_links
   SET ad_ids = ARRAY[ad_id]
 WHERE ad_id IS NOT NULL
   AND (ad_ids IS NULL OR cardinality(ad_ids) = 0);

-- Backfill: cliente destino desde la campaña legacy
UPDATE report_public_links rpl
   SET client_id = camp.client_id
  FROM campaigns camp
 WHERE camp.id = rpl.campaign_id
   AND rpl.client_id IS NULL;

-- Backfill: visibilidad según estado actual
UPDATE report_public_links
   SET visibility = CASE WHEN is_active THEN 'public' ELSE 'private' END
 WHERE visibility IS NULL OR visibility NOT IN ('public', 'private');

-- Backfill: config con TODAS las métricas para reportes existentes
UPDATE report_public_links
   SET config = jsonb_build_object(
         'metrics', jsonb_build_array(
             'total_emissions','by_medium','timeline','detections_detail',
             'by_ad','by_campaign','period_covered'),
         'branding', '{}'::jsonb)
 WHERE config IS NULL OR config = '{}'::jsonb OR NOT (config ? 'metrics');

-- Índices para el filtrado por alcance (ANY(campaign_ids))
CREATE INDEX IF NOT EXISTS idx_rpl_campaign_ids ON report_public_links USING GIN (campaign_ids);
CREATE INDEX IF NOT EXISTS idx_rpl_client_id    ON report_public_links (client_id);

COMMIT;
