#!/usr/bin/env python3
"""Baja automática de campañas programadas vencidas — PubliAudit.

Marca status='ended' en campañas 'scheduled' cuyo end_date + ventana de gracia ya pasó,
y en sus anuncios activos. Las 'ongoing' nunca vencen. Idempotente (re-correrlo no hace daño).
Corre a diario por systemd timer (publiaudit-autobaja.timer).

Ventana de gracia = CAMPAIGN_GRACE_DAYS (default 15): se siguen monitoreando emisiones después
del end_date para cazar SOBRE-PAUTA; pasada la gracia, se apagan (status='ended') y el worker
del Destroyer deja de fingerprintearlas. Las detecciones (evidencia) NO se tocan.
"""
import os
import sys
import psycopg2

GRACE = int(os.environ.get('CAMPAIGN_GRACE_DAYS', '15'))

try:
    conn = psycopg2.connect(
        host=os.environ['PG_HOST'], port=os.environ['PG_PORT'],
        dbname=os.environ['PG_DB'], user=os.environ['PG_USER'],
        password=os.environ['PG_PASS'], sslmode='require', connect_timeout=15,
    )
except Exception as e:
    print('autobaja: error de conexión:', e)
    sys.exit(1)

try:
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE advertisements a SET status='ended', updated_at=NOW()
                FROM campaigns c
                WHERE a.campaign_id = c.id
                  AND a.status='active' AND a.deleted_at IS NULL
                  AND c.campaign_type='scheduled' AND c.end_date IS NOT NULL
                  AND c.end_date + (%s || ' days')::interval < CURRENT_DATE
                """,
                (GRACE,),
            )
            ads = cur.rowcount
            cur.execute(
                """
                UPDATE campaigns SET status='ended', updated_at=NOW()
                WHERE status='active' AND campaign_type='scheduled' AND end_date IS NOT NULL
                  AND end_date + (%s || ' days')::interval < CURRENT_DATE
                """,
                (GRACE,),
            )
            camps = cur.rowcount
    print('autobaja OK: %d campañas finalizadas, %d anuncios finalizados (gracia %dd)'
          % (camps, ads, GRACE))
finally:
    conn.close()
