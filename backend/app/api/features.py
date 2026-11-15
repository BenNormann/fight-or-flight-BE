import io

import marshmallow as ma
from flask import Response
from flask_smorest import Blueprint

from ..db_utils import fetch_all, fetch_df
from .params import DEFAULT_WINDOW_1D, BBox, CsvList, TimeRangeArgs, cells_in_bbox, time_range

blp = Blueprint("features", __name__, url_prefix="/api/v1/features",
                description="ML feature matrix: one row per (h3_cell, 10-min bucket)")


class FeatureArgs(TimeRangeArgs):
    bbox = BBox()
    cells = CsvList()
    format = ma.fields.String(load_default="json", validate=ma.validate.OneOf(["json", "csv", "parquet"]))
    limit = ma.fields.Integer(load_default=200000, validate=ma.validate.Range(1, 5000000))


@blp.route("")
@blp.arguments(FeatureArgs, location="query")
@blp.response(200)
def features(args):
    """Joined features from the `ml_features` view: sun/darkness, weather, streetlight outages,
    static cell features, and trailing-30-day crime count. Use `format=parquet|csv` for bulk pulls."""
    start, end = time_range(args, DEFAULT_WINDOW_1D)
    clauses = ["bucket >= :start AND bucket < :end"]
    params = {"start": start, "end": end, "limit": args["limit"]}
    cells = list(args.get("cells") or [])
    if args.get("bbox"):
        cells += cells_in_bbox(args["bbox"])
        if not cells:
            return []
    if cells:
        clauses.append("h3_cell = ANY(:cells)")
        params["cells"] = cells
    sql = f"SELECT * FROM ml_features WHERE {' AND '.join(clauses)} ORDER BY bucket, h3_cell LIMIT :limit"

    fmt = args["format"]
    if fmt == "json":
        return fetch_all(sql, **params)
    df = fetch_df(sql, **params)
    if fmt == "csv":
        return Response(df.to_csv(index=False), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=features.csv"})
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return Response(buf.getvalue(), mimetype="application/vnd.apache.parquet",
                    headers={"Content-Disposition": "attachment; filename=features.parquet"})
