import io
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from auth.clerk import verify_clerk_token as verify_token
from core.db import get_db
from core.s3 import presign as _presign, s3_put as _s3_put

router = APIRouter(prefix='/api/clients', tags=['clients'])

# ── CLIENTS (anunciantes administrados por el tenant) ─────────────────────────
class CreateClientRequest(BaseModel):
    name:          str
    industry:      Optional[str] = None
    country:       Optional[str] = 'HN'
    logo_url:      Optional[str] = None
    rtn:           Optional[str] = None   # RTN de la empresa (ID tributario)
    company_email: Optional[str] = None
    address:       Optional[str] = None
    contact_name:  Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None

class UpdateClientRequest(BaseModel):
    name:          Optional[str]  = None
    industry:      Optional[str]  = None
    country:       Optional[str]  = None
    logo_url:      Optional[str]  = None
    active:        Optional[bool] = None
    rtn:           Optional[str]  = None
    company_email: Optional[str]  = None
    address:       Optional[str]  = None
    contact_name:  Optional[str]  = None
    contact_phone: Optional[str]  = None
    contact_email: Optional[str]  = None

@router.get('')
def list_clients(active: Optional[bool] = None, ctx = Depends(verify_token)):
    """Anunciantes del tenant, con conteo de campañas."""
    sql = '''
        SELECT cl.id, cl.name, cl.industry, cl.country, cl.logo_url, cl.active,
               cl.created_at, COUNT(DISTINCT ca.id) AS total_campaigns
        FROM clients cl
        LEFT JOIN campaigns ca ON ca.client_id = cl.id
        WHERE cl.tenant_id = %s
    '''
    params = [ctx['tenant_id']]
    if active is not None:
        sql += ' AND cl.active = %s'; params.append(active)
    sql += ' GROUP BY cl.id ORDER BY cl.name'
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

@router.post('', status_code=201)
def create_client(body: CreateClientRequest, ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM clients WHERE tenant_id = %s AND lower(name) = lower(%s)',
                        (ctx['tenant_id'], body.name.strip()))
            if cur.fetchone():
                raise HTTPException(409, 'Ya existe un anunciante con ese nombre')
            cur.execute('''
                INSERT INTO clients (tenant_id, name, industry, country, logo_url,
                                     rtn, company_email, address,
                                     contact_name, contact_phone, contact_email)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, name, industry, country, logo_url, active, created_at,
                          rtn, company_email, address,
                          contact_name, contact_phone, contact_email
            ''', (ctx['tenant_id'], body.name.strip(), body.industry,
                  body.country or 'HN', body.logo_url,
                  body.rtn, body.company_email, body.address,
                  body.contact_name, body.contact_phone, body.contact_email))
            return dict(cur.fetchone())

@router.get('/{client_id}')
def get_client(client_id: str, ctx = Depends(verify_token)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''
                SELECT cl.id, cl.name, cl.industry, cl.country, cl.logo_url, cl.active,
                       cl.created_at,
                       cl.rtn, cl.company_email, cl.address,
                       cl.contact_name, cl.contact_phone, cl.contact_email,
                       COUNT(DISTINCT ca.id) AS total_campaigns,
                       COUNT(fd.id)          AS total_airings
                FROM clients cl
                LEFT JOIN campaigns ca ON ca.client_id = cl.id
                LEFT JOIN fingerprint_detections fd ON fd.campaign_id = ca.id AND fd.deleted_at IS NULL AND (fd.confidence_score IS NULL OR fd.confidence_score >= 0.7)
                WHERE cl.id = %s AND cl.tenant_id = %s
                GROUP BY cl.id
            ''', (client_id, ctx['tenant_id']))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, 'Anunciante no encontrado')
            return dict(row)

@router.patch('/{client_id}')
def update_client(client_id: str, body: UpdateClientRequest, ctx = Depends(verify_token)):
    fields, params = [], []
    if body.name is not None:     fields.append('name = %s');     params.append(body.name.strip())
    if body.industry is not None: fields.append('industry = %s'); params.append(body.industry)
    if body.country is not None:  fields.append('country = %s');  params.append(body.country)
    if body.logo_url is not None: fields.append('logo_url = %s'); params.append(body.logo_url)
    if body.active is not None:   fields.append('active = %s');   params.append(body.active)
    if body.rtn is not None:           fields.append('rtn = %s');           params.append(body.rtn)
    if body.company_email is not None: fields.append('company_email = %s'); params.append(body.company_email)
    if body.address is not None:       fields.append('address = %s');       params.append(body.address)
    if body.contact_name is not None:  fields.append('contact_name = %s');  params.append(body.contact_name)
    if body.contact_phone is not None: fields.append('contact_phone = %s'); params.append(body.contact_phone)
    if body.contact_email is not None: fields.append('contact_email = %s'); params.append(body.contact_email)
    if not fields:
        raise HTTPException(400, 'Nada que actualizar')
    fields.append('updated_at = NOW()')
    params += [client_id, ctx['tenant_id']]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE clients SET ' + ', '.join(fields) +
                        ' WHERE id = %s AND tenant_id = %s'
                        ' RETURNING id, name, industry, country, logo_url, active,'
                        ' rtn, company_email, address, contact_name, contact_phone, contact_email', params)
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, 'Anunciante no encontrado')
            return dict(row)

@router.delete('/{client_id}')
def delete_client(client_id: str, ctx = Depends(verify_token)):
    """Archiva el anunciante (soft-delete: active=false). NO borra: conserva campañas,
    anuncios, detecciones y evidencia para auditoría. Reactivar con PATCH active=true."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE clients SET active = false, updated_at = NOW() '
                        'WHERE id = %s AND tenant_id = %s', (client_id, ctx['tenant_id']))
            if cur.rowcount == 0:
                raise HTTPException(404, 'Anunciante no encontrado')
    return {'ok': True, 'archived': True}

@router.post('/{client_id}/logo')
def upload_client_logo(client_id: str, file: UploadFile = File(...), ctx = Depends(verify_token)):
    """Sube y normaliza el logo del cliente: lo redimensiona a máx 512px (lado largo),
    re-encodea a PNG y lo guarda en S3. Evita logos enormes que inflen storage / rompan
    el render de reportes. Guarda la key en clients.logo_url."""
    from PIL import Image  # lazy: solo se necesita aquí
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM clients WHERE id = %s AND tenant_id = %s',
                        (client_id, ctx['tenant_id']))
            if not cur.fetchone():
                raise HTTPException(404, 'Anunciante no encontrado')
    raw = file.file.read()
    if not raw:
        raise HTTPException(400, 'Archivo vacío')
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, 'Imagen demasiado grande (máx 10MB)')
    try:
        img = Image.open(io.BytesIO(raw)); img.load()
    except Exception:
        raise HTTPException(400, 'Archivo de imagen inválido')
    img = img.convert('RGBA')
    MAX = 512
    if max(img.size) > MAX:
        img.thumbnail((MAX, MAX), Image.LANCZOS)
    out = io.BytesIO(); img.save(out, format='PNG', optimize=True)
    key = 'logos/%s/%s.png' % (ctx['tenant_id'], client_id)
    _s3_put(key, out.getvalue(), 'image/png')
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE clients SET logo_url = %s, updated_at = NOW() '
                        'WHERE id = %s AND tenant_id = %s', (key, client_id, ctx['tenant_id']))
    return {'ok': True, 'logo_url': key, 'preview_url': _presign(key)}

