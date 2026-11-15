import marshmallow as ma
from flask_smorest import Blueprint

from ..db_utils import fetch_all
from .params import BaseArgs, BBox

blp = Blueprint("cells", __name__, url_prefix="/api/v1/cells", description="H3 cells + static features")


class CellArgs(BaseArgs):
    bbox = BBox(metadata={"description": "minLng,minLat,maxLng,maxLat"})
    format = ma.fields.String(load_default="json", validate=ma.validate.OneOf(["json", "geojson"]))
    limit = ma.fields.Integer(load_default=20000, validate=ma.validate.Range(1, 50000))


@blp.route("")
@blp.arguments(CellArgs, location="query")
@blp.response(200)
def list_cells(args):
    """Fine (res 9) cells in the study area with static features (buildings, streetlights, roads)."""
    where, params = "", {"limit": args["limit"]}
    if args.get("bbox"):
        where = "WHERE geom && ST_MakeEnvelope(:a, :b, :c, :d, 4326)"
        params.update(dict(zip("abcd", args["bbox"])))
    geom = ", ST_AsGeoJSON(geom)::json AS geometry" if args["format"] == "geojson" else ""
    rows = fetch_all(f"""
        SELECT cell, parent_r6, building_count, avg_height_m, streetlight_count,
               road_length_m, lit_road_length_m, intersection_count, poi_counts{geom}
        FROM h3_cells {where} ORDER BY cell LIMIT :limit
    """, **params)
    if args["format"] == "geojson":
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": r.pop("geometry"), "properties": r} for r in rows
        ]}
    return rows
