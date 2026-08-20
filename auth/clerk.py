"""
Verificación de JWT de Clerk para FastAPI (sync / psycopg2).
Reemplaza el sistema JWT HS256 propio.

Multi-issuer: acepta tokens de varias instancias de Clerk (producción + development),
para permitir desarrollo local (la instancia dev de Clerk sí acepta localhost mientras
que producción no). Configurable por env CLERK_ISSUERS (CSV); default = prod + dev.
"""
import os
import threading
import httpx
from jose import jwt, JWTError
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from datetime import datetime, timezone

from core.db import get_db

# Issuers aceptados (CSV en CLERK_ISSUERS). Cada instancia de Clerk firma con su propio
# set de claves; los 'kid' son únicos por instancia, así que el cache combinado es seguro.
CLERK_ISSUERS = [
    s.strip() for s in os.environ.get(
        "CLERK_ISSUERS",
        "https://clerk.publiaudit.com,https://regular-gobbler-51.clerk.accounts.dev",
    ).split(",") if s.strip()
]

security = HTTPBearer()

_jwks_cache: dict = {}
_jwks_lock = threading.Lock()


def _load_jwks() -> None:
    """Descarga las claves públicas JWKS de TODOS los issuers al cache (por kid).
    Un issuer inalcanzable no rompe a los demás."""
    for iss in CLERK_ISSUERS:
        try:
            resp = httpx.get(f"{iss}/.well-known/jwks.json", timeout=5)
            resp.raise_for_status()
            for key in resp.json().get("keys", []):
                _jwks_cache[key["kid"]] = key
        except Exception:
            pass


def _get_key(kid: str):
    """Devuelve la clave pública por kid; refetch de JWKS si el kid no está (rotación)."""
    with _jwks_lock:
        key = _jwks_cache.get(kid)
        if key is None:
            _load_jwks()
            key = _jwks_cache.get(kid)
        return key


def verify_clerk_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependencia FastAPI que valida el JWT de Clerk y retorna el mismo
    dict ctx que usaba el sistema JWT propio:
      {'sub': user_id, 'tenant_id': tenant_id, 'role': role, 'email': email}

    Busca o crea el usuario en la tabla local (lazy sync).
    """
    token = credentials.credentials

    # 1. Verificar firma RS256 contra el issuer del token (si está permitido)
    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.get_unverified_claims(token)
        iss = unverified.get("iss")
        if iss not in CLERK_ISSUERS:
            raise HTTPException(status_code=401, detail="Issuer de Clerk no permitido")
        pub_key = _get_key(header.get("kid"))
        if not pub_key:
            raise HTTPException(status_code=401, detail="Clave pública de Clerk no encontrada")
        payload = jwt.decode(
            token,
            pub_key,
            algorithms=["RS256"],
            issuer=iss,
            options={"verify_aud": False},
        )
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {e}")

    clerk_user_id = payload.get("sub")
    if not clerk_user_id:
        raise HTTPException(status_code=401, detail="Token sin 'sub'")

    # 2. Buscar o crear usuario local (usa el mismo pool de conexiones que el resto
    # de la API — antes abría una conexión nueva por request, ver core/db.py).
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, tenant_id, role, email, is_active FROM users WHERE clerk_user_id = %s",
                    (clerk_user_id,)
                )
                user = cur.fetchone()

                if user is None:
                    # Primera vez — obtener email desde Clerk Backend API
                    email = ""
                    clerk_secret = os.environ.get("CLERK_SECRET_KEY", "")
                    if clerk_secret:
                        try:
                            r = httpx.get(
                                f"https://api.clerk.com/v1/users/{clerk_user_id}",
                                headers={"Authorization": f"Bearer {clerk_secret}"},
                                timeout=5,
                            )
                            if r.status_code == 200:
                                udata = r.json()
                                emails = udata.get("email_addresses", [])
                                email = emails[0]["email_address"] if emails else ""
                        except Exception:
                            pass

                    # Buscar usuario pre-creado por email y vincular clerk_user_id
                    if email:
                        cur.execute(
                            "SELECT id, tenant_id, role, email, is_active FROM users WHERE email = %s AND clerk_user_id IS NULL",
                            (email,)
                        )
                        user = cur.fetchone()
                        if user:
                            cur.execute("UPDATE users SET clerk_user_id = %s WHERE id = %s", (clerk_user_id, str(user['id'])))

                    # Si no hay match por email, crear usuario nuevo
                    if user is None:
                        role = "viewer"
                        tenant_id = None
                        if clerk_secret and email:
                            try:
                                meta = udata.get("public_metadata", {})
                                role = meta.get("role", "viewer")
                                tenant_id = meta.get("tenant_id")
                            except Exception:
                                pass
                        cur.execute(
                            """INSERT INTO users (clerk_user_id, email, role, tenant_id)
                               VALUES (%s, %s, %s, %s)
                               RETURNING id, tenant_id, role, email, is_active""",
                            (clerk_user_id, email, role, tenant_id)
                        )
                        user = cur.fetchone()

                if not user['is_active']:
                    raise HTTPException(status_code=403, detail="Cuenta desactivada")

                # Actualizar last_login
                cur.execute(
                    "UPDATE users SET last_login = NOW(), last_login_at = NOW() WHERE id = %s",
                    (str(user['id']),)
                )
                # get_db() hace commit al salir del bloque `with` sin excepciones.

            return {
                'sub':       str(user['id']),
                'tenant_id': str(user['tenant_id']) if user['tenant_id'] else None,
                'role':      user['role'],
                'email':     user['email'],
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error de autenticación: {e}")


def require_admin(ctx: dict = Depends(verify_clerk_token)) -> dict:
    """Solo permite usuarios con role='admin'."""
    if ctx.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="Se requiere rol admin")
    return ctx
