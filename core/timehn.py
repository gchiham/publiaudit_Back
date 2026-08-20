"""Helpers de zona horaria Honduras (UTC-6, sin DST). Compartidos entre el
panel de Cobertura y el dominio de Reportes/Evidence Portal."""
from datetime import datetime, timedelta, timezone

_TGU = timezone(timedelta(hours=-6))


def to_hn(v):
    """timestamptz (UTC u otro) -> ISO en hora Honduras (-06:00). None-safe."""
    if not v or not hasattr(v, 'astimezone'):
        return v
    if getattr(v, 'tzinfo', None) is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(_TGU).isoformat()


def now_hn():
    return datetime.now(_TGU)


def month_start_utc():
    m = now_hn().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return m.astimezone(timezone.utc)


def hn_iso(v):
    """datetime naive en hora Honduras -> ISO con offset -06:00 (HN sin DST).
    Si ya es tz-aware (UTC), lo deja igual."""
    if not v or not hasattr(v, "isoformat"):
        return v
    if getattr(v, "tzinfo", None) is None:
        v = v.replace(tzinfo=_TGU)
    return v.isoformat()


def pdf_to_hn(dt):
    if not dt:
        return dt
    return dt.astimezone(_TGU) if getattr(dt, 'tzinfo', None) is not None else dt.replace(tzinfo=_TGU)
