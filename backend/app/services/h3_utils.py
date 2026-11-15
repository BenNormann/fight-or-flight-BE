"""H3 helpers. Cells are stored as hex strings (e.g. '8929a1d7533ffff')."""
import math
from functools import lru_cache

import h3
from flask import current_app

EARTH_R_KM = 6371.0088


def circle_ring(lat, lng, radius_km, n=128):
    """Geodesic circle as a list of (lat, lng)."""
    d = radius_km / EARTH_R_KM
    lat1, lng1 = math.radians(lat), math.radians(lng)
    pts = []
    for i in range(n):
        brg = 2 * math.pi * i / n
        lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
        lng2 = lng1 + math.atan2(
            math.sin(brg) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2)
        )
        pts.append((math.degrees(lat2), math.degrees(lng2)))
    return pts


@lru_cache(maxsize=8)
def _cells(lat, lng, radius_km, res):
    return tuple(sorted(h3.polygon_to_cells(h3.LatLngPoly(circle_ring(lat, lng, radius_km)), res)))


@lru_cache(maxsize=8)
def _area_cells(lat, lng, radius_km, res, area_res):
    return tuple(sorted({h3.cell_to_parent(c, area_res) for c in _cells(lat, lng, radius_km, res)}))


def _area_args():
    c = current_app.config
    return c["USC_LAT"], c["USC_LNG"], c["RADIUS_KM"]


def study_cells():
    """All fine-resolution cells covering the study circle."""
    return _cells(*_area_args(), current_app.config["H3_RES"])


def area_cells():
    """Coarse parent cells covering the study area (used for sun/weather)."""
    c = current_app.config
    return _area_cells(*_area_args(), c["H3_RES"], c["H3_AREA_RES"])


def study_bbox():
    """(min_lat, min_lng, max_lat, max_lng) of the study circle."""
    ring = circle_ring(*_area_args())
    lats, lngs = [p[0] for p in ring], [p[1] for p in ring]
    return min(lats), min(lngs), max(lats), max(lngs)


def to_cell(lat, lng, res=None):
    return h3.latlng_to_cell(lat, lng, res or current_app.config["H3_RES"])


def in_study_area(lat, lng):
    return to_cell(lat, lng) in _study_set()


def _study_set():
    return set(study_cells())


def cell_polygon_wkt(cell):
    ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
    ring.append(ring[0])
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in ring) + "))"


def cell_centroid_wkt(cell):
    lat, lng = h3.cell_to_latlng(cell)
    return f"POINT({lng} {lat})"


def line_cells(line, length_m, step_m=50.0, res=None):
    """Ordered, de-duplicated cells a shapely LineString (lng/lat) passes through,
    sampled every ~step_m metres."""
    res = res or current_app.config["H3_RES"]
    n = max(2, int(math.ceil(length_m / step_m)) + 1)
    cells = []
    for i in range(n):
        p = line.interpolate(i / (n - 1), normalized=True)
        c = h3.latlng_to_cell(p.y, p.x, res)
        if not cells or cells[-1] != c:
            cells.append(c)
    return list(dict.fromkeys(cells))
