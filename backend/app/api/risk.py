"""Crime-risk model endpoints (model: algorithm/, see algorithm/README.md).

Hours and dates are LA local time (the model is trained on LAPD clock hours).
"""
import io
from datetime import timezone

import h3
import marshmallow as ma
from flask import Response, current_app
from flask_smorest import Blueprint, abort

from ..services import risk_model
from .params import BaseArgs, BBox, cells_in_bbox

blp = Blueprint("risk", __name__, url_prefix="/api/v1/risk",
                description="Crime-risk model: scores, heatmap, route costs")


class WhenArgs(BaseArgs):
    at = ma.fields.AwareDateTime(default_timezone=timezone.utc,
                                 metadata={"description": "ISO-8601 instant (overrides date/hour)"})
    date = ma.fields.Date(metadata={"description": "LA local date, default today"})
    hour = ma.fields.Integer(validate=ma.validate.Range(0, 23),
                             metadata={"description": "LA local hour 0-23, default now"})


class ScoreArgs(WhenArgs):
    lat = ma.fields.Float()
    lng = ma.fields.Float()
    cell = ma.fields.String(metadata={"description": "H3 res-9 cell (instead of lat/lng)"})
    live = ma.fields.Boolean(load_default=True,
                             metadata={"description": "pass live backend values to the model (current hour only)"})
    top_k = ma.fields.Integer(load_default=5, validate=ma.validate.Range(1, 21))


class HotzoneArgs(WhenArgs):
    bbox = BBox()
    min_risk = ma.fields.Float(load_default=0.0, validate=ma.validate.Range(0, 1))


class EdgeArgs(WhenArgs):
    lam = ma.fields.Float(data_key="lambda", load_default=4.0, validate=ma.validate.Range(0, 100),
                          metadata={"description": "risk weight: cost = length_m * (1 + lambda * risk_mean)"})
    no_data_risk = ma.fields.Float(load_default=risk_model.NO_DATA_RISK, validate=ma.validate.Range(0, 1))
    format = ma.fields.String(load_default="parquet", validate=ma.validate.OneOf(["parquet", "csv", "json"]))


def _run(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except risk_model.ModelUnavailable as exc:
        abort(503, message=f"risk model unavailable: {exc}")


@blp.route("/status")
@blp.response(200)
def model_status():
    """Whether the model is loaded, plus its metadata (training dates, area, 2023 test metrics)."""
    st = risk_model.status()
    if st["available"]:
        st["model"] = risk_model.meta()
        st["heatmaps_cached"] = list(risk_model._heatmaps)
    return st


@blp.route("/score")
@blp.arguments(ScoreArgs, location="query")
@blp.response(200)
def score(args):
    """Risk + SHAP factors for a point or cell, with everything the backend knows about
    that cell (weather, darkness, lighting, roads, buildings, POIs, recent crime).
    `model_inputs_from_backend` lists the backend values the model actually consumed."""
    if args.get("cell"):
        cell = args["cell"]
        if not h3.is_valid_cell(cell) or h3.get_resolution(cell) != current_app.config["H3_RES"]:
            abort(422, message="cell must be a valid res-9 H3 cell")
    elif args.get("lat") is not None and args.get("lng") is not None:
        cell = h3.latlng_to_cell(args["lat"], args["lng"], current_app.config["H3_RES"])
    else:
        abort(422, message="pass lat & lng, or cell")
    result = _run(risk_model.score_cell, cell, args.get("at"), args.get("date"), args.get("hour"),
                  use_live=args["live"], top_k=args["top_k"])
    if result["status"] == "outside":
        abort(400, message="outside the model's 10-mile area")
    return result


@blp.route("/hotzones")
@blp.arguments(HotzoneArgs, location="query")
@blp.response(200)
def hotzones(args):
    """Risk for every modelled cell at one hour (for deck.gl H3HexagonLayer), plus
    `no_data_cells` to draw grey (other police jurisdictions: unknown, not safe).
    The first request for a new date takes ~20 s to build that day's heatmap."""
    d, h, _, _ = risk_model.resolve_time(args.get("at"), args.get("date"), args.get("hour"))
    cells = set(cells_in_bbox(args["bbox"])) if args.get("bbox") else None
    return _run(risk_model.hotzones, d, h, cells, args["min_risk"])


@blp.route("/edges")
@blp.arguments(EdgeArgs, location="query")
@blp.response(200)
def edges(args):
    """Risk-weighted cost for every edge of the OSM walk graph at one hour. Join to
    /roads/graph on (u, v, key) and run A*/Dijkstra on `cost` for the safest route
    (or on `length_m` for the fastest)."""
    d, h, _, _ = risk_model.resolve_time(args.get("at"), args.get("date"), args.get("hour"))
    df = _run(risk_model.edge_costs, d, h, args["lam"], args["no_data_risk"])
    if args["format"] == "json":
        return {"date": d.isoformat(), "hour": h, "lambda": args["lam"], "edges": df.to_dict(orient="records")}
    if args["format"] == "csv":
        return Response(df.to_csv(index=False), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=edge_costs_{d}_{h:02d}.csv"})
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return Response(buf.getvalue(), mimetype="application/vnd.apache.parquet",
                    headers={"Content-Disposition": f"attachment; filename=edge_costs_{d}_{h:02d}.parquet"})
