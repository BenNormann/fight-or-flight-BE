# data/

| Path | In git? | What |
|---|---|---|
| `pois/*.geojson` | yes | points of interest, loaded by `python -m scripts.load_pois` (and into search by `load_places`) |
| `places/usc_buildings.geojson` | yes | USC campus buildings with codes, for search (`python -m scripts.load_places`) |
| `usc_google_maps_places.geojson` | from the `data/usc-google-maps-places` branch (23 MB) | Google Maps places for search (`python -m scripts.load_places`); schema in `GOOGLE_MAPS_PLACES.md` on that branch |
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

## Search places (`places` table)

`python -m scripts.load_places` fills the `places` table that place search runs on, from three sources:

- `usc_google_maps_places.geojson` (kind `place`): until that branch is merged, extract it with
  `git show fork/data/usc-google-maps-places:data/usc_google_maps_places.geojson > data/usc_google_maps_places.geojson`.
  Places outside the 5-mile study area are skipped (the file reaches 10 miles); `--all-areas` keeps them.
  Read as UTF-8: hours use U+202F before AM/PM and an en dash between times.
- `places/usc_buildings.geojson` (kind `building`): see below.
- `pois/*.geojson` (kind `poi`).

### USC campus buildings

`places/usc_buildings.geojson` has one Point per USC building on and around the University Park campus (194). Each feature has `code` (USC's three-letter building code, e.g. `DML`), `name`, `aliases` (everyday names), `category` (`campus`, `study` for libraries, `parking` for structures), `subcategory`, `address`, `usc_type`, `entrances` (`[lng, lat]` of each mapped entrance) and `source`.

Method: built by `python -m scripts.build_usc_buildings`. Codes, names, addresses, centre points (the `LAT`/`LON` fields) and entrances come from USC Facilities Planning & Management's public GIS (`https://fpmgis.usc.edu/server/rest/services/USC_FPM/`, the `Building` and `BuildingEntrance` layers). It keeps sites `UPC` and `UPCOFF` and drops demolished or sold buildings, gates, vaults and trailers. Names are title-cased from FPM's upper case. Aliases add OpenStreetMap names for the same buildings, matched by code or by the OSM building's centre falling inside the FPM footprint, plus a short hand-kept list of common names (for example `VKC` for the renamed Von KleinSmid Center, now DMC).

OSM data © OpenStreetMap contributors, ODbL.
