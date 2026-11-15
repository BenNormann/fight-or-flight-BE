"""Shared query-arg fields/schemas and filter helpers."""
from datetime import datetime, timedelta, timezone

import marshmallow as ma
from marshmallow import EXCLUDE, ValidationError

from ..db_utils import fetch_all


class BBox(ma.fields.Field):
    """'minLng,minLat,maxLng,maxLat' -> tuple of 4 floats."""

    def _deserialize(self, value, attr, data, **kwargs):
        try:
            parts = tuple(float(p) for p in str(value).split(","))
        except ValueError as exc:
            raise ValidationError("bbox must be 4 comma-separated numbers") from exc
        if len(parts) != 4 or parts[0] >= parts[2] or parts[1] >= parts[3]:
            raise ValidationError("bbox must be minLng,minLat,maxLng,maxLat")
        return parts


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


class CsvList(ma.fields.Field):
    def _deserialize(self, value, attr, data, **kwargs):
        return [v.strip() for v in str(value).split(",") if v.strip()]


class BaseArgs(ma.Schema):
    class Meta:
        unknown = EXCLUDE


class TimeRangeArgs(BaseArgs):
    start = ma.fields.AwareDateTime(data_key="from", default_timezone=timezone.utc,
                                    metadata={"description": "ISO-8601, default: `to` minus default window"})
    end = ma.fields.AwareDateTime(data_key="to", default_timezone=timezone.utc,
                                  metadata={"description": "ISO-8601, default: now"})


def time_range(args, default_window):
    end = args.get("end") or datetime.now(timezone.utc)
    start = args.get("start") or end - default_window
    if start >= end:
        raise ValidationError("`from` must be before `to`")
    return start, end


def cells_in_bbox(bbox, include_parents=False):
    rows = fetch_all(
        "SELECT cell, parent_r6 FROM h3_cells WHERE geom && ST_MakeEnvelope(:a, :b, :c, :d, 4326)",
        a=bbox[0], b=bbox[1], c=bbox[2], d=bbox[3],
    )
    cells = {r["cell"] for r in rows}
    if include_parents:
        cells |= {r["parent_r6"] for r in rows}
    return sorted(cells)


DEFAULT_WINDOW_1H = timedelta(hours=1)
DEFAULT_WINDOW_1D = timedelta(days=1)
DEFAULT_WINDOW_30D = timedelta(days=30)
