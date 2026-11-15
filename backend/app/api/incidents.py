import marshmallow as ma
from flask_smorest import Blueprint

from ..db_utils import fetch_all
from .params import DEFAULT_WINDOW_30D, BBox, CsvList, TimeRangeArgs, time_range

blp = Blueprint("incidents", __name__, url_prefix="/api/v1/incidents", description="Crime incidents")


class IncidentArgs(TimeRangeArgs):
    bbox = BBox()
    cells = CsvList()
    category = ma.fields.String(metadata={"description": "case-insensitive substring match"})
    severity = ma.fields.Integer(metadata={"description": "1 = Part I (serious), 2 = Part II"})
    limit = ma.fields.Integer(load_default=10000, validate=ma.validate.Range(1, 200000))


@blp.route("")
@blp.arguments(IncidentArgs, location="query")
@blp.response(200)
def list_incidents(args):
    """Crime points in a time range (default: last 30 days)."""
    start, end = time_range(args, DEFAULT_WINDOW_30D)
    clauses = ["ts >= :start AND ts < :end"]
    params = {"start": start, "end": end, "limit": args["limit"]}
    if args.get("bbox"):
        clauses.append("geom && ST_MakeEnvelope(:a, :b, :c, :d, 4326)")
        params.update(dict(zip("abcd", args["bbox"])))
    if args.get("cells"):
        clauses.append("h3_cell = ANY(:cells)")
        params["cells"] = args["cells"]
    if args.get("category"):
        clauses.append("category ILIKE :cat")
        params["cat"] = f"%{args['category']}%"
    if args.get("severity"):
        clauses.append("severity = :sev")
        params["sev"] = args["severity"]
    return fetch_all(f"""
        SELECT source, external_id, ts, ST_Y(geom) AS lat, ST_X(geom) AS lng,
               h3_cell, category, crime_code, severity
        FROM crime_incidents WHERE {' AND '.join(clauses)}
        ORDER BY ts DESC LIMIT :limit
    """, **params)


@blp.route("/hourly")
@blp.arguments(IncidentArgs, location="query")
@blp.response(200)
def hourly(args):
    """Hourly incident counts per cell (from the crime_hourly continuous aggregate)."""
    start, end = time_range(args, DEFAULT_WINDOW_30D)
    clauses = ["bucket >= :start AND bucket < :end"]
    params = {"start": start, "end": end, "limit": args["limit"]}
    if args.get("cells"):
        clauses.append("h3_cell = ANY(:cells)")
        params["cells"] = args["cells"]
    if args.get("category"):
        clauses.append("category ILIKE :cat")
        params["cat"] = f"%{args['category']}%"
    return fetch_all(f"""
        SELECT bucket, h3_cell, sum(n)::int AS n FROM crime_hourly
        WHERE {' AND '.join(clauses)} GROUP BY 1, 2 ORDER BY 1, 2 LIMIT :limit
    """, **params)
