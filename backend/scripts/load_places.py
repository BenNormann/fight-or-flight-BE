"""Load the searchable `places` table from three sources (bulk upsert by id):

  * Google Maps places  data/usc_google_maps_places.geojson  -> kind=place     id gmaps:<place_id|id>
  * campus buildings    data/places/usc_buildings.geojson    -> kind=building  id bldg:<CODE>
  * campus POIs         data/pois/*.geojson                  -> kind=poi       id poi:<pois.id>

    docker compose exec api python -m scripts.load_places [--only place,building,poi] [--all-areas]

Re-running is safe: rows are upserted, and rows of a source file that are no longer in it are
deleted. Google places outside the study area (5 mi of USC; the file reaches 10 mi) are skipped
unless --all-areas is given, since routes can't reach them.
"""
import argparse
import glob
import json
import math
import os
import re
import time

from app.services.opening_hours import parse_hours
from app.services.place_taxonomy import categorize, slug
from app.services.text_norm import normalize

PLACES_FILE = "usc_google_maps_places.geojson"
BUILDINGS_FILE = os.path.join("places", "usc_buildings.geojson")

# Popularity: 0.6 * reach (log-scaled review count) + 0.4 * quality (Bayesian-shrunk rating).
PRIOR_REVIEWS = 20          # a rating needs ~20 reviews before it counts as much as the prior
DEFAULT_MEAN_RATING = 4.3   # used when the file gives nothing to average
REVIEW_REF = 10000          # review count that maps to full reach (log scale)
W_REACH, W_QUALITY = 0.6, 0.4
BUILDING_POPULARITY = 0.8

# POI type (data/pois) -> (category, subcategory, display name when the feature has none)
POI_TYPES = {
    "seating": ("outdoors", "outdoor_seating", "Outdoor seating"),
    "study": ("study", "study_spot", "Study spot"),
}

# Short forms people type for chains, keyed by the normalized base name.
BRAND_ALIASES = {
    "the coffee bean tea leaf": ["Coffee Bean"],
    "in n out burger": ["In-N-Out", "Innout"],
    "7 eleven": ["7-11", "Seven Eleven"],
    "cvs": ["CVS Pharmacy"],
    "cvs pharmacy": ["CVS"],
    "walgreens pharmacy": ["Walgreens"],
    "trader joes": ["TJs", "Trader Joe"],
    "mcdonalds": ["McDonald"],
    "starbucks coffee company": ["Starbucks"],
    "chick fil a": ["Chickfila"],
    "the habit burger grill": ["The Habit", "Habit Burger"],
    "wells fargo bank": ["Wells Fargo"],
    "chase bank": ["Chase"],
    "bank of america financial center": ["Bank of America", "BofA"],
}

_BRANCH = re.compile(r"\s+[-–—|@·]\s+.*$|\s*\([^()]*\)\s*$|\s*:\s+.*$")
_HYPHEN_IN_WORD = re.compile(r"(?<=\w)-(?=\w)")


# ---- pure transforms (unit-tested; no DB, no app context) -------------------------------------

def name_aliases(name: str) -> list[str]:
    """Search aliases derived from a place name: the name without a trailing branch suffix
    ('Starbucks - Figueroa' -> 'Starbucks'), '&' <-> 'and', without a leading 'The', hyphens
    closed up ('Chick-fil-A' -> 'Chickfila'), plus known brand short forms. Excludes the name itself."""
    name = (name or "").strip()
    cands = []
    base = name
    for _ in range(2):  # "X - Y (Z)" -> "X"
        stripped = _BRANCH.sub("", base).strip()
        if len(stripped) >= 2 and stripped != base:
            base = stripped
    for v in dict.fromkeys((name, base)):  # ordered: aliases must be deterministic
        cands.append(v)
        if "&" in v:
            cands.append(re.sub(r"\s*&\s*", " and ", v))
        if re.search(r"\band\b", v, re.I):
            cands.append(re.sub(r"\s+and\s+", " & ", v, flags=re.I))
    for v in list(cands):
        if re.match(r"the\s+", v, re.I) and len(v) > 8:
            cands.append(re.sub(r"^the\s+", "", v, flags=re.I))
        if _HYPHEN_IN_WORD.search(v):
            cands.append(_HYPHEN_IN_WORD.sub("", v))
    for v in list(cands):
        cands += BRAND_ALIASES.get(normalize(v), [])
    return dedupe_aliases(name, cands)


def dedupe_aliases(name: str, aliases) -> list[str]:
    """Keep aliases whose normalized form is new (not the name's, not an earlier alias's)."""
    seen, out = {normalize(name)}, []
    for a in aliases:
        a = (a or "").strip()
        n = normalize(a)
        if n and n not in seen:
            seen.add(n)
            out.append(a)
    return out


def search_text(name_norm: str, aliases) -> str:
    """name_norm + normalized aliases, single-space separated, each distinct form once."""
    parts = [name_norm]
    for a in aliases:
        n = normalize(a)
        if n and n not in parts:
            parts.append(n)
    return " ".join(p for p in parts if p)


def popularity(rating, review_count, mean_rating=DEFAULT_MEAN_RATING) -> float:
    """0..1 prominence. The rating is shrunk toward `mean_rating` with a prior of PRIOR_REVIEWS
    reviews, so a 5.0 from 3 reviews ranks below a 4.5 from 800."""
    n = max(int(review_count or 0), 0)
    r = float(rating) if rating else None
    reach = min(math.log1p(n) / math.log1p(REVIEW_REF), 1.0)
    shrunk = mean_rating if r is None else (PRIOR_REVIEWS * mean_rating + n * r) / (PRIOR_REVIEWS + n)
    quality = min(max((shrunk - 1.0) / 4.0, 0.0), 1.0)
    return round(W_REACH * reach + W_QUALITY * quality, 4)


def mean_rating(features) -> float:
    rs = [float(f["properties"]["rating"]) for f in features
          if (f.get("properties") or {}).get("rating") and (f["properties"].get("review_count") or 0) > 0]
    return round(sum(rs) / len(rs), 3) if rs else DEFAULT_MEAN_RATING


def _point(feat):
    g = feat.get("geometry") or {}
    if g.get("type") != "Point" or not g.get("coordinates"):
        raise ValueError(f"feature {(feat.get('properties') or {}).get('id')}: not a Point")
    lng, lat = g["coordinates"][:2]
    return float(lat), float(lng)


def _row(**kw):
    kw["name_norm"] = normalize(kw["name"])
    kw["search_text"] = search_text(kw["name_norm"], kw["aliases"])
    kw.setdefault("hours", None)
    kw["detail"] = {k: v for k, v in (kw.get("detail") or {}).items() if v not in (None, "", [], {})}
    return kw


def google_place_row(feat, source_file=PLACES_FILE, mean=DEFAULT_MEAN_RATING) -> dict:
    p = feat.get("properties") or {}
    lat, lng = _point(feat)
    name = (p.get("name") or "").strip()
    if not name:
        raise ValueError(f"place {p.get('id')}: missing name")
    labels = [str(x) for x in (p.get("categories") or []) if x]
    category, sub = categorize(labels)
    n = int(p.get("review_count") or 0)
    rating = float(p["rating"]) if p.get("rating") else None
    return _row(
        id=f"gmaps:{p.get('place_id') or p['id']}",
        kind="place",
        name=name,
        aliases=name_aliases(name),
        category=category,
        subcategory=sub,
        categories=labels,
        address=p.get("address"),
        lat=lat, lng=lng,
        rating=rating,
        review_count=n,
        popularity=popularity(rating, n, mean),
        hours=parse_hours(p.get("open_hours")),
        detail={
            "phone": p.get("phone"), "website": p.get("website"), "photo_url": p.get("photo_url"),
            "rating_breakdown": p.get("rating_breakdown"), "attributes": p.get("attributes"),
            "description": p.get("description"), "reviews": p.get("reviews"), "plus_code": p.get("plus_code"),
            "hours_text": p.get("open_hours"), "tier": p.get("detail"), "source_id": p.get("id"),
            "place_id": p.get("place_id"),
        },
        source="google-maps",
        source_file=os.path.basename(source_file),
    )


def building_row(feat, source_file=BUILDINGS_FILE) -> dict:
    p = feat.get("properties") or {}
    lat, lng = _point(feat)
    code = str(p["code"]).strip().upper()
    aliases = [code] + [a for a in (p.get("aliases") or []) if a]
    return _row(
        id=f"bldg:{code}",
        kind="building",
        name=p["name"],
        aliases=dedupe_aliases(p["name"], aliases),
        category=p.get("category") or "campus",
        subcategory=p.get("subcategory") or "university_building",
        categories=[],
        address=p.get("address"),
        lat=lat, lng=lng,
        rating=None, review_count=0,
        popularity=BUILDING_POPULARITY,
        detail={"code": code, "entrances": p.get("entrances"), "usc_type": p.get("usc_type")},
        source=p.get("source") or "usc-fpm-gis",
        source_file=os.path.basename(source_file),
    )


def poi_row(r: dict) -> dict:
    """A row from services.pois.parse_feature_collection -> a places row."""
    from shapely.geometry import shape

    props = json.loads(r["props"]) if isinstance(r.get("props"), str) else (r.get("props") or {})
    cat, sub, default_name = POI_TYPES.get(r["type"], (None, slug(r["type"]), None))
    if cat is None:
        cat, _ = categorize([str(r["type"]).replace("_", " ")])
    pt = shape(json.loads(r["geom"]) if isinstance(r["geom"], str) else r["geom"]).representative_point()
    name = r.get("name") or default_name or str(r["type"]).replace("_", " ").capitalize()
    aliases = ["Outdoor tables", "Outdoor seating"] if r["type"] == "seating" else []
    return _row(
        id=f"poi:{r['id']}",
        kind="poi",
        name=name,
        aliases=dedupe_aliases(name, aliases),
        category=cat,
        subcategory=sub,
        categories=[],
        address=None,
        lat=pt.y, lng=pt.x,
        rating=None, review_count=0,
        popularity=0.3,
        detail={"poi_type": r["type"], **props},
        source=r.get("source"),
        source_file=r["source_file"],
    )


# ---- DB ---------------------------------------------------------------------------------------

COLS = ["id", "kind", "name", "name_norm", "aliases", "search_text", "category", "subcategory", "categories",
        "address", "lat", "lng", "rating", "review_count", "popularity", "hours", "detail", "source",
        "source_file"]
TEMPLATE = ("(%(id)s, %(kind)s, %(name)s, %(name_norm)s, %(aliases)s::text[], %(search_text)s, %(category)s, "
            "%(subcategory)s, %(categories)s::text[], %(address)s, %(lat)s, %(lng)s, %(rating)s, %(review_count)s, "
            "%(popularity)s, %(hours)s::jsonb, %(detail)s::jsonb, %(source)s, %(source_file)s)")
UPDATE_COLS = [c for c in COLS if c != "id"]
MERGE = f"""
INSERT INTO places ({", ".join(COLS)}, geom)
SELECT {", ".join(COLS)}, ST_SetSRID(ST_MakePoint(lng, lat), 4326) FROM _places_in
ON CONFLICT (id) DO UPDATE SET {", ".join(f"{c} = EXCLUDED.{c}" for c in UPDATE_COLS)}, geom = EXCLUDED.geom
"""


def upsert(conn, rows, kind, source_files):
    """Bulk upsert `rows` (execute_values into a temp table, then one INSERT ... ON CONFLICT), and
    delete rows of `kind` from `source_files` that are no longer present."""
    from psycopg2.extras import Json, execute_values

    with conn.cursor() as cur:
        # Same column types as places, no constraints (geom is filled in by MERGE from lat/lng).
        cur.execute("DROP TABLE IF EXISTS _places_in")
        cur.execute(f"CREATE TEMP TABLE _places_in AS SELECT {', '.join(COLS)} FROM places WITH NO DATA")
        data = [{**r, "hours": Json(r["hours"]) if r["hours"] is not None else None, "detail": Json(r["detail"])}
                for r in rows]
        execute_values(cur, f"INSERT INTO _places_in ({', '.join(COLS)}) VALUES %s", data,
                       template=TEMPLATE, page_size=2000)
        cur.execute(MERGE)
        cur.execute("DELETE FROM places p WHERE p.kind = %s AND p.source_file = ANY(%s) "
                    "AND NOT EXISTS (SELECT 1 FROM _places_in i WHERE i.id = p.id)", (kind, list(source_files)))
        deleted = cur.rowcount
        cur.execute("DROP TABLE _places_in")
    return deleted


def _read(path):
    with open(path, encoding="utf-8") as f:   # explicit: the Windows default (cp1252) garbles names
        return json.load(f)


def _dedupe(rows):
    out = {}
    for r in rows:
        out.setdefault(r["id"], r)
    return list(out.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--places", help=f"Google places GeoJSON (default: $DATA_DIR/{PLACES_FILE})")
    ap.add_argument("--buildings", help=f"campus buildings GeoJSON (default: $DATA_DIR/{BUILDINGS_FILE})")
    ap.add_argument("--only", default="place,building,poi", help="comma list of kinds to load")
    ap.add_argument("--all-areas", action="store_true", help="keep Google places outside the study area")
    args = ap.parse_args()
    kinds = {k.strip() for k in args.only.split(",") if k.strip()}

    from sqlalchemy import text

    from app import create_app
    from app.extensions import db
    from app.services.h3_utils import in_study_area
    from app.services.pois import parse_feature_collection

    app = create_app()
    with app.app_context():
        data_dir = app.config["DATA_DIR"]
        batches = []  # (kind, rows, source_files)
        t0 = time.time()
        if "place" in kinds:
            path = args.places or os.path.join(data_dir, PLACES_FILE)
            if os.path.exists(path):
                feats = _read(path)["features"]
                mean = mean_rating(feats)
                rows = [google_place_row(f, path, mean) for f in feats]
                kept = rows if args.all_areas else [r for r in rows if in_study_area(r["lat"], r["lng"])]
                batches.append(("place", _dedupe(kept), [os.path.basename(path)]))
                print(f"{os.path.basename(path)}: {len(kept)} places ({len(rows) - len(kept)} outside the study "
                      f"area skipped; mean rating {mean})")
            else:
                print(f"no Google places file at {path} (see data/README.md); skipping")
        if "building" in kinds:
            path = args.buildings or os.path.join(data_dir, BUILDINGS_FILE)
            if os.path.exists(path):
                rows = [building_row(f, path) for f in _read(path)["features"]]
                batches.append(("building", _dedupe(rows), [os.path.basename(path)]))
                print(f"{os.path.basename(path)}: {len(rows)} buildings")
            else:
                print(f"no buildings file at {path}; skipping")
        if "poi" in kinds:
            files = sorted(glob.glob(os.path.join(data_dir, "pois", "*.geojson")))
            rows = []
            for path in files:
                rows += [poi_row(r) for r in parse_feature_collection(_read(path), path)]
            batches.append(("poi", _dedupe(rows), [os.path.basename(p) for p in files]))
            print(f"pois/: {len(rows)} POIs from {len(files)} file(s)")

        conn = db.session.connection().connection  # raw psycopg2 connection, same transaction
        for kind, rows, files in batches:
            deleted = upsert(conn, rows, kind, files)
            print(f"  {kind}: upserted {len(rows)}, deleted {deleted} stale")
        db.session.commit()
        counts = db.session.execute(text(
            "SELECT kind, category, count(*) FROM places GROUP BY 1, 2 ORDER BY 1, 3 DESC")).all()
        print(f"done in {time.time() - t0:.1f}s; places by kind/category:")
        for kind, cat, n in counts:
            print(f"  {kind:9s} {cat:14s} {n}")


if __name__ == "__main__":
    main()
