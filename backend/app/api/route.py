"""Safe walking routes (planner: pathfinding/, see pathfinding/README.md)."""
from datetime import timezone

import marshmallow as ma
from flask_smorest import Blueprint, abort
from marshmallow import ValidationError

from ..services import risk_model, routing
from .params import BaseArgs

blp = Blueprint("route", __name__, url_prefix="/api/v1/route",
                description="Safe walking routes: low crime risk, past blue-light phones, bounded detour")


class LatLng(ma.fields.Field):
    """'lat,lng' -> (lat, lng)."""

    def _deserialize(self, value, attr, data, **kwargs):
        try:
            lat, lng = (float(x) for x in str(value).split(","))
        except ValueError as exc:
            raise ValidationError("expected 'lat,lng'") from exc
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValidationError("lat/lng out of range")
        return lat, lng


class RouteArgs(BaseArgs):
    start = LatLng(data_key="from", required=True, metadata={"description": "lat,lng"})
    end = LatLng(data_key="to", required=True, metadata={"description": "lat,lng"})
    at = ma.fields.AwareDateTime(default_timezone=timezone.utc,
                                 metadata={"description": "ISO-8601 instant (overrides date/hour)"})
    date = ma.fields.Date(metadata={"description": "LA local date, default today"})
    hour = ma.fields.Integer(validate=ma.validate.Range(0, 23),
                             metadata={"description": "LA local hour 0-23, default now"})
    max_detour = ma.fields.Float(load_default=0.20, validate=ma.validate.Range(0, 1),
                                 metadata={"description": "safe route may be at most this much longer (0.2 = 20%)"})
    phone_weight = ma.fields.Float(load_default=0.05, validate=ma.validate.Range(0, 1),
                                   metadata={"description": "mean-risk points one passed blue-light phone is worth"})


@blp.route("")
@blp.arguments(RouteArgs, location="query")
@blp.response(200)
def route(args):
    """`safe` and `shortest` routes with stats (length, detour %, mean/max risk, metres in
    high-risk cells, % without data, blue-light phones passed). Each route has `coords`
    ([[lat, lng], ...]) and a GeoJSON `geometry` (LineString, [lng, lat])."""
    try:
        return routing.plan(args["start"], args["end"], args.get("at"), args.get("date"), args.get("hour"),
                            max_detour=args["max_detour"], phone_weight=args["phone_weight"])
    except routing.RouteError as exc:
        abort(400, message=str(exc))
    except risk_model.ModelUnavailable as exc:
        abort(503, message=f"route planner unavailable: {exc}")
