import marshmallow as ma
from flask_smorest import Blueprint

from ..db_utils import fetch_all
from .params import DEFAULT_WINDOW_1H, BaseArgs, BBox, CsvList, TimeRangeArgs, cells_in_bbox, time_range

blp = Blueprint("observations", __name__, url_prefix="/api/v1/observations",
                description="Space-time observations (sun, weather, outages, ...)")

BUCKETS = {
    "raw": None,
    "1m": None,
    "10m": "obs_10min",
    "1h": "obs_hourly",
}


class ObsArgs(TimeRangeArgs):
    cells = CsvList(metadata={"description": "comma-separated H3 cells (res 9 or res 6)"})
    bbox = BBox(metadata={"description": "minLng,minLat,maxLng,maxLat (includes parent area cells)"})
    metric = CsvList(metadata={"description": "comma-separated metric names"})
    source = CsvList()
    bucket = ma.fields.String(load_default="10m", validate=ma.validate.OneOf(list(BUCKETS)))
    limit = ma.fields.Integer(load_default=100000, validate=ma.validate.Range(1, 500000))


class LatestArgs(BaseArgs):
    cells = CsvList()
    bbox = BBox()
    metric = CsvList()


def _filters(args, time_col):
    clauses, params = [], {}
    cells = list(args.get("cells") or [])
    if args.get("bbox"):
        cells += cells_in_bbox(args["bbox"], include_parents=True)
        if not cells:
            return None, None
    if cells:
        clauses.append("h3_cell = ANY(:cells)")
        params["cells"] = cells
    if args.get("metric"):
        clauses.append("metric = ANY(:metrics)")
        params["metrics"] = args["metric"]
    if args.get("source"):
        clauses.append("source = ANY(:sources)")
        params["sources"] = args["source"]
    if time_col:
        clauses.append(f"{time_col} >= :start AND {time_col} < :end")
    return clauses, params


@blp.route("")
@blp.arguments(ObsArgs, location="query")
@blp.response(200)
def list_observations(args):
    """Observations over a time range. `bucket=10m|1h` reads continuous aggregates;
    `1m` buckets raw data on the fly; `raw` returns individual points."""
    start, end = time_range(args, DEFAULT_WINDOW_1H)
    bucket = args["bucket"]
    time_col = "bucket" if BUCKETS[bucket] else "ts"
    clauses, params = _filters(args, time_col)
    if clauses is None:
        return []
    params.update(start=start, end=end, limit=args["limit"])
    where = "WHERE " + " AND ".join(clauses)

    if bucket == "raw":
        sql = f"SELECT ts AS t, h3_cell, source, metric, value FROM observations {where}"
    elif bucket == "1m":
        sql = f"""SELECT time_bucket(INTERVAL '1 minute', ts) AS t, h3_cell, source, metric,
                         avg(value) AS avg, min(value) AS min, max(value) AS max, count(*) AS n
                  FROM observations {where} GROUP BY 1, 2, 3, 4"""
    else:
        sql = f"SELECT bucket AS t, h3_cell, source, metric, avg, min, max, last, n FROM {BUCKETS[bucket]} {where}"
    return fetch_all(f"{sql} ORDER BY t, h3_cell, metric LIMIT :limit", **params)


@blp.route("/latest")
@blp.arguments(LatestArgs, location="query")
@blp.response(200)
def latest(args):
    """Most recent value per (cell, metric, source) within the last 24h, for live inference."""
    clauses, params = _filters(args, None)
    if clauses is None:
        return []
    clauses.append("ts > now() - INTERVAL '1 day'")
    return fetch_all(f"""
        SELECT DISTINCT ON (h3_cell, metric, source) h3_cell, metric, source, ts, value
        FROM observations WHERE {' AND '.join(clauses)}
        ORDER BY h3_cell, metric, source, ts DESC
    """, **params)


@blp.route("/metrics")
@blp.response(200)
def metrics():
    """Metrics seen in the last 7 days, with their sources and latest timestamp."""
    return fetch_all("""
        SELECT metric, source, max(bucket) AS latest_bucket, count(DISTINCT h3_cell) AS cells
        FROM obs_hourly WHERE bucket > now() - INTERVAL '7 days'
        GROUP BY metric, source ORDER BY metric, source
    """)
