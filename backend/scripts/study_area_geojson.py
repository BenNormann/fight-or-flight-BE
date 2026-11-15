"""Print the study circle as a GeoJSON polygon (used by osmium extract).

    python -m scripts.study_area_geojson [--margin-km 1.5]
"""
import argparse
import json

from app import create_app
from app.services.h3_utils import circle_ring

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--margin-km", type=float, default=0.0)
    args = ap.parse_args()
    app = create_app()
    with app.app_context():
        c = app.config
        ring = [[lng, lat] for lat, lng in circle_ring(c["USC_LAT"], c["USC_LNG"], c["RADIUS_KM"] + args.margin_km)]
        ring.append(ring[0])
        print(json.dumps({"type": "Feature", "properties": {},
                          "geometry": {"type": "Polygon", "coordinates": [ring]}}))
