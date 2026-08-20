import os
import secrets

from fastapi import HTTPException, Query

# ── Auth de paneles internos de operación (cobertura / mediadev) ──────────────
# Estos endpoints exponen infra global (costos AWS, gateways, Destroyer, cobertura)
# y NO son datos de tenant: van protegidos por un token compartido que el front de
# cobertura ya envía como ?token=… El servidor antes lo IGNORABA (endpoints abiertos).
# Fail-closed: si COBERTURA_TOKEN no está en el entorno se niega el acceso, pero NO
# se cae el resto del API.
_COBERTURA_TOKEN = os.environ.get('COBERTURA_TOKEN', '').strip()

def verify_cobertura_token(token: str = Query(None)):
    """Valida el token compartido de los paneles internos. Comparación en tiempo
    constante. Sin default inseguro: si no hay token configurado, deniega."""
    if not _COBERTURA_TOKEN:
        raise HTTPException(status_code=503, detail='Panel de operación no configurado')
    if not token or not secrets.compare_digest(token, _COBERTURA_TOKEN):
        raise HTTPException(status_code=403, detail='Token inválido o ausente')
    return True
