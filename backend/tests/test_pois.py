import json

import h3
import pytest

from app.services.pois import parse_feature_collection


def fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def pt(lng, lat, **props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]}, "properties": props}


def test_parse_keeps_extra_props(app):
    rows = parse_feature_collection(
        fc(pt(-118.2827514, 34.018763, id="usc-tbl-001", type="seating", name=None,
              table_count_est=4, source="esri-world-imagery+manual-review")),
        "/data/pois/usc_outdoor_table_groups.geojson",
    )
    r = rows[0]
    assert (r["id"], r["type"], r["source_file"]) == ("usc-tbl-001", "seating", "usc_outdoor_table_groups.geojson")
    assert json.loads(r["props"]) == {"table_count_est": 4}
    assert r["h3_cell"] == h3.latlng_to_cell(34.018763, -118.2827514, 9)


def test_default_type_and_generated_ids(app):
    rows = parse_feature_collection(fc(pt(-118.28, 34.02), pt(-118.29, 34.02)), "study_spots.geojson", "study")
    assert [r["id"] for r in rows] == ["study_spots-0001", "study_spots-0002"]
    assert {r["type"] for r in rows} == {"study"}


def test_missing_type_and_duplicate_ids_rejected(app):
    with pytest.raises(ValueError, match="type"):
        parse_feature_collection(fc(pt(-118.28, 34.02)), "x.geojson")
    with pytest.raises(ValueError, match="duplicate"):
        parse_feature_collection(fc(pt(-118.28, 34.02, id="a", type="seating"),
                                    pt(-118.29, 34.02, id="a", type="seating")), "x.geojson")


def test_cell_feature_sql_renders():
    from app.services.cell_features import REFRESH_SQL

    assert "COALESCE(p.counts, '{}'::jsonb)" in REFRESH_SQL
    assert "lit IN ('yes'" in REFRESH_SQL
