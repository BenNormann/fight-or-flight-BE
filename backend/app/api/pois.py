import marshmallow as ma
from flask_smorest import Blueprint

from ..db_utils import fetch_all
from .params import BaseArgs, BBox, CsvList

blp = Blueprint("pois", __name__, url_prefix="/api/v1/pois",
                description="Points of interest: outdoor seating, study spots, ...")


class PoiArgs(BaseArgs):
    bbox = BBox()
    type = CsvList(metadata={"description": "comma-separated POI types, e.g. seating,study"})
    cells = CsvList()
    limit = ma.fields.Integer(load_default=5000, validate=ma.validate.Range(1, 50000))


@blp.route("")
@blp.arguments(PoiArgs, location="query")
@blp.response(200)
def list_pois(args):
    """POIs as a GeoJSON FeatureCollection. Extra attributes (e.g. `table_count_est`)
    are merged into each feature's properties."""
    clauses, params = [], {"limit": args["limit"]}
    if args.get("bbox"):
        clauses.append("geom && ST_MakeEnvelope(:a, :b, :c, :d, 4326)")
        params.update(dict(zip("abcd", args["bbox"])))
    if args.get("type"):
        clauses.append("type = ANY(:types)")
        params["types"] = args["type"]
    if args.get("cells"):
        clauses.append("h3_cell = ANY(:cells)")
        params["cells"] = args["cells"]
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = fetch_all(f"""
        SELECT id, type, name, source, h3_cell, props, ST_AsGeoJSON(geom, 7)::json AS geometry
        FROM pois {where} ORDER BY id LIMIT :limit
    """, **params)
    features = []
    for r in rows:
        geometry, extra = r.pop("geometry"), r.pop("props") or {}
        features.append({"type": "Feature", "id": r["id"], "geometry": geometry, "properties": {**extra, **r}})
    return {"type": "FeatureCollection", "features": features}


@blp.route("/types")
@blp.response(200)
def poi_types():
    """POI types with counts."""
    return fetch_all("SELECT type, count(*)::int AS count FROM pois GROUP BY type ORDER BY type")
