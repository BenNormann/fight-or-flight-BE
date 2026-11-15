"""Convert a USC campus-map (Concept3D) category export into bluelight_phones.csv.

How the export was made: open https://maps.usc.edu/ in a browser, turn on the
"Emergency Phones" layer (category 55276), and in DevTools > Network copy the
response of the category request that lists `children.locations` into
data/categories-3.json. We don't fetch it automatically: the map API's
robots.txt disallows automated access.

Usage (from the repo root):
    python -m algorithm.data.bluelight_from_concept3d [path/to/export.json]
"""
import csv
import json
import sys
from pathlib import Path

from algorithm.common import config


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else config.ROOT / "data" / "categories-3.json"
    d = json.loads(src.read_text())
    locs = d.get("children", {}).get("locations") or d.get("locations") or []
    rows = [(round(float(x["lat"]), 6), round(float(x["lng"]), 6), f"{x.get('name', 'Emergency Phone')} #{x['id']}")
            for x in locs if x.get("lat") and x.get("lng")]
    if not rows:
        sys.exit(f"No locations with lat/lng found in {src}")
    with config.BLUELIGHT_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lat", "lon", "name"])
        w.writerows(rows)
    print(f"Wrote {len(rows)} blue-light phones to {config.BLUELIGHT_CSV} "
          f"(category: {d.get('name', '?')})")


if __name__ == "__main__":
    main()
