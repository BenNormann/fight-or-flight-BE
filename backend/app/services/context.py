"""Everything the backend knows about one H3 cell at one moment, as a flat dict.

Used to feed the risk model: keys that match a model feature name are passed to it
(see risk_model.model_inputs); the rest is returned alongside the score as context.
Names follow the ml_features view, so a retrained model that uses them picks them up
automatically.
"""
from datetime import datetime, timedelta, timezone

from ..db_utils import fetch_all

LIVE_METRICS = ("sun_altitude_deg", "is_dark", "temp_c", "humidity_pct", "precip_mm",
                "cloud_cover_pct", "wind_speed_kmh", "visibility_m", "streetlight_outages_open")


def cell_context(cell: str, at: datetime | None = None) -> dict:
    """Static features (buildings, roads, lighting, POIs), live observations at `at`
    (latest value within the preceding 2 hours, from the cell or its area cell),
    and recent crime counts from all sources (incl. NIBRS 2025+)."""
    at = at or datetime.now(timezone.utc)
    static = fetch_all("""
        SELECT cell, parent_r6, building_count, avg_height_m, streetlight_count,
               road_length_m, lit_road_length_m, intersection_count, poi_counts
        FROM h3_cells WHERE cell = :cell
    """, cell=cell)
    if not static:
        return {"h3_cell": cell, "in_study_area": False}
    ctx = static[0]
    poi_counts = ctx.pop("poi_counts") or {}
    ctx.update(in_study_area=True, seating_count=int(poi_counts.get("seating", 0)),
               poi_count=int(sum(poi_counts.values())), poi_counts=poi_counts)
    ctx["lit_road_share"] = (round(ctx["lit_road_length_m"] / ctx["road_length_m"], 3)
                             if ctx["road_length_m"] else None)

    obs = fetch_all("""
        SELECT DISTINCT ON (metric) metric, value, ts, source
        FROM observations
        WHERE h3_cell IN (:cell, :parent) AND metric = ANY(:metrics)
          AND ts <= :at AND ts > :at - INTERVAL '2 hours'
        ORDER BY metric, ts DESC
    """, cell=cell, parent=ctx["parent_r6"], metrics=list(LIVE_METRICS), at=at)
    for m in LIVE_METRICS:
        ctx[m] = None
    ctx["observed_at"] = {}
    for o in obs:
        ctx[o["metric"]] = o["value"]
        ctx["observed_at"][o["metric"]] = o["ts"]
    if ctx["streetlight_outages_open"] is None:
        ctx["streetlight_outages_open"] = 0

    crime = fetch_all("""
        SELECT count(*) FILTER (WHERE ts > :at - INTERVAL '30 days')::int AS crime_count_30d_live,
               count(*) FILTER (WHERE ts > :at - INTERVAL '90 days')::int AS crime_count_90d_live,
               max(ts) AS last_incident_at
        FROM crime_incidents WHERE h3_cell = :cell AND ts <= :at AND ts > :at - INTERVAL '90 days'
    """, cell=cell, at=at)[0]
    ctx.update(crime)
    return ctx


def is_current_hour(at: datetime, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return abs((now - at).total_seconds()) < timedelta(hours=1).total_seconds()
