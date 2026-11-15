"""Searchable places: Google Maps places, USC campus buildings and campus POIs in one table.

Revision ID: 0004_places
Revises: 0003_pois
Create Date: 2026-11-14
"""
from alembic import op

revision = "0004_places"
down_revision = "0003_pois"
branch_labels = None
depends_on = None

UP = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE places (
  id           TEXT PRIMARY KEY,               -- gmaps:<place_id|source id> | bldg:<CODE> | poi:<pois.id>
  kind         TEXT NOT NULL,                  -- place | building | poi | address
  name         TEXT NOT NULL,
  name_norm    TEXT NOT NULL,                  -- text_norm.normalize(name)
  aliases      TEXT[] NOT NULL DEFAULT '{}',   -- display aliases (building code, short names, ...)
  search_text  TEXT NOT NULL,                  -- name_norm + normalized aliases; fuzzy matching runs on this
  category     TEXT NOT NULL,                  -- top-level id from services/place_taxonomy.CATEGORIES
  subcategory  TEXT,                           -- slug of the primary raw type (e.g. chicken_restaurant)
  categories   TEXT[] NOT NULL DEFAULT '{}',   -- raw Google type labels
  address      TEXT,
  lat          DOUBLE PRECISION NOT NULL,
  lng          DOUBLE PRECISION NOT NULL,
  geom         geometry(Point, 4326) NOT NULL,
  rating       REAL,
  review_count INTEGER NOT NULL DEFAULT 0,
  popularity   REAL NOT NULL DEFAULT 0,        -- 0..1 prominence (reviews + shrunk rating), precomputed
  hours        JSONB,                          -- {"0": [[open_min, close_min], ...], ...}, Monday = "0"; NULL = unknown
  detail       JSONB NOT NULL DEFAULT '{}',    -- phone, website, photo_url, reviews, hours_text, tier, ...
  source       TEXT,
  source_file  TEXT
);
CREATE INDEX ix_places_name_trgm ON places USING GIN (name_norm gin_trgm_ops);
CREATE INDEX ix_places_search_trgm ON places USING GIN (search_text gin_trgm_ops);
CREATE INDEX ix_places_name_prefix ON places (name_norm text_pattern_ops);
CREATE INDEX ix_places_geom ON places USING GIST (geom);
CREATE INDEX ix_places_category ON places (category);
CREATE INDEX ix_places_kind ON places (kind);
"""


def upgrade():
    op.execute(UP)


def downgrade():
    op.execute("DROP TABLE IF EXISTS places")
    # pg_trgm / unaccent are left installed: other objects may use them.
