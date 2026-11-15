"""Load streetlight point locations (e.g. LA GeoHub / Bureau of Street Lighting export).

    python -m scripts.load_streetlights data/raw/streetlights.geojson [--type-col COLUMN]
"""
import argparse

import geopandas as gpd
import h3
from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.services.cell_features import refresh_static_features
from app.services.h3_utils import study_cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--type-col", default=None)
    ap.add_argument("--id-col", default=None)
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        res = app.config["H3_RES"]
        valid = set(study_cells())
        gdf = gpd.read_file(args.path, engine="pyogrio").to_crs(4326)
        gdf = gdf[gdf.geometry.notna() & (gdf.geometry.geom_type == "Point")]
        gdf["h3_cell"] = [h3.latlng_to_cell(p.y, p.x, res) for p in gdf.geometry]
        gdf = gdf[gdf["h3_cell"].isin(valid)]
        out = gpd.GeoDataFrame({
            "source_id": gdf[args.id_col].astype(str) if args.id_col else None,
            "light_type": gdf[args.type_col].astype(str) if args.type_col else None,
            "h3_cell": gdf["h3_cell"],
        }, geometry=gdf.geometry.values, crs=4326).rename_geometry("geom")

        db.session.execute(text("TRUNCATE streetlights RESTART IDENTITY"))
        db.session.commit()
        out.to_postgis("streetlights", db.engine, if_exists="append", index=False, chunksize=10000)
        refresh_static_features()
        print(f"loaded {len(out):,} streetlights")


if __name__ == "__main__":
    main()
