"""Load every data/pois/*.geojson file into the `pois` table (upsert by id).

    docker compose exec api python -m scripts.load_pois [file.geojson ...]

Re-running is safe: rows are upserted, and rows that were removed from a file are
deleted. Then the per-cell `poi_counts` are recomputed.
"""
import argparse
import glob
import json
import os

import h3

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.services.cell_features import refresh_static_features
from app.services.h3_utils import in_study_area
from app.services.pois import parse_feature_collection

UPSERT = text("""
INSERT INTO pois (id, type, name, source, source_file, props, geom, h3_cell)
VALUES (:id, :type, :name, :source, :source_file, CAST(:props AS jsonb),
        ST_SetSRID(ST_GeomFromGeoJSON(:geom), 4326), :h3_cell)
ON CONFLICT (id) DO UPDATE SET type = EXCLUDED.type, name = EXCLUDED.name, source = EXCLUDED.source,
  source_file = EXCLUDED.source_file, props = EXCLUDED.props, geom = EXCLUDED.geom, h3_cell = EXCLUDED.h3_cell
""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="default: all data/pois/*.geojson")
    ap.add_argument("--type", help="default type for features without a 'type' property")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        files = args.files or sorted(glob.glob(os.path.join(app.config["DATA_DIR"], "pois", "*.geojson")))
        if not files:
            print("no POI files found in data/pois/")
            return
        for path in files:
            with open(path) as f:
                rows = parse_feature_collection(json.load(f), path, args.type)
            outside = [r["id"] for r in rows if not in_study_area(*h3.cell_to_latlng(r["h3_cell"]))]
            if rows:
                db.session.execute(UPSERT, rows)
            db.session.execute(
                text("DELETE FROM pois WHERE source_file = :f AND NOT (id = ANY(:ids))"),
                {"f": os.path.basename(path), "ids": [r["id"] for r in rows]},
            )
            db.session.commit()
            types = sorted({r["type"] for r in rows})
            print(f"{os.path.basename(path)}: {len(rows)} POIs ({', '.join(types)})"
                  + (f"; {len(outside)} outside the study area: {outside[:5]}" if outside else ""))
        refresh_static_features()


if __name__ == "__main__":
    main()
