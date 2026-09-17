"""Date/time parsing and formatting helpers for the event system.

Accepts Thai and English natural-language input (used by !event create) and
formats human-readable countdowns. All event times are treated as Asia/Bangkok
local time (UTC+7), since the bot serves a school in Thailand.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

BANGKOK_TZ = timezone(timedelta(hours=7))

_WEEKDAYS_EN = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_WEEKDAYS_TH = {
    "จันทร์": 0,
    "อังคาร": 1,
    "พุธ": 2,
    "พฤหัสบดี": 3,
    "พฤหัส": 3,
    "ศุกร์": 4,
    "เสาร์": 5,
    "อาทิตย์": 6,
}


def today_bangkok() -> date:
    return datetime.now(BANGKOK_TZ).date()


def now_bangkok() -> datetime:
    return datetime.now(BANGKOK_TZ)


def _next_weekday(today: date, target_weekday: int) -> date:
    days_ahead = (target_weekday - today.weekday() + 7) % 7
    days_ahead = days_ahead or 7  # "next Monday" always means a future Monday, not today
    return today + timedelta(days=days_ahead)


def parse_event_date(raw: str) -> Optional[date]:
    """Parse DD/MM/YYYY, 'today'/'tomorrow' (Thai or English), or 'next <weekday>'."""
    text = raw.strip().lower()
    today = today_bangkok()

    if text in ("today", "วันนี้"):
        return today
    if text in ("tomorrow", "พรุ่งนี้"):
        return today + timedelta(days=1)

    match = re.match(r"^next\s+([a-z]+)$", text)
    if match and match.group(1) in _WEEKDAYS_EN:
        return _next_weekday(today, _WEEKDAYS_EN[match.group(1)])

    for name, idx in _WEEKDAYS_TH.items():
        if name in text and ("หน้า" in text or "next" in text):
            return _next_weekday(today, idx)

    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", text)
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    return None


def parse_event_time(raw: str) -> Optional[tuple[int, int]]:
    """Parse HH:MM (24-hour)."""
    match = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", raw.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def combine(event_date: date, hour: int, minute: int) -> datetime:
    return datetime(event_date.year, event_date.month, event_date.day, hour, minute, tzinfo=BANGKOK_TZ)


def format_remaining(target: datetime) -> str:
    """Human-readable Thai countdown string, e.g. 'อีก 2 วัน 3 ชั่วโมง'."""
    delta = target - now_bangkok()
    if delta.total_seconds() <= 0:
        return "กิจกรรมนี้ผ่านไปแล้ว"

    days, remainder = divmod(int(delta.total_seconds()), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60

    parts = []
    if days:
        parts.append(f"{days} วัน")
    if hours:
        parts.append(f"{hours} ชั่วโมง")
    if not days and minutes:
        parts.append(f"{minutes} นาที")
    if not parts:
        parts.append("อีกไม่ถึงนาที")
    return "อีก " + " ".join(parts)
