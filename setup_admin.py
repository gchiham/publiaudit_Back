"""Script para setear la contraseña del usuario admin inicial."""
import os, bcrypt, psycopg2, getpass
from pathlib import Path

# Credenciales desde /etc/media-app.env (no hardcodear)
_env = Path('/etc/media-app.env')
if _env.exists():
    for _l in _env.read_text().splitlines():
        if '=' in _l and not _l.lstrip().startswith('#'):
            _k, _, _v = _l.partition('=')
            os.environ.setdefault(_k.strip(), _v.strip())
PG_HOST = os.environ['PG_HOST']
PG_PORT = int(os.environ.get('PG_PORT', '25060'))
PG_DB   = os.environ.get('PG_DB', 'destroyer_db')
PG_USER = os.environ.get('PG_USER', 'destroyer')
PG_PASS = os.environ['PG_PASS']

email    = input('Email del admin [admin@mediaai.hn]: ').strip() or 'admin@mediaai.hn'
password = getpass.getpass('Nueva contraseña: ')
confirm  = getpass.getpass('Confirmar contraseña: ')

if password != confirm:
    print('ERROR: contraseñas no coinciden')
    exit(1)

hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

conn = psycopg2.connect(
    host=PG_HOST, port=PG_PORT, dbname=PG_DB,
    user=PG_USER, password=PG_PASS,
    connect_timeout=10, sslmode='require',
)
with conn, conn.cursor() as cur:
    cur.execute('UPDATE users SET password_hash = %s WHERE email = %s', (hashed, email))
    if cur.rowcount == 0:
        print(f'ERROR: usuario {email} no encontrado')
    else:
        print(f'✅ Contraseña actualizada para {email}')
conn.close()
