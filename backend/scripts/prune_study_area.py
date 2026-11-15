"""Remove data outside the study circle, e.g. after shrinking RADIUS_KM.

    docker compose exec api python -m scripts.seed_h3_cells            # grid first
    docker compose exec api python -m scripts.prune_study_area         # dry run: counts only
    docker compose exec api python -m scripts.prune_study_area --yes   # delete

Rows are kept by the same rule the ingestors use: their res-9 cell must be one of
h3_cells (crime, buildings, streetlights, POIs). Roads are kept out to RADIUS_KM +
ROAD_MARGIN_KM, so routes near the edge still have streets. Observations are left
alone (they expire through the 90-day retention policy). Everything removed can be
re-created by the loaders/ingestors with a larger radius.
"""
import argparse
import datetime as dt

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.services.cell_features import refresh_static_features

CELL_TABLES = ("crime_incidents", "buildings", "streetlights", "pois")
ROADS_OUTSIDE = """
    NOT ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :radius_m)
"""
ORPHAN_NODES = """
    NOT EXISTS (SELECT 1 FROM road_edges e WHERE e.u = road_nodes.osmid)
    AND NOT EXISTS (SELECT 1 FROM road_edges e WHERE e.v = road_nodes.osmid)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        c = app.config
        n_cells = db.session.execute(text("SELECT count(*) FROM h3_cells")).scalar()
        if not n_cells:
            raise SystemExit("h3_cells is empty: run scripts.seed_h3_cells first")
        road = {"lat": c["USC_LAT"], "lng": c["USC_LNG"], "radius_m": (c["RADIUS_KM"] + c["ROAD_MARGIN_KM"]) * 1000}
        cell_outside = "h3_cell IS NULL OR h3_cell NOT IN (SELECT cell FROM h3_cells)"

        print(f"study circle {c['RADIUS_KM']} km ({n_cells:,} cells); roads to "
              f"{c['RADIUS_KM'] + c['ROAD_MARGIN_KM']:.2f} km. {'DELETING' if args.yes else 'Dry run'}:")
        plan = [(t, cell_outside, {}) for t in CELL_TABLES] + [("road_edges", ROADS_OUTSIDE, road)]
        for table, where, params in plan:
            total = db.session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            outside = db.session.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params).scalar()
            print(f"  {table:16} {outside:>9,} of {total:>9,} outside")
            if args.yes and outside:
                db.session.execute(text(f"DELETE FROM {table} WHERE {where}"), params)
        if args.yes:
            orphans = db.session.execute(text(f"DELETE FROM road_nodes WHERE {ORPHAN_NODES}")).rowcount
            print(f"  road_nodes       {orphans:>9,} unreferenced nodes removed")
            db.session.commit()
            # crime_hourly is a continuous aggregate: recompute it over the deleted history
            with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(text("CALL refresh_continuous_aggregate('crime_hourly', :lo, :hi)"),
                             {"lo": dt.datetime(2019, 1, 1), "hi": dt.datetime.now(dt.timezone.utc)})
            refresh_static_features()
            print("done. Restart the API so the route planner reloads the walk graph: docker compose restart api")
        else:
            print("re-run with --yes to delete")


if __name__ == "__main__":
    main()
