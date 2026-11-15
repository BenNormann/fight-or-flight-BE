#!/usr/bin/env bash
# Maintainers: upload the large data files to the GitHub Release and refresh
# data/checksums.sha256 (commit that file afterwards). Requires `gh auth login`.
#
#   ./scripts/publish_data_release.sh            # tag data-v1
#   DATA_RELEASE_TAG=data-v2 ./scripts/publish_data_release.sh
set -euo pipefail

REPO="${DATA_RELEASE_REPO:-RithvikR1218/fight-or-flight-BE}"
TAG="${DATA_RELEASE_TAG:-data-v1}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FILES=("data/raw/LARIAC6_Buildings_2020.gdb.zip" "data/tiles/buildings.pmtiles")
for f in "${FILES[@]}"; do [[ -f "$f" ]] || { echo "missing $f" >&2; exit 1; }; done

if command -v sha256sum >/dev/null; then sha256sum "${FILES[@]}"; else shasum -a 256 "${FILES[@]}"; fi \
  > data/checksums.sha256
echo "wrote data/checksums.sha256"

if gh release view "$TAG" -R "$REPO" >/dev/null 2>&1; then
  gh release upload "$TAG" "${FILES[@]}" -R "$REPO" --clobber
else
  gh release create "$TAG" "${FILES[@]}" -R "$REPO" --title "Data $TAG" --notes \
"Large data files for the backend (too big for git). Fetch with \`./scripts/fetch_data.sh\`.

- \`LARIAC6_Buildings_2020.gdb.zip\`: LA County Countywide Building Outlines (LARIAC6, 2020), source for \`build_buildings.sh\`
- \`buildings.pmtiles\`: prebuilt 3D building tiles for the 10-mile USC circle (zoom 10-16, property \`height_m\`)"
fi
echo "done: https://github.com/$REPO/releases/tag/$TAG"
