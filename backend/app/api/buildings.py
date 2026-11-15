import os

import marshmallow as ma
from flask import current_app, send_file
from flask_smorest import Blueprint, abort

from ..db_utils import fetch_all
from .params import BaseArgs, BBox

blp = Blueprint("buildings", __name__, url_prefix="/api/v1", description="3D building footprints")


class BuildingArgs(BaseArgs):
    bbox = BBox(required=True)
    limit = ma.fields.Integer(load_default=5000, validate=ma.validate.Range(1, 50000))


@blp.route("/buildings.pmtiles")
def pmtiles():
    """Vector tiles of all buildings (PMTiles, supports HTTP range requests).
    Render with MapLibre `fill-extrusion` using the `height_m` property."""
    path = os.path.join(current_app.config["DATA_DIR"], "tiles", "buildings.pmtiles")
    if not os.path.exists(path):
        abort(404, message="buildings.pmtiles not built yet: run scripts/build_buildings.sh")
    return send_file(path, mimetype="application/vnd.pmtiles", conditional=True)


@blp.route("/buildings")
@blp.arguments(BuildingArgs, location="query")
@blp.response(200)
def buildings_geojson(args):
    """Building footprints in a small bbox as GeoJSON (for debugging / ML; use PMTiles for rendering)."""
    rows = fetch_all("""
        SELECT id, height_m, elev_m, h3_cell, ST_AsGeoJSON(geom, 6)::json AS geometry
        FROM buildings WHERE geom && ST_MakeEnvelope(:a, :b, :c, :d, 4326) LIMIT :limit
    """, limit=args["limit"], **dict(zip("abcd", args["bbox"])))
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": r.pop("id"), "geometry": r.pop("geometry"), "properties": r} for r in rows
    ]}
