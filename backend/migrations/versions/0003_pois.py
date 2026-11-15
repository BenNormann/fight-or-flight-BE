"""Points of interest (seating, study spots, ...) + per-cell POI counts.

Revision ID: 0003_pois
Revises: 0002_roads
Create Date: 2026-11-14
"""
from alembic import op

revision = "0003_pois"
down_revision = "0002_roads"
branch_labels = None
depends_on = None

UP = """
CREATE TABLE pois (
  id          TEXT PRIMARY KEY,           -- stable id from the GeoJSON (e.g. usc-tbl-001)
  type        TEXT NOT NULL,              -- seating | study | ... (matches the frontend POI types)
  name        TEXT,
  source      TEXT,
  source_file TEXT NOT NULL,              -- data/pois/<file>.geojson the row was loaded from
  props       JSONB NOT NULL DEFAULT '{}',-- every other GeoJSON property (e.g. table_count_est)
  geom        geometry(Geometry, 4326) NOT NULL,
  h3_cell     VARCHAR(16)
);
CREATE INDEX ix_pois_geom ON pois USING GIST (geom);
CREATE INDEX ix_pois_type ON pois (type);
CREATE INDEX ix_pois_h3 ON pois (h3_cell);

ALTER TABLE h3_cells ADD COLUMN poi_counts JSONB NOT NULL DEFAULT '{}';  -- {"seating": 2, ...}
"""


def _view(path_name):
    import importlib.util
    import os

    spec = importlib.util.spec_from_file_location("m", os.path.join(os.path.dirname(__file__), path_name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.VIEW


def upgrade():
    op.execute(UP)
    # Append POI features to ml_features (CREATE OR REPLACE allows appending columns only)
    view = _view("0002_roads.py").replace(
        "c.road_length_m, c.lit_road_length_m, c.intersection_count\n",
        "c.road_length_m, c.lit_road_length_m, c.intersection_count,\n"
        "       COALESCE((c.poi_counts->>'seating')::int, 0) AS seating_count,\n"
        "       COALESCE((SELECT sum(v::int) FROM jsonb_each_text(c.poi_counts) AS t(k, v)), 0)::int AS poi_count\n",
    )
    assert "seating_count" in view
    op.execute(view)


def downgrade():
    op.execute("DROP VIEW IF EXISTS ml_features")
    op.execute(_view("0002_roads.py"))
    op.execute("ALTER TABLE h3_cells DROP COLUMN poi_counts")
    op.execute("DROP TABLE IF EXISTS pois")
