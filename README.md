# fight-or-flight-BE

Backend for the USC safe-route project. It collects **space-time data** (light, weather, crime) for a 10-mile circle around USC, stores it in **PostgreSQL + PostGIS + TimescaleDB**, and serves it to:

- the **crime-risk model** in `algorithm/` (XGBoost, by Desh; see `algorithm/README.md`), which the API loads in-process and feeds with backend data, and
- the **frontend**, which gets 3D buildings as PMTiles, a risk heatmap, and JSON data layers.

```
LIVE (automatic, ingestor container)
  sun 1m · Open-Meteo 5m · NWS 10m · LAPD crime 60m ──upsert──┐
                                                               ▼
STATIC (one-time scripts, run by you)            PostgreSQL 16 + PostGIS + TimescaleDB
  County buildings (.gdb.zip) ─ build_buildings.sh ─▶  buildings, h3_cells features
        └─▶ data/tiles/buildings.pmtiles                       │
  OSM extract (.osm.pbf) ─ fetch_osm.sh + load_roads ▶ road_nodes, road_edges
                                                               ▼
       crime_incidents ─ export_crime_db ─▶ algorithm/data/crime.db
                                                               ▼
              Flask API (/api/v1) + risk model (algorithm/) ──▶ frontend / routing
```

## Quick start

```bash
./scripts/fetch_data.sh           # large data files from the GitHub Release (see "Data files")
cp .env.example .env              # optional; every value has a default
docker compose up -d --build      # db + api (runs migrations) + ingestor
docker compose exec api python -m scripts.seed_h3_cells    # 6,909 res-9 cells, 30 res-6 area cells
open http://localhost:5000/api/docs                        # Swagger UI
```

If ports 5432 or 5000 are already in use, set `DB_PORT` / `API_PORT` in `.env`.

That gives you the **live data**. On first start the crime ingestors backfill from 2024-01-01, which takes a few minutes and pulls about 350k incidents. Watch progress with `curl localhost:5000/api/v1/sources`.

**Buildings and roads are not loaded automatically.** They are one-time loads you run after `seed_h3_cells`; see [Static map layers](#static-map-layers). Until then, `/buildings*` and `/roads*` return empty results or 404, and the building/road columns in `/cells` and `/features` are 0.

```bash
docker compose run --rm tools bash scripts/build_buildings.sh   # ~3 min: buildings -> PostGIS (+ rebuilds PMTiles)
docker compose run --rm tools python -m scripts.load_roads      # ~3 min: OSM walk network -> PostGIS
docker compose exec api python -m scripts.load_pois              # seconds: data/pois/*.geojson -> PostGIS
# (only if data/raw/usc_10mi_roads.osm.pbf is missing: bash scripts/fetch_osm.sh first)
```

## Spatial model: H3

Every data point is keyed by an [H3](https://h3geo.org) hexagon, stored as a hex string, so all sources line up on `(h3_cell, time_bucket)`.

| Resolution | Cell size | Used for |
|---|---|---|
| **9** (fine) | ~0.12 km², 6,909 cells | crime, streetlights, buildings, features |
| **6** (area) | ~36 km², 30 cells | sun position and weather (area-wide signals) |

`h3_cells.parent_r6` links the two levels. The `ml_features` view broadcasts area signals down to their res-9 children.

## Database

| Object | Kind | Purpose |
|---|---|---|
| `observations` | hypertable (1-day chunks) | narrow `(h3_cell, metric, ts, source, value)`. New metrics need no migration. Upserts make polling idempotent. |
| `crime_incidents` | hypertable (30-day chunks) | point events with geometry + H3 cell |
| `obs_10min`, `obs_hourly` | continuous aggregates | avg/min/max/last per cell+metric, kept fresh by policies, real-time enabled |
| `crime_hourly` | continuous aggregate | incident counts per cell, hour, category |
| `ml_features` | view | one row per `(h3_cell, 10-min bucket)`: sun, darkness, weather, outages, static cell features (buildings, roads, POI counts), trailing-30-day crime count |
| `h3_cells` | PostGIS table | res-9 grid + static features (buildings, roads, streetlights) |
| `buildings`, `road_nodes`, `road_edges`, `pois`, `streetlights` | PostGIS tables | static layers, loaded by scripts |
| `ingest_runs` | table | ingestion audit log, shown in `/health` and `/sources` |

Policies: raw `observations` compress after 3 days and are dropped after 90 days. Aggregates are kept indefinitely. The schema lives in `backend/migrations/versions/` (`0001_initial`, `0002_roads`, `0003_pois`).

## Ingestion sources

| Source | Data | Interval | Notes |
|---|---|---|---|
| `sun` | `sun_altitude_deg`, `is_dark` | 1 min | computed locally with astral; civil twilight (−6°) = dark |
| `openmeteo` | temp, humidity, precip, cloud cover, wind | 5 min | free, no key; one batched call for all 30 area cells; upstream refreshes every ~15 min |
| `nws` | temp, humidity, visibility, wind, precip | 10 min | stations `KCQT,KLAX`; offline stations are skipped |
| `lapd` | crime 2024 | 60 min | legacy dataset `2nrs-mtv8` (frozen at end of 2024) |
| `lapd_nibrs` | crime 2025+ | 60 min | NIBRS dataset `k7nn-b2ep`, updated ~weekly; coordinates rounded to the hundred-block |
| `la311` | `streetlight_outages_open` | 30 min | disabled until `LA311_DATASET_ID` is set (MyLA311 publishes one dataset per year) |

Polling faster than a source publishes is harmless: writes are upserts. Published crime data lags by days, so treat it as history and training data. The minute-level signals are light and weather.

Add a source by subclassing `Ingestor` (`backend/app/ingestion/base.py`), implementing `fetch()` and `normalize()`, and registering it in `app/ingestion/__init__.py`.

## API (`/api/v1`, full docs at `/api/docs`)

| Endpoint | Purpose |
|---|---|
| `GET /health` | DB status + last run per ingestor |
| `GET /sources` | sources, intervals, last runs |
| `GET /cells?bbox=&format=json\|geojson` | res-9 cells + static features |
| `GET /observations?bbox=&cells=&metric=&from=&to=&bucket=raw\|1m\|10m\|1h` | time series |
| `GET /observations/latest?bbox=&metric=` | latest value per cell/metric, for live inference |
| `GET /observations/metrics` | which metrics exist |
| `GET /incidents?bbox=&from=&to=&category=` | crime points (default: last 30 days) |
| `GET /incidents/hourly?cells=&from=&to=` | hourly crime counts per cell |
| `GET /features?bbox=&from=&to=&format=json\|csv\|parquet` | **the ML feature matrix** |
| `GET /buildings.pmtiles` | 3D building vector tiles (supports HTTP range requests) |
| `GET /buildings?bbox=` | building GeoJSON for a small area |
| `GET /roads?bbox=` | road edges as GeoJSON for a small area |
| `GET /roads/graph?part=nodes\|edges&format=parquet\|csv\|json` | **full walk graph for routing** |
| `GET /pois?bbox=&type=seating,study` | points of interest as GeoJSON (extra attributes in `properties`) |
| `GET /pois/types` | POI types with counts |
| `GET /risk/status` | model loaded?, training dates, 2023 test metrics |
| `GET /risk/score?lat=&lng=\|cell=&hour=&date=\|at=` | **risk + top factors for a point**, plus everything the backend knows about that cell |
| `GET /risk/hotzones?hour=&date=&bbox=&min_risk=` | **heatmap**: risk for every modelled cell at an hour, plus `no_data_cells` |
| `GET /risk/edges?hour=&date=&lambda=&format=parquet\|csv\|json` | **route costs** for every walk-graph edge: `cost = length_m × (1 + λ·risk)` |
| `POST /admin/ingest/{source}` | run one ingestor now (`X-Admin-Token` if `ADMIN_TOKEN` is set) |
| `GET /auth/login?next=<FE url>` | full-page redirect to Google sign-in (see [Auth](#auth)) |
| `GET /auth/callback` | Google returns here; sets the session cookie, redirects to `next` |
| `GET /auth/me` | `{id, email, name, picture}` when signed in, `401` otherwise |
| `POST /auth/logout` | `204`, clears the session |

`bbox` is `minLng,minLat,maxLng,maxLat`. Times are ISO-8601, interpreted as UTC if no offset is given.

```bash
# training pull for the ML service
curl -o features.parquet "localhost:5000/api/v1/features?from=2026-11-01T00:00:00Z&format=parquet"
```

## Auth

Sign-in is Google OpenID Connect, limited to `@usc.edu` Workspace accounts. Reads stay public.

1. The FE sends the browser to `/api/v1/auth/login?next=<absolute FE URL>`.
2. Google signs the user in and returns to `/api/v1/auth/callback`. The backend checks the ID token
   (`email_verified`, email domain and Workspace `hd` must all be `usc.edu`), then sets the session
   cookie `fof_session` (Flask's signed cookie, httpOnly, 7 days) and redirects to `next`.
3. Refused or failed sign-in redirects to the FE's `/login?error=domain|cancelled|failed|unavailable`.
4. The FE asks `GET /api/v1/auth/me` who is signed in (`401` = signed out); `POST /api/v1/auth/logout` ends it.

`next` must be on an origin in `FRONTEND_ORIGINS` (otherwise it falls back to that list's first
origin), and CORS allows only those origins, with credentials. The session stores only the user's
public profile, keyed by Google's stable `sub`; there is no users table yet.

Setup (`.env`):

- `SECRET_KEY`: long random string (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
  The same value on every API process, or sessions break between them.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`: Google Cloud console > APIs & Services > Credentials >
  OAuth client ID (Web application). Authorized redirect URI: `http://localhost:5000/api/v1/auth/callback`
  locally, your API's `/api/v1/auth/callback` in production (also set `OAUTH_REDIRECT_URI` to it
  when behind a proxy).
- `FRONTEND_ORIGINS`: comma-separated FE origins. Default `http://localhost:5173,http://localhost:4173`.
- FE on a different site than the API (production): `SESSION_COOKIE_SAMESITE=None` and
  `SESSION_COOKIE_SECURE=true` (HTTPS only).
- `AUTH_DEV_LOGIN=true` (local only, never in production) adds `GET /api/v1/auth/dev/login?email=&next=`,
  which signs in any `@usc.edu` address without Google, for testing the FE end to end.

Without `SECRET_KEY` and the Google client, `/auth/me` answers `401` and `/auth/login` redirects back
with `error=unavailable`. Sign-in also stays disabled if `SECRET_KEY` is shorter than 32 characters, contains
spaces, or starts with `#` (an `.env` comment read as the value).

### Deploying auth (nginx + HTTPS, frontend on its own domain)

- **nginx must forward the scheme:** `proxy_set_header X-Forwarded-Proto $scheme;` and `proxy_set_header Host $host;`.
  The app trusts one proxy's `X-Forwarded-*` headers (werkzeug `ProxyFix`), so the callback it sends to Google
  is `https://...`. Without the header, Google refuses the plain-http callback (`redirect_uri_mismatch`).
- **Google console:** OAuth client (Web application) with these Authorized redirect URIs, exact match, no trailing slash:
  `https://<host>/api/v1/auth/callback` (production) and `http://localhost:5000/api/v1/auth/callback` (local).
  Set `OAUTH_REDIRECT_URI` to the production one.
- **Frontend on a different domain:** choose one.
  1. **Recommended: the frontend host proxies `/api`.** E.g. a Vercel rewrite of `/api/:path*` to
     `https://<backend host>/api/:path*`. The browser sees one origin, so the cookie is first-party and works in
     Safari/Firefox, CORS isn't involved, and `SESSION_COOKIE_SAMESITE=Lax` stays.
     Use `OAUTH_REDIRECT_URI=https://<frontend host>/api/v1/auth/callback` and register that URI in Google.
  2. **The frontend calls the API host directly.** Set `FRONTEND_ORIGINS=https://<frontend host>`,
     `SESSION_COOKIE_SAMESITE=None`, `SESSION_COOKIE_SECURE=true`, and `OAUTH_REDIRECT_URI=https://<backend host>/api/v1/auth/callback`.
     The cookie is then third-party: Safari (all iPhones) and Firefox block it by default, so sign-in can
     appear to succeed while `/auth/me` keeps answering `401`.
- In both cases, put the frontend's URL in `FRONTEND_ORIGINS` (it is also the allow-list for `next`). CORS on
  `/api/*` only allows those origins.
- `authlib` is a new dependency: deploy with `docker compose up -d --build`.

## Risk model integration

The crime-risk model lives in `algorithm/` (training, evaluation and the model card are in `algorithm/README.md`). The API imports `algorithm.scoring.score` directly. Each gunicorn worker loads the model plus its crime history once at startup, which takes ~4 s and ~600 MB per worker. That's why `gunicorn.conf.py` runs 2 workers × 4 threads.

**Data the backend gives the model:**

| Model input | Source |
|---|---|
| crime history: cell 7/30/90-day counts, severity, same-hour, neighbours, area-wide | `crime_incidents` (LAPD `2nrs-mtv8`, 2020–2024) exported to `algorithm/data/crime.db` by `scripts/export_crime_db.py`. The backend is the only crime downloader, and its output matches the model author's own download (99.2% of cell-hours identical, rank correlation 1.0). |
| `is_dark` | live sun ingestor, for current-hour requests; other hours use the model's own astral calculation |
| time, location, blue-light distance | computed inside the model |

`/risk/score` builds a context dict for the cell from the backend database: weather, darkness, lit-road share, streetlights, buildings, roads, POIs, and live crime counts including NIBRS 2025+. Every key whose name matches a model feature is passed to the model as an input. The response lists them under `model_inputs_from_backend`; everything else comes back under `context`. The model only uses `is_dark` from this list today. If it's retrained with backend features using the same names as `/features` (e.g. `temp_c`, `lit_road_length_m`, `seating_count`), those values flow in with no backend change.

Known limits:
- The model anchors its history at the end of 2023, because LAPD's 2024 NIBRS switch halved the old dataset's counts. NIBRS 2025+ data is therefore context only until the model is retrained on it.
- About half the cells are other police jurisdictions. They return `no_data`, which means unknown, not safe.
- Blue-light phone locations in `algorithm/common/geo.py` are placeholders.

**Routing:** `/risk/edges` gives a cost per edge of the walk graph for an hour. Join it to `/roads/graph?part=edges` on `(u, v, key)` and run A* on `cost` for the safest route, or on `length_m` for the fastest. Cells without data get a neutral risk of 0.5, and `no_data_share` flags how much of an edge they cover.

## Data files

GitHub rejects files over 100 MB, so the two large files live in a **GitHub Release** (tag `data-v1`). Release assets can be up to 2 GB each and don't use Git LFS quota. Everything else is in git.

| File | Size | Where | Purpose |
|---|---|---|---|
| `data/raw/LARIAC6_Buildings_2020.gdb.zip` | 412 MB | Release | building source for `build_buildings.sh` |
| `data/tiles/buildings.pmtiles` | 97 MB | Release | prebuilt 3D tiles; serve as-is or rebuild |
| `data/raw/usc_10mi_roads.osm.pbf` | 12 MB | git | OSM roads clipped to the circle |
| `data/checksums.sha256` | — | git | SHA-256 of the Release files |
| `data/work/` | ~1 GB | ignored | downloads + intermediates; safe to delete |
| `algorithm/data/crime.db` | 122 MB | ignored | model crime history, generated by `scripts/export_crime_db.py` |

`./scripts/fetch_data.sh` downloads the Release files into place and verifies their checksums. It resumes partial downloads and skips files already present. Pass `--no-tiles` to skip the tiles and build them yourself.

To update the data, regenerate the files, then run `./scripts/publish_data_release.sh` (needs `gh auth login`; use `DATA_RELEASE_TAG=data-v2` for a new version). It uploads the files and rewrites `data/checksums.sha256`, which you then commit.

## Static map layers

These change rarely, so they are loaded by scripts instead of the ingestor. Re-running a script replaces that layer (TRUNCATE + load) and recomputes the per-cell features in `h3_cells`. Use the `tools` container: it has tippecanoe and runs as your user.

| Layer | Source | Script | Result |
|---|---|---|---|
| H3 grid | computed | `docker compose exec api python -m scripts.seed_h3_cells` | `h3_cells` (run this first) |
| Buildings | LA County LARIAC6 Building Outlines (file in repo) | `docker compose run --rm tools bash scripts/build_buildings.sh` | `buildings` table, `data/tiles/buildings.pmtiles` |
| Roads | OpenStreetMap extract (file in repo) | `docker compose run --rm tools python -m scripts.load_roads` | `road_nodes`, `road_edges` |
| POIs | `data/pois/*.geojson` (in git) | `docker compose exec api python -m scripts.load_pois` | `pois` |
| Streetlights | LA GeoHub export (optional, bring your own file) | `docker compose exec api python -m scripts.load_streetlights /data/raw/<file>` | `streetlights` |

### Buildings

The source file is `data/raw/LARIAC6_Buildings_2020.gdb.zip` (412 MB, from the data Release; see [Data files](#data-files)). It came from the [Countywide Building Outlines](https://www.arcgis.com/home/item.html?id=e6a1e375e12a4fe6849d896a26ec028a) item: 3.3M footprints countywide, CRS EPSG:6424 (CA State Plane, US feet).

`build_buildings.sh` does the following:
1. Opens the zipped geodatabase in place (no unzip), using layer `LARIAC6_BUILDINGS_2020` (skips the `*_DELETED_*` layer).
2. Keeps `CODE = Building`, dropping `Courtyard` and `Free Standing Solar Structure`. Override with `--codes`.
3. Clips to the 16.09 km circle using each footprint's interior point. That leaves **969,395 buildings**.
4. Converts `HEIGHT`/`ELEV` from feet to metres. 97.6% have a height; the max is 310 m (US Bank Tower).
5. Loads PostGIS and tags each building with its res-9 cell, filling `h3_cells.building_count` / `avg_height_m`.
6. Runs tippecanoe to write `data/tiles/buildings.pmtiles` (~100 MB, zoom 10–16, layer `buildings`, property `height_m`). Footprints with no height get 4 m in the tiles only, so they still extrude.

**Map data stays in the backend.** Shipping ~1M footprints to the browser as GeoJSON would mean ~450 MB. The frontend loads only the visible tiles from `GET /api/v1/buildings.pmtiles`, which supports range requests. Render with MapLibre GL plus the [pmtiles](https://github.com/protomaps/PMTiles) protocol: a `fill-extrusion` layer on source-layer `buildings`, with `fill-extrusion-height: ["get", "height_m"]`.

### Roads

The County file contains no roads, so roads come from **OpenStreetMap**: streets plus sidewalks, footpaths and campus paths. That suits pedestrian routing better than the County street centerlines. The road graph is built from a local file, not the public Overpass API, which proved too unreliable (502s and hung requests).

1. `scripts/fetch_osm.sh` (one-time; the output is committed):
   - Downloads Geofabrik's Southern California extract (~640 MB, resumable, kept in `data/work/` and ignored by git).
   - Clips it to the circle with `osmium extract` and keeps only `highway=*` ways.
   - Writes `data/raw/usc_10mi_roads.osm.pbf` (12 MB, committed to git). Re-run it whenever you want fresher OSM data.
2. `scripts/load_roads.py` builds the network and loads it:
   - Uses OSMnx with an OSMnx-style "walk" filter that drops motorways, private access, `foot=no`, construction and so on.
   - Keeps the largest connected component, simplifies the graph, and makes it undirected.
   - Loads the result into PostGIS.

What gets stored:
- `road_nodes(osmid, geom, street_count, h3_cell)` holds intersections and path ends.
- `road_edges(u, v, key, name, highway, lit, sidewalk, footway, length_m, geom, h3_cell, h3_cells[])`:
  - `h3_cell` is the cell at the edge's midpoint.
  - `h3_cells` lists every res-9 cell the edge passes through, sampled every 50 m. The ML service can weight an edge by `length_m × risk(cells)`.
  - `lit` is OSM's streetlight tag (`yes`/`no`/…). It's often missing, and NULL means unknown.
- Per-cell features go into `h3_cells`: `road_length_m`, `lit_road_length_m`, `intersection_count`. They are also exposed through `/cells` and `/features`.

For the ML service, pull the full graph with `GET /api/v1/roads/graph?part=nodes` and `?part=edges` (Parquet by default), then build e.g. a networkx graph keyed on `osmid`.

### POIs

Campus points of interest, such as outdoor seating and study spots, live as GeoJSON files in `data/pois/` (format in `data/README.md`). `load_pois` does the following:
- Upserts every file into `pois(id, type, name, source, source_file, props, geom, h3_cell)`. `props` keeps any extra attributes, such as `table_count_est`.
- Deletes rows that were removed from a file.
- Recomputes `h3_cells.poi_counts`, e.g. `{"seating": 4}`.

The frontend reads `GET /api/v1/pois?type=seating`. The ML feature matrix gets `seating_count` and `poi_count` per cell.

Current data: `usc_outdoor_table_groups.geojson`, 15 outdoor table groups found from satellite imagery and checked by hand (PR #1).

## Development

```bash
docker compose run --rm --no-deps api pytest -q    # unit tests (no DB needed)
docker compose logs -f ingestor
docker compose exec db psql -U safe -d saferoute
```
