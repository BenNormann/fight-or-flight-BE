"""Print the study circle as a GeoJSON polygon (used by osmium extract)."""
import json

from app import create_app
from app.services.h3_utils import circle_ring

if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        c = app.config
        ring = [[lng, lat] for lat, lng in circle_ring(c["USC_LAT"], c["USC_LNG"], c["RADIUS_KM"])]
        ring.append(ring[0])
        print(json.dumps({"type": "Feature", "properties": {},
                          "geometry": {"type": "Polygon", "coordinates": [ring]}}))
