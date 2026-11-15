"""Initial schema: PostGIS static tables, TimescaleDB hypertables,
continuous aggregates, compression/retention policies, ML feature view.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-14
"""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

TABLES = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------- static / slowly changing ----------
CREATE TABLE h3_cells (
  cell              VARCHAR(16) PRIMARY KEY,
  res               SMALLINT NOT NULL,
  parent_r6         VARCHAR(16) NOT NULL,
  geom              geometry(Polygon, 4326) NOT NULL,
  centroid          geometry(Point, 4326) NOT NULL,
  building_count    INTEGER NOT NULL DEFAULT 0,
  avg_height_m      DOUBLE PRECISION,
  streetlight_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_h3_cells_geom ON h3_cells USING GIST (geom);
CREATE INDEX ix_h3_cells_parent ON h3_cells (parent_r6);

CREATE TABLE buildings (
  id        BIGSERIAL PRIMARY KEY,
  source_id TEXT,
  geom      geometry(MultiPolygon, 4326) NOT NULL,
  height_m  DOUBLE PRECISION,
  elev_m    DOUBLE PRECISION,
  h3_cell   VARCHAR(16)
);
CREATE INDEX ix_buildings_geom ON buildings USING GIST (geom);
CREATE INDEX ix_buildings_h3 ON buildings (h3_cell);

CREATE TABLE streetlights (
  id         BIGSERIAL PRIMARY KEY,
  source_id  TEXT,
  geom       geometry(Point, 4326) NOT NULL,
  light_type TEXT,
  h3_cell    VARCHAR(16)
);
CREATE INDEX ix_streetlights_geom ON streetlights USING GIST (geom);
CREATE INDEX ix_streetlights_h3 ON streetlights (h3_cell);

CREATE TABLE ingest_runs (
  id          BIGSERIAL PRIMARY KEY,
  source      TEXT NOT NULL,
  started_at  TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  status      TEXT NOT NULL,
  rows        INTEGER,
  error       TEXT
);
CREATE INDEX ix_ingest_runs_source ON ingest_runs (source, started_at DESC);

-- ---------- space-time (hypertables) ----------
-- Narrow format: new metrics/sources need no migration.
-- PK doubles as the upsert target and the main (cell, metric, time) lookup index.
CREATE TABLE observations (
  h3_cell VARCHAR(16) NOT NULL,
  metric  TEXT NOT NULL,
  ts      TIMESTAMPTZ NOT NULL,
  source  TEXT NOT NULL,
  value   DOUBLE PRECISION NOT NULL,
  meta    JSONB,
  PRIMARY KEY (h3_cell, metric, ts, source)
);
SELECT create_hypertable('observations', 'ts', chunk_time_interval => INTERVAL '1 day');

CREATE TABLE crime_incidents (
  source      TEXT NOT NULL,
  external_id TEXT NOT NULL,
  ts          TIMESTAMPTZ NOT NULL,
  geom        geometry(Point, 4326) NOT NULL,
  h3_cell     VARCHAR(16) NOT NULL,
  category    TEXT,
  crime_code  TEXT,
  severity    SMALLINT,
  raw         JSONB,
  PRIMARY KEY (source, external_id, ts)
);
SELECT create_hypertable('crime_incidents', 'ts', chunk_time_interval => INTERVAL '30 days');
CREATE INDEX ix_crime_h3_ts ON crime_incidents (h3_cell, ts DESC);
CREATE INDEX ix_crime_geom ON crime_incidents USING GIST (geom);
"""

# Continuous aggregates cannot be created inside a transaction.
CAGGS = [
    """
    CREATE MATERIALIZED VIEW obs_10min
    WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
    SELECT time_bucket(INTERVAL '10 minutes', ts) AS bucket, h3_cell, source, metric,
           avg(value) AS avg, min(value) AS min, max(value) AS max,
           last(value, ts) AS last, count(*) AS n
    FROM observations
    GROUP BY bucket, h3_cell, source, metric
    WITH NO DATA
    """,
    """
    CREATE MATERIALIZED VIEW obs_hourly
    WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
    SELECT time_bucket(INTERVAL '1 hour', ts) AS bucket, h3_cell, source, metric,
           avg(value) AS avg, min(value) AS min, max(value) AS max,
           last(value, ts) AS last, count(*) AS n
    FROM observations
    GROUP BY bucket, h3_cell, source, metric
    WITH NO DATA
    """,
    """
    CREATE MATERIALIZED VIEW crime_hourly
    WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
    SELECT time_bucket(INTERVAL '1 hour', ts) AS bucket, h3_cell, category, count(*) AS n
    FROM crime_incidents
    GROUP BY bucket, h3_cell, category
    WITH NO DATA
    """,
]

POLICIES = """
SELECT add_continuous_aggregate_policy('obs_10min',
  start_offset => INTERVAL '1 day', end_offset => INTERVAL '10 minutes',
  schedule_interval => INTERVAL '5 minutes');
SELECT add_continuous_aggregate_policy('obs_hourly',
  start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '30 minutes');
SELECT add_continuous_aggregate_policy('crime_hourly',
  start_offset => INTERVAL '60 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');

ALTER TABLE observations SET (
  timescaledb.compress,
  timescaledb.compress_segmentby = 'h3_cell, metric, source',
  timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('observations', INTERVAL '3 days');
SELECT add_retention_policy('observations', INTERVAL '90 days');
"""

# One row per (fine cell, 10-min bucket). Area-level signals (sun, weather at res 6)
# are broadcast to their res-9 children via parent_r6.
ML_FEATURES_VIEW = """
CREATE VIEW ml_features AS
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
       cr.crime_count_30d
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


def upgrade():
    op.execute(TABLES)
    with op.get_context().autocommit_block():
        for stmt in CAGGS:
            op.execute(stmt)
    op.execute(POLICIES)
    op.execute(ML_FEATURES_VIEW)


def downgrade():
    op.execute("DROP VIEW IF EXISTS ml_features")
    with op.get_context().autocommit_block():
        for name in ("crime_hourly", "obs_hourly", "obs_10min"):
            op.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
    op.execute(
        "DROP TABLE IF EXISTS crime_incidents, observations, ingest_runs, streetlights, buildings, h3_cells CASCADE"
    )
