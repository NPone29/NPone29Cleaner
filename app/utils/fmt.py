"""Petites fonctions de formatage (tailles, dates, durées) en français."""

from __future__ import annotations

from datetime import datetime, timedelta

_UNITS = ["o", "Ko", "Mo", "Go", "To", "Po"]


def human_size(num_bytes: int | float | None) -> str:
    """1536 -> '1,5 Ko'. Utilise des puissances de 1024 comme l'Explorateur Windows."""
    if num_bytes is None:
        return "—"
    size = float(num_bytes)
    if size < 0:
        size = 0.0
    unit = 0
    while size >= 1024 and unit < len(_UNITS) - 1:
        size /= 1024
        unit += 1
    if unit == 0:
        return f"{int(size)} {_UNITS[0]}"
    text = f"{size:.1f}" if size < 100 else f"{size:.0f}"
    return f"{text.replace('.', ',')} {_UNITS[unit]}"


def format_date(value: datetime | None) -> str:
    return value.strftime("%d/%m/%Y") if value else "—"


def format_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} j {hours} h"
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min"


def parse_wmi_datetime(value: str | None) -> datetime | None:
    """'20230512000000.******+000' (format CIM/WMI) -> datetime(2023, 5, 12)."""
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.strptime(text[:14], "%Y%m%d%H%M%S")
    except ValueError:
        try:
            return datetime.strptime(text[:8], "%Y%m%d")
        except ValueError:
            return None
