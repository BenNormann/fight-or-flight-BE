import datetime as dt

from app.services.risk_model import LA, model_inputs, resolve_time

FEATURES = ["hour", "weekday", "is_dark", "cell_lat", "cell_30d", "area_7d"]


def test_model_inputs_only_matching_features():
    ctx = {"is_dark": 1.0, "temp_c": 18.0, "cell_30d": None, "poi_counts": {"seating": 2},
           "h3_cell": "8929a1d6447ffff"}
    assert model_inputs(ctx, FEATURES) == {"is_dark": 1}


def test_future_features_flow_through():
    # a retrained model that adds backend features gets them with no code change
    ctx = {"is_dark": 0.0, "temp_c": 18.5, "lit_road_length_m": 120.0}
    assert model_inputs(ctx, FEATURES + ["temp_c", "lit_road_length_m"]) == {
        "is_dark": 0, "temp_c": 18.5, "lit_road_length_m": 120.0}


def test_resolve_time_uses_la_clock():
    at = dt.datetime(2026, 11, 15, 6, 15, tzinfo=dt.timezone.utc)  # 22:15 PST on Nov 14
    d, h, instant, _ = resolve_time(at, None, None)
    assert (d, h) == (dt.date(2026, 11, 14), 22)
    d, h, instant, is_now = resolve_time(None, dt.date(2026, 7, 4), 21)
    assert (d, h, is_now) == (dt.date(2026, 7, 4), 21, False)
    assert instant.astimezone(LA).hour == 21
