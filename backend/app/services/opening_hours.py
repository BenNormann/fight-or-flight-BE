"""Opening hours: parse Google Maps `open_hours` text into minute ranges, and answer "is it open?".

Stored format (places.hours):
    {"0": [[open_min, close_min], ...], ..., "6": [...]}
Monday = "0". Minutes are from local (America/Los_Angeles) midnight. A range that runs past
midnight keeps close_min > 1440 on the day it opened (e.g. 6 PM-2 AM = [1080, 1560]).
Open 24 hours = [[0, 1440]]; closed all day = []. A day Google gave no text for is left out.
Unknown hours = None.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_MIN = 1440

# Google uses U+202F (narrow no-break space) before AM/PM and U+2013 (en dash) between times.
_SPACES = re.compile(r"[   \s]+")
_DASHES = re.compile(r"\s*[‐-―−-]\s*")
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?"
_RANGE = re.compile(rf"^{_TIME}\s*(?:-|to)\s*{_TIME}$", re.I)


def _clean(s: str) -> str:
    s = _SPACES.sub(" ", str(s)).strip()
    return _DASHES.sub("-", s)


def _minutes(h, m, mer) -> int | None:
    h, m = int(h), int(m or 0)
    if m > 59:
        return None
    if mer:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if mer.lower().startswith("p") else 0)
    elif h > 24:
        return None
    return h * 60 + m


def parse_range(text: str) -> list[list[int]] | None:
    """One Google hours entry -> list of [open, close] ranges ([] = closed); None if unparseable.

    '11 AM-9 PM' -> [[660, 1260]]; '6 PM-2 AM' -> [[1080, 1560]]; 'Open 24 hours' -> [[0, 1440]];
    'Closed' -> []. A missing AM/PM on the opening time takes the closing time's ('9-11 AM')."""
    s = _clean(text).lower()
    if not s:
        return None
    if s in ("closed", "closed all day"):
        return []
    if s in ("open 24 hours", "24 hours", "open 24/7", "24/7"):
        return [[0, DAY_MIN]]
    out = []
    for part in re.split(r"\s*,\s*", s):
        m = _RANGE.match(part)
        if not m:
            return None
        h1, m1, mer1, h2, m2, mer2 = m.groups()
        close = _minutes(h2, m2, mer2)
        opening = _minutes(h1, m1, mer1 or mer2)
        if opening is None or close is None:
            return None
        if not mer1 and opening > close >= 0 and opening >= 720:  # "11-2 PM" means 11 AM, not 11 PM
            opening -= 720
        if close <= opening:  # runs past midnight ("6 PM-2 AM", "6 AM-12 AM", "12 AM-12 AM")
            close += DAY_MIN
        out.append([opening, close])
    return out


def parse_hours(open_hours) -> dict | None:
    """Google `open_hours` ({"Monday": ["8 AM-10 PM"], ...}) -> stored format, or None if unknown."""
    if not isinstance(open_hours, dict) or not open_hours:
        return None
    hours = {}
    for day, entries in open_hours.items():
        key = str(day).strip().lower()
        if key not in DAYS:
            continue
        if isinstance(entries, str):
            entries = [entries]
        ranges, ok = [], False
        for e in entries or []:
            r = parse_range(e)
            if r is None:
                continue
            ok = True
            ranges.extend(r)
        if ok:
            hours[str(DAYS.index(key))] = sorted(ranges)
    return {k: hours[k] for k in sorted(hours)} or None


def is_open(hours: dict | None, when: datetime) -> bool | None:
    """Whether a place with `hours` is open at `when` (tz-aware; naive is taken as LA local time).
    Counts the previous day's ranges that run past midnight. None when hours are unknown."""
    if not hours:
        return None
    local = when.astimezone(TZ) if when.tzinfo else when.replace(tzinfo=TZ)
    dow = local.weekday()
    minute = local.hour * 60 + local.minute
    today = hours.get(str(dow))
    yesterday = hours.get(str((dow - 1) % 7))
    if yesterday and any(o <= minute + DAY_MIN < c for o, c in yesterday):
        return True
    if today is None:
        return None
    return any(o <= minute < c for o, c in today)
