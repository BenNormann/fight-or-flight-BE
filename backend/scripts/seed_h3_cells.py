"""Populate h3_cells with every res-9 cell in the 10-mile study circle.

    docker compose exec api python -m scripts.seed_h3_cells
"""
import h3
from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.services.cell_features import refresh_static_features
from app.services.h3_utils import cell_centroid_wkt, cell_polygon_wkt, study_cells

UPSERT = text("""
INSERT INTO h3_cells (cell, res, parent_r6, geom, centroid)
VALUES (:cell, :res, :parent, ST_GeomFromText(:geom, 4326), ST_GeomFromText(:centroid, 4326))
ON CONFLICT (cell) DO UPDATE SET parent_r6 = EXCLUDED.parent_r6, geom = EXCLUDED.geom,
                                 centroid = EXCLUDED.centroid
""")


def main():
    app = create_app()
    with app.app_context():
        res, area_res = app.config["H3_RES"], app.config["H3_AREA_RES"]
        cells = study_cells()
        rows = [{"cell": c, "res": res, "parent": h3.cell_to_parent(c, area_res),
                 "geom": cell_polygon_wkt(c), "centroid": cell_centroid_wkt(c)} for c in cells]
        db.session.execute(UPSERT, rows)
        db.session.commit()
        refresh_static_features()
        print(f"seeded {len(rows)} res-{res} cells "
              f"({len({r['parent'] for r in rows})} res-{area_res} area cells)")


if __name__ == "__main__":
    main()
