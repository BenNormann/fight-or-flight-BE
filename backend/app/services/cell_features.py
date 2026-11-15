"""Recompute static per-cell features after loading buildings / streetlights / roads / POIs."""
from sqlalchemy import text

from ..extensions import db

# OSM lit=* values that mean "lit at night" (anything except no/disused/unknown)
LIT_VALUES = "('yes', '24/7', 'automatic', 'limited', 'interval', 'sunset-sunrise', 'dusk-dawn')"

REFRESH_SQL = f"""
UPDATE h3_cells c SET
  building_count     = COALESCE(b.n, 0),
  avg_height_m       = b.avg_h,
  streetlight_count  = COALESCE(s.n, 0),
  road_length_m      = COALESCE(r.len, 0),
  lit_road_length_m  = COALESCE(r.lit_len, 0),
  intersection_count = COALESCE(i.n, 0),
  poi_counts         = COALESCE(p.counts, '{{}}'::jsonb)
FROM h3_cells c2
LEFT JOIN (SELECT h3_cell, count(*) n, avg(height_m) avg_h FROM buildings GROUP BY h3_cell) b
       ON b.h3_cell = c2.cell
LEFT JOIN (SELECT h3_cell, count(*) n FROM streetlights GROUP BY h3_cell) s
       ON s.h3_cell = c2.cell
LEFT JOIN (SELECT h3_cell, sum(length_m) len,
                  sum(length_m) FILTER (WHERE lit IN {LIT_VALUES}) lit_len
           FROM road_edges GROUP BY h3_cell) r
       ON r.h3_cell = c2.cell
LEFT JOIN (SELECT h3_cell, count(*) n FROM road_nodes WHERE street_count >= 3 GROUP BY h3_cell) i
       ON i.h3_cell = c2.cell
LEFT JOIN (SELECT h3_cell, jsonb_object_agg(type, n) counts
           FROM (SELECT h3_cell, type, count(*) n FROM pois GROUP BY 1, 2) x GROUP BY h3_cell) p
       ON p.h3_cell = c2.cell
WHERE c.cell = c2.cell
"""


def refresh_static_features():
    db.session.execute(text(REFRESH_SQL))
    db.session.commit()
