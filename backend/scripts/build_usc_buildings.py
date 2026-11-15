"""Build data/places/usc_buildings.geojson: USC University Park campus buildings with their
official 3-letter codes, a display name, search aliases, a point and the entrances.

    python -m scripts.build_usc_buildings [--out PATH] [--no-osm]

Sources (both public, no key):
  * USC Facilities Planning & Management (FPM) GIS: the `Building` feature service (code, name,
    address, type, centroid LAT/LON) and `BuildingEntrance` (entrance points by building code).
    https://fpmgis.usc.edu/server/rest/services/USC_FPM/
  * OpenStreetMap via Overpass (optional): everyday names for the same buildings ("Leavey Library"
    for "THOMAS & DOROTHY LEAVEY LIBRARY"), matched by code or by the OSM building's centre falling
    inside the FPM footprint. (c) OpenStreetMap contributors, ODbL.

Run it again when buildings change and commit the output; scripts/load_places.py reads the file.
"""
import argparse
import json
import os
import re
import time

import requests
from shapely.geometry import Point, shape

FPM = "https://fpmgis.usc.edu/server/rest/services/USC_FPM"
OVERPASS = "https://overpass-api.de/api/interpreter"
UA = {"User-Agent": "usc-safe-route-hackathon"}
# USC University Park campus in OSM (relation) + USC Village (way)
OSM_QUERY = """[out:json][timeout:120];
(relation(18383936);way(541642577);)->.c;.c map_to_area->.a;
(way["building"]["name"](area.a);relation["building"]["name"](area.a););out tags center;"""

KEEP_SITES = {"UPC", "UPCOFF"}               # owned, on or next to campus
DROP_CATEGORIES = {"Infrastructure", "Trailer", "Gate"}

# Well-known names students use that neither source has (legacy names, nicknames).
KNOWN_ALIASES = {
    "DMC": ["VKC", "Von KleinSmid Center"],  # renamed from Von KleinSmid Center (VKC) in 2020
    "DML": ["Doheny Library", "Doheny"],
    "LVL": ["Leavey Library", "Leavey"],
    "TCC": ["Tutor Campus Center", "Ronald Tutor Campus Center"],
    "LRC": ["Lyon Center", "Lyon Rec Center"],
    "THH": ["Taper Hall"],
    "STU": ["Student Union"],
    "ADM": ["Bovard", "Bovard Auditorium"],
    "BKS": ["USC Bookstore"],
    "ESH": ["Student Health Center", "Engemann"],
    "SSL": ["Seaver Library"],
    "GEC": ["Galen Center"],
    "JFF": ["Fertitta Hall"],
    "HOH": ["Hoffman Hall"],
    "ASC": ["Annenberg"],
    "UAC": ["Uytengsu"],
}

_SMALL = {"of", "and", "for", "the", "at", "in", "on", "a", "&"}
_UPPER = {"USC", "II", "III", "IV", "SCA", "SAS", "ECE", "LA", "CIRM", "PCIP", "I"}
# FPM names that are truncated or garbled in the source; replaced by the OSM name.
NAME_OVERRIDES = {"DCC": "Davidson Continuing Education Center"}
# Too generic to show that an OSM name and an FPM name are the same building.
_GENERIC = {"hall", "building", "center", "centre", "college", "residential", "school", "usc", "university",
            "house", "memorial", "and", "the", "for", "of", "science", "sciences", "engineering", "research",
            "institute", "apartments", "tower", "library", "structure", "parking", "street"}


def _word(w: str, first: bool) -> str:
    if not w:
        return w
    core = w.strip("().,'")
    if core.upper() in _UPPER:
        return w.upper()
    if core.lower() in _SMALL and not first:
        return w.lower()
    out = "-".join(re.sub(r"[a-z]", lambda m: m.group(0).upper(), p.lower(), count=1) for p in w.split("-"))
    out = re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), out)
    out = re.sub(r"([A-Z]\.)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)  # J.douglas -> J.Douglas
    out = re.sub(r"'S\b", "'s", out)
    return out


def title_case(name: str) -> str:
    """'THOMAS & DOROTHY LEAVEY LIBRARY' -> 'Thomas & Dorothy Leavey Library'."""
    name = re.sub(r"\s+", " ", name.replace("�", " ")).strip()
    words = name.split(" ")
    return " ".join(_word(w, i == 0) for i, w in enumerate(words))


def _tokens(s) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if len(w) >= 4 and w not in _GENERIC}


def _same_building(fpm_name: str, osm_tags: dict) -> bool:
    """An OSM building whose centre lies in the FPM footprint names the same building if it shares a
    distinctive word with it, or is a plain campus building (not a church/library/shop inside it)."""
    names = " ".join(osm_tags.get(k) or "" for k in ("name", "short_name", "official_name", "alt_name"))
    if _tokens(fpm_name) & _tokens(names):
        return True
    return not osm_tags.get("amenity") and not osm_tags.get("shop") and osm_tags.get("building") in (
        "university", "college", "dormitory", "yes")


def _get(url, **params):
    for attempt in range(5):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=120)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)


def fetch_fpm():
    bld = _get(f"{FPM}/Building/FeatureServer/0/query", where="1=1", outSR=4326, f="geojson",
               outFields="BLD_CODE,BLDG_NAME,BLDG_ADDR,USCBuildingCategory,USCBuildingType,GridMapCampus,"
                         "LAT,LON,DisplayAsBuilding,YEAR_DEMO,YEAR_SOLD,SITE,ZipCode")
    ent = _get(f"{FPM}/BuildingEntrance/FeatureServer/0/query", where="1=1", outSR=4326, f="geojson",
               outFields="BLD_CODE,TYPE,ACCESS_LVL,WHEEL_CHAIR_ACCESS")
    return bld["features"], ent["features"]


def fetch_osm():
    for attempt in range(5):
        try:
            r = requests.post(OVERPASS, data={"data": OSM_QUERY}, headers=UA, timeout=180)
            r.raise_for_status()
            return r.json()["elements"]
        except (requests.RequestException, ValueError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 5)


def subcategory(p) -> str:
    name = (p.get("BLDG_NAME") or "").upper()
    if "LIBRARY" in name:
        return "library"
    if p.get("USCBuildingCategory") == "Parking":
        return "parking_structure"
    if p.get("USCBuildingCategory") == "Field" or "STADIUM" in name or "FIELD" in name:
        return "athletic_facility"
    if p.get("USCBuildingType") == "RESIDENCE":
        return "residence_hall"
    return "university_building"


CATEGORY_BY_SUB = {"library": "study", "parking_structure": "parking", "athletic_facility": "campus",
                   "residence_hall": "campus", "university_building": "campus"}


def build(bld_features, ent_features, osm_elements=()):
    entrances = {}
    for f in ent_features:
        p, g = f.get("properties") or {}, f.get("geometry")
        code = (p.get("BLD_CODE") or "").strip()
        if code and g and g.get("coordinates"):
            lng, lat = g["coordinates"][:2]
            entrances.setdefault(code, []).append([round(lng, 6), round(lat, 6)])

    osm = []
    for e in osm_elements or []:
        t, c = e.get("tags") or {}, e.get("center")
        if t.get("name") and c:
            osm.append((t, Point(c["lon"], c["lat"])))

    out, seen = [], set()
    for f in bld_features:
        p = f.get("properties") or {}
        code = (p.get("BLD_CODE") or "").strip().upper()
        if (not code or code in seen or p.get("SITE") not in KEEP_SITES or p.get("YEAR_DEMO") or p.get("YEAR_SOLD")
                or p.get("USCBuildingCategory") in DROP_CATEGORIES or p.get("DisplayAsBuilding") == "NO"
                or not p.get("BLDG_NAME")):
            continue
        seen.add(code)
        poly = shape(f["geometry"]) if f.get("geometry") else None
        if p.get("LAT") and p.get("LON"):
            lat, lng = float(p["LAT"]), float(p["LON"])
        elif poly is not None:
            pt = poly.representative_point()
            lat, lng = pt.y, pt.x
        else:
            continue
        name = NAME_OVERRIDES.get(code) or title_case(p["BLDG_NAME"])
        aliases = [code]
        for t, c in osm:
            codes = {(t.get(k) or "").strip().upper() for k in ("short_name", "ref")}
            if code in codes or (poly is not None and poly.contains(c) and _same_building(p["BLDG_NAME"], t)):
                for k in ("name", "short_name", "official_name", "alt_name"):
                    for v in (t.get(k) or "").split(";"):
                        aliases.append(v.strip())
        aliases += KNOWN_ALIASES.get(code, [])
        if code in NAME_OVERRIDES:
            aliases.append(title_case(p["BLDG_NAME"]))
        seen_alias, clean = {name.lower()}, []
        for a in aliases:
            if a and a.lower() not in seen_alias:
                seen_alias.add(a.lower())
                clean.append(a)
        sub = subcategory(p)
        addr = (p.get("BLDG_ADDR") or "").strip()
        out.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lng, 6), round(lat, 6)]},
            "properties": {
                "code": code,
                "name": name,
                "aliases": clean,
                "category": CATEGORY_BY_SUB[sub],
                "subcategory": sub,
                "address": (title_case(addr) + (f", Los Angeles, CA {p['ZipCode']}" if p.get("ZipCode") else ""))
                           if addr else None,
                "usc_type": p.get("USCBuildingType"),
                "entrances": entrances.get(code, []),
                "source": "usc-fpm-gis" + ("+osm" if len(clean) > 1 + len(KNOWN_ALIASES.get(code, [])) else ""),
            },
        })
    out.sort(key=lambda x: x["properties"]["code"])
    return {"type": "FeatureCollection", "features": out}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(here, "..", "..", "data", "places", "usc_buildings.geojson"))
    ap.add_argument("--no-osm", action="store_true", help="skip the OpenStreetMap name aliases")
    args = ap.parse_args()
    bld, ent = fetch_fpm()
    osm = [] if args.no_osm else fetch_osm()
    fc = build(bld, ent, osm)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(fc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    n = len(fc["features"])
    print(f"wrote {n} buildings to {os.path.abspath(args.out)} "
          f"({sum(1 for x in fc['features'] if x['properties']['entrances'])} with entrances)")


if __name__ == "__main__":
    main()
