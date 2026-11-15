"""Build data/usc/usc_places.json: USC building codes and named outdoor spaces -> coordinates.

    pip install osmium shapely
    python -m scripts.usc_data places [--pbf data/work/study_area.osm.pbf]

Source is OpenStreetMap (the study-area extract made by backend/scripts/fetch_osm.sh). OSM tags
many USC buildings with their registrar code as `short_name`/`ref` (THH, SGM, ...); the rest are
matched by name below. The output is committed, so the other scrapers don't need osmium.
Codes that can't be matched are listed under `unresolved` so they can be added by hand.
"""
import argparse
import os
import re

from .common import DATA, PLACES_FILE, now_iso, write_json

# USC campus (UPC + USC Village) bounding box.
S, W, N, E = 34.0120, -118.2950, 34.0300, -118.2760

# Registrar building codes that OSM doesn't carry as short_name/ref -> OSM name (regex).
CODE_BY_NAME = {
    "DOH": r"^Edward L Doheny Junior Memorial Library$",
    "SSL": r"^Seaver Science Library$",
    "VPD": r"^Verna and Peter Dauterive Hall$",
    "MRF": r"^Montgomery Ross Fisher Building$",
    "HAR": r"^Harris Hall$",
    "HNB": r"^Hedco Neurosciences Building$",
    "AHF": r"^Allan Hancock Foundation$",
    "CAS": r"^College Academic Services Building$",
    "PED": r"^Physical Education Building$",
    "STU": r"^Student Union$",
    "ADM": r"^Bovard Administration Building$",
    "ASC": r"^Annenberg School for Communication",
    "ANN": r"^Wallis Annenberg Hall$",
    "JFF": r"^Jill & Frank Fertitta Hall$",
    "DMC": r"^Dr\.? Joseph Medicine Crow Center",
    "KDC": r"^Glorya Kaufman International Dance Center$",
    "LRC": r"^Lyon Recreation Center$",
}

# Outdoor event venues, keyed by the name in trojanevents.usc.edu's diagram image alt text -> OSM name (regex).
# Argue Plaza and Crocker Plaza aren't named in OSM; add them here with a nearby feature if needed.
# `approx` = the OSM feature is next to the venue rather than the venue itself.
VENUES = {
    "AHF": (r"^Allan Hancock Foundation$", True),
    "Alumni Park": (r"^Alumni Park$", False),
    "Associates Park": (r"^Associates Park$", False),
    "Bogardus Courtyard": (r"^Bogardus Courtyard$", False),
    "CAS Lawn": (r"^College Academic Services Building$", True),
    "Dr. Medicine Crow": (r"^Dr\.? Joseph Medicine Crow Center", True),
    "E.F. Hutton": (r"^Hutton Park$", False),
    "Founders Park": (r"^Hutton Park$", True),   # OSM: Hutton Park, old_name Founders Park
    "Great Lawn": (r"^Great Lawn$", False),
    "Hahn Plaza": (r"^Hahn Plaza$", False),
    "McCarthy Quad": (r"^McCarthy Quad$", False),
    "McClintock Lawn": (r"^McClintock Hall$", True),
    "Pardee": (r"^Pardee Plaza$", False),
    "Pardee Lawn": (r"^Pardee Plaza$", True),
    "Meyers Plaza": (r"^Physical Education Building$", True),
    "Queen's Courtyard": (r"^Queen's Courtyard$", False),
    "SOS Lawn": (r"^Social Sciences Building$", True),
    "Village Plaza": (r"^USC Village Fountain$", True),
}

# Trousdale Parkway isn't an area in OSM; its 4 event quadrants span Exposition Blvd -> Jefferson Blvd.
TROUSDALE = [[-118.28535, 34.01870], [-118.28530, 34.02040], [-118.28525, 34.02260], [-118.28520, 34.02520]]


def read_osm(pbf: str) -> list[dict]:
    import osmium
    from shapely import wkb

    fab = osmium.geom.WKBFactory()
    out = []

    class H(osmium.SimpleHandler):
        def area(self, a):
            t = dict(a.tags)
            if "name" not in t:
                return
            try:
                g = wkb.loads(fab.create_multipolygon(a), hex=True)
            except Exception:
                return
            c = g.representative_point()
            if S <= c.y <= N and W <= c.x <= E:
                out.append({"name": t["name"], "tags": t, "lat": round(c.y, 6), "lng": round(c.x, 6),
                            "osm": ("way/" if a.from_way() else "relation/") + str(a.orig_id())})

        def node(self, n):
            t = dict(n.tags)
            if "name" in t and S <= n.location.lat <= N and W <= n.location.lon <= E \
                    and any(k in t for k in ("amenity", "tourism", "leisure", "place")):
                out.append({"name": t["name"], "tags": t, "lat": round(n.location.lat, 6),
                            "lng": round(n.location.lon, 6), "osm": f"node/{n.id}"})

    H().apply_file(pbf, locations=True)
    return out


def find(feats: list[dict], pattern: str) -> dict | None:
    rx = re.compile(pattern)
    hits = [f for f in feats if rx.search(f["name"])]
    hits.sort(key=lambda f: ("building" not in f["tags"], f["osm"].startswith("node")))
    return hits[0] if hits else None


def build(pbf: str) -> dict:
    feats = read_osm(pbf)
    buildings = {}
    for f in feats:
        t = f["tags"]
        if "building" not in t:
            continue
        for key in ("short_name", "ref"):
            code = (t.get(key) or "").strip().upper()
            if re.fullmatch(r"[A-Z]{2,4}", code) and code not in buildings:
                buildings[code] = {"name": t.get("official_name") or f["name"], "lat": f["lat"], "lng": f["lng"], "osm": f["osm"]}
    unresolved = []
    for code, pat in CODE_BY_NAME.items():
        if code in buildings:
            continue
        f = find(feats, pat)
        if f:
            buildings[code] = {"name": f["tags"].get("official_name") or f["name"], "lat": f["lat"], "lng": f["lng"], "osm": f["osm"]}
        else:
            unresolved.append(code)
    venues = {}
    for name, (pat, approx) in VENUES.items():
        f = find(feats, pat)
        if f:
            venues[name] = {"lat": f["lat"], "lng": f["lng"], "osm": f["osm"], "osm_name": f["name"], "approx": approx}
        else:
            unresolved.append(name)
    venues["Trousdale Parkway"] = {"line": TROUSDALE, "approx": True}
    return {"generated_at": now_iso(), "source": "OpenStreetMap contributors (ODbL)",
            "buildings": dict(sorted(buildings.items())), "venues": venues, "unresolved": unresolved}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="usc_data places")
    ap.add_argument("--pbf", default=os.path.join(DATA, "work", "study_area.osm.pbf"))
    args = ap.parse_args(argv)
    places = build(args.pbf)
    write_json(PLACES_FILE, places)
    print(f"{PLACES_FILE}: {len(places['buildings'])} buildings, {len(places['venues'])} venues; "
          f"unresolved: {places['unresolved']}")
