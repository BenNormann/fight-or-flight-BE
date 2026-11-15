#!/usr/bin/env bash
# Download the Geofabrik SoCal OSM extract once, clip it to the study circle and keep
# only highway=* ways. Result: data/raw/usc_roads.osm.pbf (small, kept in the repo).
# The clip is the study circle plus ROAD_MARGIN_KM so routes near the edge have streets.
#   docker compose run --rm tools bash scripts/fetch_osm.sh
set -euo pipefail
DATA_DIR="${DATA_DIR:-/data}"
URL="${OSM_EXTRACT_URL:-https://download.geofabrik.de/north-america/us/california/socal-latest.osm.pbf}"
WORK="$DATA_DIR/work"
OUT="$DATA_DIR/raw/usc_roads.osm.pbf"
mkdir -p "$WORK"

# "latest" points to a new file every day, so never resume a partial download from it:
# reuse a complete download younger than 7 days, otherwise fetch fresh into .part and rename.
if [[ -f "$WORK/region.osm.pbf" && -n "$(find "$WORK/region.osm.pbf" -mtime -7)" ]]; then
  echo "reusing $WORK/region.osm.pbf (less than 7 days old; delete it to force a fresh download)"
else
  echo "downloading $URL ..."
  rm -f "$WORK/region.osm.pbf.part"
  curl -fL --retry 5 --retry-delay 5 -A "${HTTP_USER_AGENT:-usc-safe-route-hackathon}" \
       -o "$WORK/region.osm.pbf.part" "$URL"
  mv "$WORK/region.osm.pbf.part" "$WORK/region.osm.pbf"
fi

python -m scripts.study_area_geojson --margin-km "${ROAD_MARGIN_KM:-1.5}" > "$WORK/study_area.geojson"
echo "clipping to study circle ..."
osmium extract -p "$WORK/study_area.geojson" -s complete_ways "$WORK/region.osm.pbf" \
       -o "$WORK/study_area.osm.pbf" --overwrite
echo "keeping highway=* ways ..."
osmium tags-filter "$WORK/study_area.osm.pbf" w/highway -o "$OUT" --overwrite
ls -lh "$OUT"
echo "done: $OUT  (next: python -m scripts.load_roads)"
