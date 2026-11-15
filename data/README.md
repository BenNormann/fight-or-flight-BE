# data/

| Path | In git? | What |
|---|---|---|
| `pois/*.geojson` | yes | points of interest, loaded by `python -m scripts.load_pois` |
| `raw/usc_roads.osm.pbf` | yes | OSM roads clipped to the study circle + 1.5 km (`scripts/fetch_osm.sh`) |
| `raw/LARIAC6_Buildings_2020.gdb.zip` | no (Release) | LA County building outlines (`./scripts/fetch_data.sh`) |
| `tiles/buildings.pmtiles` | no (Release) | 3D building tiles |
| `checksums.sha256` | yes | checksums of the Release files |
| `work/` | no | downloads + intermediates, safe to delete |

## POIs (`pois/`)

Each file is a GeoJSON FeatureCollection in WGS84 `[lng, lat]`. Each feature needs a `type` property that matches a frontend POI type (`seating`, `study`, ...). `id`, `name` and `source` are optional; every other property is kept as-is and returned by `GET /api/v1/pois`. Drop a new file in and re-run the loader. Rows removed from a file are deleted on the next load.

### USC outdoor table groups

`pois/usc_outdoor_table_groups.geojson` has one Point per group of outdoor tables on the USC University Park campus (15 groups). Each feature has `type: "seating"` (matches the FE `seating` POI type), `table_count_est`, and `source`.

Method: Esri World Imagery (zoom 19) scanned for patio umbrellas and table clusters, then every point was reviewed by hand on a satellite map; 8 groups were added manually where imagery could not show them (shade, no umbrellas). Counts are estimates. Coordinates are WGS84 [lng, lat].

Imagery © Esri, Maxar, Earthstar Geographics; only derived coordinates are stored here.
