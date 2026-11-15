import io

import marshmallow as ma
from flask import Response
from flask_smorest import Blueprint

from ..db_utils import fetch_all, fetch_df
from .params import BaseArgs, BBox

blp = Blueprint("roads", __name__, url_prefix="/api/v1/roads",
                description="Walkable street network (OpenStreetMap) for routing")


class RoadArgs(BaseArgs):
    bbox = BBox(required=True)
    limit = ma.fields.Integer(load_default=20000, validate=ma.validate.Range(1, 100000))


class GraphArgs(BaseArgs):
    part = ma.fields.String(load_default="edges", validate=ma.validate.OneOf(["edges", "nodes"]))
    format = ma.fields.String(load_default="parquet", validate=ma.validate.OneOf(["parquet", "csv", "json"]))
    bbox = BBox()
    geometry = ma.fields.String(load_default="none", validate=ma.validate.OneOf(["none", "wkt"]))


@blp.route("")
@blp.arguments(RoadArgs, location="query")
@blp.response(200)
def roads_geojson(args):
    """Road edges in a bbox as GeoJSON (for display / debugging)."""
    rows = fetch_all("""
        SELECT id, u, v, key, name, highway, lit, sidewalk, length_m, h3_cell,
               ST_AsGeoJSON(geom, 6)::json AS geometry
        FROM road_edges WHERE geom && ST_MakeEnvelope(:a, :b, :c, :d, 4326) LIMIT :limit
    """, limit=args["limit"], **dict(zip("abcd", args["bbox"])))
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": r.pop("id"), "geometry": r.pop("geometry"), "properties": r} for r in rows
    ]}


@blp.route("/graph")
@blp.arguments(GraphArgs, location="query")
@blp.response(200)
def graph(args):
    """Bulk graph export for the ML/routing service. Fetch `part=nodes` and `part=edges`,
    build a graph keyed on node `osmid` (edges: `u`, `v`, `key`), and weight edges by
    `length_m` x risk over `h3_cells`."""
    where, params = "", {}
    if args.get("bbox"):
        where = "WHERE geom && ST_MakeEnvelope(:a, :b, :c, :d, 4326)"
        params = dict(zip("abcd", args["bbox"]))
    geom = ", ST_AsText(geom) AS wkt" if args["geometry"] == "wkt" else ""
    if args["part"] == "nodes":
        sql = f"SELECT osmid, ST_Y(geom) AS lat, ST_X(geom) AS lng, street_count, h3_cell{geom} FROM road_nodes {where}"
    else:
        sql = (f"SELECT u, v, key, osmid, name, highway, lit, sidewalk, footway, length_m, h3_cell, "
               f"array_to_string(h3_cells, ',') AS h3_cells{geom} FROM road_edges {where}")

    fmt = args["format"]
    if fmt == "json":
        return fetch_all(sql + " LIMIT 100000", **params)
    df = fetch_df(sql, **params)
    name = f"roads_{args['part']}"
    if fmt == "csv":
        return Response(df.to_csv(index=False), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={name}.csv"})
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return Response(buf.getvalue(), mimetype="application/vnd.apache.parquet",
                    headers={"Content-Disposition": f"attachment; filename={name}.parquet"})
