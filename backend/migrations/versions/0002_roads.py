"""Walkable road network (OpenStreetMap) + per-cell road features.

Revision ID: 0002_roads
Revises: 0001_initial
Create Date: 2026-11-14
"""
from alembic import op

revision = "0002_roads"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

UP = """
CREATE TABLE road_nodes (
  osmid        BIGINT PRIMARY KEY,
  geom         geometry(Point, 4326) NOT NULL,
  street_count SMALLINT,
  h3_cell      VARCHAR(16)
);
CREATE INDEX ix_road_nodes_geom ON road_nodes USING GIST (geom);
CREATE INDEX ix_road_nodes_h3 ON road_nodes (h3_cell);

-- Undirected walk graph: one row per edge, (u, v, key) identifies it.
CREATE TABLE road_edges (
  id        BIGSERIAL PRIMARY KEY,
  u         BIGINT NOT NULL,
  v         BIGINT NOT NULL,
  key       SMALLINT NOT NULL DEFAULT 0,
  osmid     TEXT,
  name      TEXT,
  highway   TEXT,
  lit       TEXT,               -- OSM lit=* tag (yes/no/24/7/automatic/...), NULL = unknown
  sidewalk  TEXT,
  footway   TEXT,
  length_m  DOUBLE PRECISION NOT NULL,
  geom      geometry(LineString, 4326) NOT NULL,
  h3_cell   VARCHAR(16),        -- cell of the edge midpoint
  h3_cells  VARCHAR(16)[],      -- every res-9 cell the edge passes through (for risk weighting)
  UNIQUE (u, v, key)
);
CREATE INDEX ix_road_edges_geom ON road_edges USING GIST (geom);
CREATE INDEX ix_road_edges_h3 ON road_edges (h3_cell);
CREATE INDEX ix_road_edges_h3_cells ON road_edges USING GIN (h3_cells);

ALTER TABLE h3_cells
  ADD COLUMN road_length_m      DOUBLE PRECISION NOT NULL DEFAULT 0,
  ADD COLUMN lit_road_length_m  DOUBLE PRECISION NOT NULL DEFAULT 0,
  ADD COLUMN intersection_count INTEGER NOT NULL DEFAULT 0;
"""

# Same as 0001's view with the road features appended (CREATE OR REPLACE only allows appending).
VIEW = """
CREATE OR REPLACE VIEW ml_features AS
WITH obs AS (
  SELECT c.cell, o.bucket,
    avg(o.avg) FILTER (WHERE o.metric = 'sun_altitude_deg')        AS sun_altitude_deg,
    max(o.last) FILTER (WHERE o.metric = 'is_dark')                AS is_dark,
    avg(o.avg) FILTER (WHERE o.metric = 'temp_c')                  AS temp_c,
    avg(o.avg) FILTER (WHERE o.metric = 'humidity_pct')            AS humidity_pct,
    avg(o.avg) FILTER (WHERE o.metric = 'precip_mm')               AS precip_mm,
    avg(o.avg) FILTER (WHERE o.metric = 'cloud_cover_pct')         AS cloud_cover_pct,
    avg(o.avg) FILTER (WHERE o.metric = 'wind_speed_kmh')          AS wind_speed_kmh,
    avg(o.avg) FILTER (WHERE o.metric = 'visibility_m')            AS visibility_m,
    max(o.last) FILTER (WHERE o.metric = 'streetlight_outages_open') AS streetlight_outages_open
  FROM h3_cells c
  JOIN obs_10min o ON o.h3_cell IN (c.cell, c.parent_r6)
  GROUP BY c.cell, o.bucket
)
SELECT obs.cell AS h3_cell, obs.bucket,
       obs.sun_altitude_deg, obs.is_dark, obs.temp_c, obs.humidity_pct, obs.precip_mm,
       obs.cloud_cover_pct, obs.wind_speed_kmh, obs.visibility_m,
       COALESCE(obs.streetlight_outages_open, 0) AS streetlight_outages_open,
       c.building_count, c.avg_height_m, c.streetlight_count,
       cr.crime_count_30d,
       c.road_length_m, c.lit_road_length_m, c.intersection_count
FROM obs
JOIN h3_cells c ON c.cell = obs.cell
LEFT JOIN LATERAL (
  SELECT COALESCE(sum(ch.n), 0)::int AS crime_count_30d
  FROM crime_hourly ch
  WHERE ch.h3_cell = obs.cell
    AND ch.bucket >= obs.bucket - INTERVAL '30 days'
    AND ch.bucket <  obs.bucket
) cr ON true;
"""


def _initial_view_sql():
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(__file__), "0001_initial.py")
    spec = importlib.util.spec_from_file_location("m0001", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ML_FEATURES_VIEW


def upgrade():
    op.execute(UP)
    op.execute(VIEW)


def downgrade():
    op.execute("DROP VIEW IF EXISTS ml_features")
    op.execute("ALTER TABLE h3_cells DROP COLUMN road_length_m, DROP COLUMN lit_road_length_m, "
               "DROP COLUMN intersection_count")
    op.execute("DROP TABLE IF EXISTS road_edges, road_nodes")
    op.execute(_initial_view_sql())
