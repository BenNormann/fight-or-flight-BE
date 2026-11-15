#!/usr/bin/env bash
# Local building file -> clip to study circle -> PostGIS -> PMTiles.
# Run inside the tools container (has tippecanoe):
#   docker compose run --rm tools bash scripts/build_buildings.sh [path-to-file]
set -euo pipefail
DATA_DIR="${DATA_DIR:-/data}"

python -m scripts.load_buildings "$@"

mkdir -p "$DATA_DIR/tiles"
tippecanoe \
  -o "$DATA_DIR/tiles/buildings.pmtiles" \
  -l buildings \
  -Z 10 -z 16 \
  --drop-densest-as-needed \
  --extend-zooms-if-still-dropping \
  --force \
  "$DATA_DIR/work/buildings.geojsonl"

echo "done: $DATA_DIR/tiles/buildings.pmtiles"
