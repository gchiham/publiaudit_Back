import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth.clerk import verify_clerk_token as verify_token
from core.db import get_db

JWT_SECRET = os.environ.get('JWT_SECRET', '')
JWT_EXP_HOURS = int(os.environ.get('JWT_EXP_HOURS', '24'))

router = APIRouter(prefix='/api/auth', tags=['auth'])


class LoginRequest(BaseModel):
    email: str
    password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def create_token(user_id: str, tenant_id: str, role: str, email: str) -> str:
    """Mantenido solo para compatibilidad con /api/auth/login."""
    payload = {
        'sub':       user_id,
        'tenant_id': tenant_id,
        'role':      role,
        'email':     email,
        'exp':       datetime.now(timezone.utc) + timedelta(hours=JWT_EXP_HOURS),
        'iat':       datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

# ── AUTH ──────────────────────────────────────────────────────────────────────
@router.post('/login')
def login(body: LoginRequest):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT id, tenant_id, email, password_hash, role, is_active '
                'FROM users WHERE email = %s',
                (body.email.lower().strip(),)
            )
            user = cur.fetchone()

    if not user or not user['is_active']:
        raise HTTPException(status_code=401, detail='Credenciales incorrectas')

    if not bcrypt.checkpw(body.password.encode(), user['password_hash'].encode()):
        raise HTTPException(status_code=401, detail='Credenciales incorrectas')

    # Actualizar last_login
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE users SET last_login = NOW() WHERE id = %s', (str(user['id']),))

    token = create_token(
        str(user['id']), str(user['tenant_id']), user['role'], user['email']
    )
    return {'access_token': token, 'token_type': 'bearer', 'role': user['role']}

@router.get('/me')
def me(ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                '''SELECT u.id, u.email, u.role, u.last_login,
                          COALESCE(c.name, '') as client_name, COALESCE(c.slug, '') as client_slug, COALESCE(c.tier, 'free') as tier
                   FROM users u LEFT JOIN tenants c ON u.tenant_id = c.id
                   WHERE u.id = %s''',
                (ctx['sub'],)
            )
            return dict(cur.fetchone())

@router.post('/change-password')
def change_password(body: ChangePasswordRequest, ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT password_hash FROM users WHERE id = %s', (ctx['sub'],))
            user = cur.fetchone()
            if not bcrypt.checkpw(body.current_password.encode(), user['password_hash'].encode()):
                raise HTTPException(status_code=400, detail='Contraseña actual incorrecta')
            new_hash = bcrypt.hashpw(body.new_password.encode(), bcrypt.gensalt()).decode()
            cur.execute('UPDATE users SET password_hash = %s WHERE id = %s',
                        (new_hash, ctx['sub']))
    return {'ok': True}
