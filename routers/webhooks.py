import logging
import os

from fastapi import APIRouter, HTTPException, Request

from core.db import get_db

log = logging.getLogger('publiaudit-api')

router = APIRouter(prefix='/api/webhooks', tags=['webhooks'])

# ── Webhook Clerk ─────────────────────────────────────────────────────────────
@router.post('/clerk', include_in_schema=False)
async def clerk_webhook(request: Request):
    """
    Sincroniza la tabla users con eventos de Clerk.
    Configurar en Clerk Dashboard → Webhooks:
      URL: http://137.184.53.234:8080/api/webhooks/clerk
      Events: user.created, user.updated, user.deleted
    """
    import json
    from svix.webhooks import Webhook, WebhookVerificationError

    webhook_secret = os.environ.get('CLERK_WEBHOOK_SECRET', '')
    if not webhook_secret:
        raise HTTPException(status_code=500, detail='CLERK_WEBHOOK_SECRET no configurado')

    payload = await request.body()
    headers = dict(request.headers)

    try:
        wh    = Webhook(webhook_secret)
        event = wh.verify(payload, headers)
    except WebhookVerificationError:
        raise HTTPException(status_code=400, detail='Firma de webhook inválida')

    evt_type = event['type']
    data     = event['data']

    with get_db() as conn:
        with conn.cursor() as cur:
            if evt_type == 'user.created':
                meta      = data.get('public_metadata', {})
                emails    = data.get('email_addresses', [])
                email     = emails[0]['email_address'] if emails else ''
                fname     = data.get('first_name') or ''
                lname     = data.get('last_name') or ''
                full_name = f'{fname} {lname}'.strip() or None
                cur.execute(
                    """INSERT INTO users (clerk_user_id, email, full_name, avatar_url, role, tenant_id)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (clerk_user_id) DO NOTHING""",
                    (data['id'], email, full_name, data.get('image_url'),
                     meta.get('role', 'viewer'), meta.get('tenant_id'))
                )
            elif evt_type == 'user.updated':
                meta   = data.get('public_metadata', {})
                emails = data.get('email_addresses', [])
                fname  = data.get('first_name') or ''
                lname  = data.get('last_name') or ''
                cur.execute(
                    """UPDATE users SET
                         email      = COALESCE(%s, email),
                         full_name  = COALESCE(%s, full_name),
                         avatar_url = COALESCE(%s, avatar_url),
                         role       = COALESCE(%s, role),
                         tenant_id  = COALESCE(%s::uuid, tenant_id)
                       WHERE clerk_user_id = %s""",
                    (
                        emails[0]['email_address'] if emails else None,
                        f'{fname} {lname}'.strip() or None,
                        data.get('image_url'),
                        meta.get('role'),
                        meta.get('tenant_id'),
                        data['id'],
                    )
                )
            elif evt_type == 'user.deleted':
                cur.execute(
                    "UPDATE users SET is_active = false WHERE clerk_user_id = %s",
                    (data['id'],)
                )

    log.info(f'[clerk-webhook] {evt_type} → {data.get("id")}')
    return {'ok': True}
