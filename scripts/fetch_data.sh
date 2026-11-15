#!/usr/bin/env bash
# Download the large data files that aren't in git (GitHub limits files to 100 MB)
# from this repo's GitHub Release into data/, and verify their checksums.
#
#   ./scripts/fetch_data.sh             # buildings source + prebuilt 3D tiles
#   ./scripts/fetch_data.sh --no-tiles  # skip tiles (rebuild with build_buildings.sh)
set -euo pipefail

REPO="${DATA_RELEASE_REPO:-RithvikR1218/fight-or-flight-BE}"
TAG="${DATA_RELEASE_TAG:-data-v2}"   # data-v2: 5-mile tiles; data-v1: 10-mile
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FILES=("data/raw/LARIAC6_Buildings_2020.gdb.zip")
[[ "${1:-}" == "--no-tiles" ]] || FILES+=("data/tiles/buildings.pmtiles")

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
expected() { grep "  $1\$" data/checksums.sha256 | cut -d' ' -f1; }

for path in "${FILES[@]}"; do
  want="$(expected "$path")"
  if [[ -f "$path" && "$(sha256 "$path")" == "$want" ]]; then
    echo "ok       $path (already present)"
    continue
  fi
  url="https://github.com/$REPO/releases/download/$TAG/$(basename "$path")"
  echo "fetching $path"
  mkdir -p "$(dirname "$path")"
  curl -fL --retry 5 --retry-delay 3 -C - -o "$path.part" "$url"
  got="$(sha256 "$path.part")"
  if [[ -n "$want" && "$got" != "$want" ]]; then
    echo "checksum mismatch for $path (got $got, want $want); delete $path.part and retry" >&2
    exit 1
  fi
  mv "$path.part" "$path"
  echo "ok       $path"
done
