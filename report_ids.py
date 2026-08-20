"""PubliAudit — IDs presentables para reportes y evidencias.

- report_ref(created_at, token): folio LEGIBLE del reporte. Es una ETIQUETA,
  no una llave: la seguridad/acceso la sigue dando el token. Único porque hereda
  la unicidad del token. No filtra cuántos reportes existen.

- evd(detection_id) / evd_decode(code): referencia ESTABLE, GLOBAL e INMUTABLE
  por detección. Derivada de fingerprint_detections.id (bigint, nunca reusado)
  mediante un cifrado Feistel reversible => el mismo airing siempre da el mismo
  EVD en cualquier reporte, es VERIFICABLE (decodable) sin columna nueva, y NO
  filtra el volumen de detecciones (códigos no secuenciales).

IMPORTANTE: EVD_SALT NO debe cambiar una vez en producción — cambiarlo re-mapea
todos los EVD ya emitidos. Definir en /etc/publiaudit.env (env EVD_SALT).
"""
import os, hashlib
from datetime import datetime, timezone

EVD_SALT = os.environ.get("EVD_SALT", "publiaudit-evd-v1").encode()

# Alfabeto de 32 chars sin caracteres ambiguos (0/O/1/I) — robusto al teclearse.
_AL      = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_HALF    = 16
_MASK    = (1 << _HALF) - 1
_ROUNDS  = 4
_CODE_LEN = 7          # 7 * 5 bits = 35 bits >= dominio de 32 bits


def _F(r: int, i: int) -> int:
    h = hashlib.blake2b(bytes([i]) + EVD_SALT + r.to_bytes(2, "big"), digest_size=2).digest()
    return int.from_bytes(h, "big") & _MASK


def _feistel_enc(n: int) -> int:
    l, r = (n >> _HALF) & _MASK, n & _MASK
    for i in range(_ROUNDS):
        l, r = r, l ^ _F(r, i)
    return (l << _HALF) | r


def _feistel_dec(x: int) -> int:
    l, r = (x >> _HALF) & _MASK, x & _MASK
    for i in reversed(range(_ROUNDS)):
        r, l = l, r ^ _F(l, i)
    return (l << _HALF) | r


def _b32(v: int) -> str:
    s = ""
    for _ in range(_CODE_LEN):
        s = _AL[v & 31] + s
        v >>= 5
    return s


def _unb32(s: str) -> int:
    v = 0
    for c in s:
        v = (v << 5) | _AL.index(c)
    return v


def evd(detection_id: int) -> str:
    """fingerprint_detections.id -> 'EVD-XXXXXXX' (estable, no secuencial)."""
    return "EVD-" + _b32(_feistel_enc(int(detection_id)))


def evd_decode(code: str):
    """'EVD-XXXXXXX' -> detection_id (int), o None si malformado."""
    try:
        raw = (code or "").strip().upper()
        if raw.startswith("EVD-"):
            raw = raw[4:]
        if len(raw) != _CODE_LEN or any(c not in _AL for c in raw):
            return None
        return _feistel_dec(_unb32(raw))
    except Exception:
        return None


def report_ref(created_at=None, token: str = "") -> str:
    """Folio legible del reporte: 'PA-AAAA-XXXXXX'. Etiqueta, no llave."""
    year   = (created_at or datetime.now(timezone.utc)).year
    suffix = (token or "")[:6].upper() or "000000"
    return f"PA-{year}-{suffix}"
