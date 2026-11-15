"""Clip LA County building outlines to the study circle and load them into PostGIS.
Also writes data/work/buildings.geojsonl for tippecanoe.

    python -m scripts.load_buildings [path] [--layer NAME] [--codes Building] [--height-units ft|m]

`path` defaults to the first .gdb/.gdb.zip/.gpkg/.shp/.geojson found in data/raw/.
Dataset: https://www.arcgis.com/home/item.html?id=e6a1e375e12a4fe6849d896a26ec028a
(LARIAC6_Buildings_2020.gdb.zip: 3.3M footprints, EPSG:6424, HEIGHT/ELEV in US feet,
 CODE in {Building, Courtyard, Free Standing Solar Structure}.)
"""
import argparse
import glob
import os
import sys
import zipfile

import geopandas as gpd
import h3
import pyogrio
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.services.cell_features import refresh_static_features
from app.services.h3_utils import circle_ring

FT_TO_M = 0.3048
DEFAULT_RENDER_HEIGHT_M = 4.0  # tiles only: footprints without HEIGHT still extrude
HEIGHT_COLS = ("HEIGHT", "height", "BLD_HEIGHT", "HGT")
ELEV_COLS = ("ELEV", "elev", "ELEVATION")
ID_COLS = ("BLD_ID", "OBJECTID", "objectid", "id")
EXTS = (".gdb", ".gdb.zip", ".gpkg", ".shp", ".geojson", ".zip")


def find_input(data_dir):
    for ext in EXTS:
        hits = sorted(glob.glob(os.path.join(data_dir, "raw", f"*{ext}")))
        if hits:
            return hits[0]
    sys.exit(f"no building file found in {data_dir}/raw (looked for {', '.join(EXTS)})")


def gdal_path(path):
    """A zipped .gdb must be opened via its inner folder: /vsizip/<zip>/<name>.gdb"""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            inner = sorted({n.split("/")[0] for n in zf.namelist() if n.split("/")[0].lower().endswith(".gdb")})
        if inner:
            return f"/vsizip/{os.path.abspath(path)}/{inner[0]}"
    return path


def pick_layer(path, requested):
    if requested:
        return requested
    names = [name for name, _ in pyogrio.list_layers(path)]
    # LARIAC ships a *_DELETED_* layer next to the current one
    current = [n for n in names if "DELETED" not in n.upper()]
    return (current or names)[0]


def pick(cols, candidates):
    return next((c for c in candidates if c in cols), None)


def to_multi(g):
    if g is None or g.is_empty:
        return None
    g = shapely.force_2d(g)
    if isinstance(g, Polygon):
        return MultiPolygon([g])
    return g if isinstance(g, MultiPolygon) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--layer", help="layer name (default: first non-DELETED layer)")
    ap.add_argument("--codes", default="Building",
                    help="comma-separated CODE values to keep; '' keeps all (drops Courtyard by default)")
    ap.add_argument("--height-units", choices=["ft", "m"], default="ft")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        cfg = app.config
        path = gdal_path(args.path or find_input(cfg["DATA_DIR"]))
        layer = pick_layer(path, args.layer)
        print(f"reading {path} (layer {layer})")

        ring = circle_ring(cfg["USC_LAT"], cfg["USC_LNG"], cfg["RADIUS_KM"])
        circle = Polygon([(lng, lat) for lat, lng in ring])
        src_crs = CRS.from_user_input(pyogrio.read_info(path, layer=layer)["crs"])
        tf = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True)
        xs, ys = tf.transform([p[1] for p in ring], [p[0] for p in ring])

        gdf = gpd.read_file(path, layer=layer, bbox=(min(xs), min(ys), max(xs), max(ys)),
                            engine="pyogrio", use_arrow=True)
        print(f"  {len(gdf):,} features in bbox")
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        if codes and "CODE" in gdf.columns:
            gdf = gdf[gdf["CODE"].isin(codes)]
            print(f"  {len(gdf):,} after CODE filter {codes}")
        gdf = gdf.to_crs(4326)
        gdf = gdf[gdf.geometry.representative_point().within(circle)].copy()
        print(f"  {len(gdf):,} footprints in {cfg['RADIUS_KM']} km circle")

        factor = FT_TO_M if args.height_units == "ft" else 1.0
        hcol, ecol, icol = pick(gdf.columns, HEIGHT_COLS), pick(gdf.columns, ELEV_COLS), pick(gdf.columns, ID_COLS)
        print(f"  columns: height={hcol} elev={ecol} id={icol}")
        pts = gdf.geometry.representative_point()
        out = gpd.GeoDataFrame({
            "source_id": gdf[icol].astype(str) if icol else None,
            "height_m": (gdf[hcol].astype(float) * factor).round(2) if hcol else None,
            "elev_m": (gdf[ecol].astype(float) * factor).round(2) if ecol else None,
            "h3_cell": [h3.latlng_to_cell(p.y, p.x, cfg["H3_RES"]) for p in pts],
        }, geometry=[to_multi(g) for g in gdf.geometry], crs=4326).rename_geometry("geom")
        out = out[out.geometry.notna()]

        db.session.execute(text("TRUNCATE buildings RESTART IDENTITY"))
        db.session.commit()
        out.to_postgis("buildings", db.engine, if_exists="append", index=False, chunksize=10000)
        refresh_static_features()
        print(f"  loaded {len(out):,} buildings into PostGIS")

        work = os.path.join(cfg["DATA_DIR"], "work")
        os.makedirs(work, exist_ok=True)
        seq = os.path.join(work, "buildings.geojsonl")
        if os.path.exists(seq):
            os.remove(seq)
        tiles = out[["height_m", "geom"]].copy()
        tiles["height_m"] = tiles["height_m"].fillna(DEFAULT_RENDER_HEIGHT_M)
        tiles.to_file(seq, driver="GeoJSONSeq")
        print(f"  wrote {seq}")


if __name__ == "__main__":
    main()
