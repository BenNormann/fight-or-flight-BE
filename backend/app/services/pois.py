"""Parse POI GeoJSON files (data/pois/*.geojson) into rows for the `pois` table.

Each feature needs a `type` property (seating, study, ...), either per feature or as a
file-level default. `id`, `name` and `source` are optional; every other property is kept in `props`.
"""
import json
import os

import h3
from flask import current_app
from shapely.geometry import shape

RESERVED = {"id", "type", "name", "source"}


def parse_feature_collection(fc: dict, source_file: str, default_type: str | None = None) -> list[dict]:
    if fc.get("type") != "FeatureCollection":
        raise ValueError(f"{source_file}: not a FeatureCollection")
    res = current_app.config["H3_RES"]
    stem = os.path.splitext(os.path.basename(source_file))[0]
    rows, seen = [], set()
    for i, feat in enumerate(fc.get("features") or []):
        props = feat.get("properties") or {}
        geom = feat.get("geometry")
        if not geom:
            raise ValueError(f"{source_file} feature {i}: missing geometry")
        poi_type = props.get("type") or default_type
        if not poi_type:
            raise ValueError(f"{source_file} feature {i}: missing 'type' property")
        poi_id = str(props.get("id") or f"{stem}-{i + 1:04d}")
        if poi_id in seen:
            raise ValueError(f"{source_file}: duplicate id {poi_id}")
        seen.add(poi_id)
        pt = shape(geom).representative_point()
        rows.append({
            "id": poi_id,
            "type": str(poi_type),
            "name": props.get("name"),
            "source": props.get("source"),
            "source_file": os.path.basename(source_file),
            "props": json.dumps({k: v for k, v in props.items() if k not in RESERVED}),
            "geom": json.dumps(geom),
            "h3_cell": h3.latlng_to_cell(pt.y, pt.x, res),
        })
    return rows
