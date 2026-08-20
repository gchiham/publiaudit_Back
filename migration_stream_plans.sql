-- ============================================================
-- Migration: Stream subscription plans
-- PubliAudit — Plan limits + client stream subscriptions
-- ============================================================

-- 1. Plans catalog
CREATE TABLE IF NOT EXISTS plans (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(50)  UNIQUE NOT NULL,
    display_name  VARCHAR(100) NOT NULL,
    max_streams   INTEGER      NOT NULL,
    price_monthly NUMERIC(10,2),
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

INSERT INTO plans (name, display_name, max_streams, price_monthly) VALUES
    ('basic',      'Plan Básico',        3,  99.00),
    ('pro',        'Plan Pro',           8, 249.00),
    ('enterprise', 'Plan Enterprise',   -1,   0.00)  -- -1 = ilimitado
ON CONFLICT (name) DO NOTHING;

-- 2. Add plan_id to clients (use existing tier column as default mapping)
ALTER TABLE clients ADD COLUMN IF NOT EXISTS plan_id UUID REFERENCES plans(id);

-- Map existing clients: si tier='agency' → pro, resto → basic
UPDATE clients
SET plan_id = (
    SELECT id FROM plans
    WHERE name = CASE
        WHEN clients.tier IN ('agency', 'pro') THEN 'pro'
        WHEN clients.tier = 'enterprise'       THEN 'enterprise'
        ELSE 'basic'
    END
)
WHERE plan_id IS NULL;

-- 3. Client active stream subscriptions
CREATE TABLE IF NOT EXISTS client_streams (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id  UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    stream_id  UUID NOT NULL REFERENCES stream_catalog(id) ON DELETE CASCADE,
    added_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    added_by   UUID REFERENCES users(id),
    UNIQUE (client_id, stream_id)
);

-- 4. Auto-populate: give existing clients the streams they already have detections for
--    (respects current state, capped to plan limit)
INSERT INTO client_streams (client_id, stream_id)
SELECT DISTINCT fd.client_id, sc.id
FROM fingerprint_detections fd
JOIN stream_catalog sc ON sc.id = fd.stream_id
ON CONFLICT (client_id, stream_id) DO NOTHING;

-- 5. Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_client_streams_client ON client_streams(client_id);
CREATE INDEX IF NOT EXISTS idx_client_streams_stream ON client_streams(stream_id);
