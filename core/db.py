import os
import threading
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool

PG_HOST = os.environ.get('PG_HOST', 'private-media-db-do-user-2116998-0.d.db.ondigitalocean.com')
PG_PORT = int(os.environ.get('PG_PORT', '25060'))
PG_DB   = os.environ.get('PG_DB',   'destroyer_db')
PG_USER = os.environ.get('PG_USER', 'destroyer')
PG_PASS = os.environ['PG_PASS']
PG_SSLMODE = os.environ.get('PG_SSLMODE', 'require')

# ── DB — pool de conexiones ───────────────────────────────────────────────────
# Antes se abría una conexión nueva (TCP+TLS) por request → cuello de botella y
# riesgo de agotar el límite de conexiones de la PG managed. Ahora se reutiliza
# un pool. El semáforo hace que los requests ESPEREN una conexión libre en vez de
# fallar con error cuando el pool está lleno (mejor que devolver 500 bajo carga).
# Tamaño vía env: PG_POOL_MIN (def 1) y PG_POOL_MAX (def 5). OJO: la base es el plan
# DO de max_connections=25 (≈3 reservadas + ~13 las usan procesos internos de DO),
# y TODOS los servicios se conectan como user 'destroyer' (API main+auth, daemon,
# health_engine, worker). Presupuesto real para apps ≈ 9-12. La suma de
# PG_POOL_MAX (main) + AUTH_POOL_MAX (auth) debe dejar margen a daemon/health/worker.
_PG_POOL = None
_PG_POOL_SEM: Optional[threading.Semaphore] = None
_PG_POOL_LOCK = threading.Lock()

def _get_pool():
    global _PG_POOL, _PG_POOL_SEM
    if _PG_POOL is None:
        with _PG_POOL_LOCK:
            if _PG_POOL is None:
                maxconn = int(os.environ.get('PG_POOL_MAX', '5'))
                _PG_POOL_SEM = threading.Semaphore(maxconn)
                _PG_POOL = psycopg2.pool.ThreadedConnectionPool(
                    int(os.environ.get('PG_POOL_MIN', '1')), maxconn,
                    host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                    user=PG_USER, password=PG_PASS,
                    connect_timeout=10, sslmode=PG_SSLMODE,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
    return _PG_POOL

@contextmanager
def get_db():
    pool = _get_pool()
    _PG_POOL_SEM.acquire()                     # espera si todas las conexiones están en uso
    conn = None
    try:
        conn = pool.getconn()
        if conn.closed:                        # descarta conexiones muertas (server cerró el socket)
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    finally:
        if conn is not None:
            try:
                pool.putconn(conn, close=bool(conn.closed))
            except Exception:
                pass
        _PG_POOL_SEM.release()


def pg_arr(v):
    """Normaliza un uuid[] de psycopg2 (lista, o literal Postgres '{a,b}') a lista de str."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if x]
    s = str(v).strip()
    if s.startswith('{') and s.endswith('}'):
        s = s[1:-1]
    return [x.strip().strip('"') for x in s.split(',') if x.strip()]
