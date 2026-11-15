"""Geography helpers: the H3 cell universe, blue-light phones, darkness."""
import csv
import datetime as dt
import math
from functools import lru_cache
from zoneinfo import ZoneInfo

import h3
from astral import LocationInfo
from astral.sun import sun

from . import config

# TODO(data): replace with the real USC DPS Emergency Blue Light Phone
# coordinates (dps.usc.edu). These are APPROXIMATE campus landmark points used
# as stand-ins so the distance feature exists end to end. Drop a CSV at
# data/bluelight_phones.csv with header `lat,lon,name` and it wins.
PLACEHOLDER_PHONES = [
    (34.0202, -118.2837, "Doheny Library (approx)"),
    (34.0219, -118.2829, "Leavey Library (approx)"),
    (34.0204, -118.2863, "Tutor Campus Center (approx)"),
    (34.0254, -118.2851, "USC Village (approx)"),
    (34.0189, -118.2878, "Exposition/Watt Way gate (approx)"),
    (34.0187, -118.2905, "Parkside (approx)"),
    (34.0219, -118.2803, "Figueroa/Jefferson gate (approx)"),
    (34.0236, -118.2880, "Vermont/Jefferson gate (approx)"),
]


@lru_cache(maxsize=1)
def bluelight_phones() -> list[tuple[float, float, str]]:
    p = config.BLUELIGHT_CSV
    if p.exists():
        with p.open(newline="") as f:
            rows = [(float(r["lat"]), float(r["lon"]), r.get("name", ""))
                    for r in csv.DictReader(f) if r.get("lat")]
        if rows:
            return rows
    return PLACEHOLDER_PHONES


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def dist_to_bluelight_m(lat: float, lon: float) -> float:
    return min(haversine_m(lat, lon, a, b) for a, b, _ in bluelight_phones())


def in_radius(lat: float, lon: float) -> bool:
    return haversine_m(lat, lon, *config.CENTER) <= config.RADIUS_M


@lru_cache(maxsize=1)
def area_cells() -> list[str]:
    """Every H3 cell whose centre falls inside the RADIUS_MILES circle, sorted."""
    lat0, lon0 = config.CENTER
    ring = []
    for k in range(128):  # 128-gon approximation of the circle
        a = 2 * math.pi * k / 128
        dlat = config.RADIUS_M * math.cos(a) / 111_320.0
        dlon = config.RADIUS_M * math.sin(a) / (111_320.0 * math.cos(math.radians(lat0)))
        ring.append((lat0 + dlat, lon0 + dlon))
    return sorted(h3.polygon_to_cells(h3.LatLngPoly(ring), config.H3_RES))


@lru_cache(maxsize=4096)
def twilight_hours(day: dt.date) -> tuple[float, float]:
    """(civil dawn, civil dusk) as fractional local hours for LA on `day`."""
    tz = ZoneInfo(config.TIMEZONE)
    loc = LocationInfo("LA", "USA", config.TIMEZONE, config.LAT, config.LON)
    s = sun(loc.observer, date=day, tzinfo=tz)
    dawn, dusk = s["dawn"], s["dusk"]
    return dawn.hour + dawn.minute / 60, dusk.hour + dusk.minute / 60


def is_dark(day: dt.date, hour: int) -> int:
    """1 if the middle of this clock hour is before civil dawn or after civil dusk."""
    dawn, dusk = twilight_hours(day)
    mid = hour + 0.5
    return int(mid < dawn or mid > dusk)


def season(month: int) -> int:
    """0=winter (DJF), 1=spring, 2=summer, 3=fall."""
    return (month % 12) // 3
