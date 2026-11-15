#!/usr/bin/env bash
# Download the Geofabrik SoCal OSM extract once, clip it to the study circle and keep
# only highway=* ways. Result: data/raw/usc_10mi_roads.osm.pbf (small, kept in the repo).
#   docker compose run --rm tools bash scripts/fetch_osm.sh
set -euo pipefail
DATA_DIR="${DATA_DIR:-/data}"
URL="${OSM_EXTRACT_URL:-https://download.geofabrik.de/north-america/us/california/socal-latest.osm.pbf}"
WORK="$DATA_DIR/work"
OUT="$DATA_DIR/raw/usc_10mi_roads.osm.pbf"
mkdir -p "$WORK"

echo "downloading $URL (resumable) ..."
curl -fL --retry 5 --retry-delay 5 -C - -A "${HTTP_USER_AGENT:-usc-safe-route-hackathon}" \
     -o "$WORK/region.osm.pbf" "$URL"

python -m scripts.study_area_geojson > "$WORK/study_area.geojson"
echo "clipping to study circle ..."
osmium extract -p "$WORK/study_area.geojson" -s complete_ways "$WORK/region.osm.pbf" \
       -o "$WORK/study_area.osm.pbf" --overwrite
echo "keeping highway=* ways ..."
osmium tags-filter "$WORK/study_area.osm.pbf" w/highway -o "$OUT" --overwrite
ls -lh "$OUT"
echo "done: $OUT  (next: python -m scripts.load_roads)"
