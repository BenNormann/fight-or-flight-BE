# USC outdoor table groups

`usc_outdoor_table_groups.geojson` — one Point per group of outdoor tables on the USC University Park campus (15 groups). Each feature has `type: "seating"` (matches the FE `seating` POI type), `table_count_est`, and `source`.

Method: Esri World Imagery (zoom 19) scanned for patio umbrellas and table clusters, then every point was reviewed by hand on a satellite map; 8 groups were added manually where imagery could not show them (shade, no umbrellas). Counts are estimates. Coordinates are WGS84 [lng, lat].

Imagery © Esri, Maxar, Earthstar Geographics; only derived coordinates are stored here.
