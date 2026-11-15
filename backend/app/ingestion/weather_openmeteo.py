"""Weather from Open-Meteo (free, no key). One batched request for every area cell."""
from datetime import datetime, timezone

import h3

from ..services.h3_utils import area_cells
from .base import Ingestor, obs

URL = "https://api.open-meteo.com/v1/forecast"
FIELDS = {
    "temperature_2m": "temp_c",
    "relative_humidity_2m": "humidity_pct",
    "precipitation": "precip_mm",
    "cloud_cover": "cloud_cover_pct",
    "wind_speed_10m": "wind_speed_kmh",
}


class OpenMeteoIngestor(Ingestor):
    name = "openmeteo"
    interval_setting = "POLL_OPENMETEO_MINUTES"
    description = "Current weather per area cell (Open-Meteo, ~15 min model refresh)"

    def fetch(self):
        cells = list(area_cells())
        centers = [h3.cell_to_latlng(c) for c in cells]
        data = self.http_get(URL, params={
            "latitude": ",".join(f"{lat:.5f}" for lat, _ in centers),
            "longitude": ",".join(f"{lng:.5f}" for _, lng in centers),
            "current": ",".join(FIELDS),
            "timezone": "GMT",
        })
        return cells, data

    def normalize(self, raw):
        cells, data = raw
        locations = data if isinstance(data, list) else [data]
        rows = []
        for cell, loc in zip(cells, locations):
            cur = loc.get("current") or {}
            if "time" not in cur:
                continue
            ts = datetime.fromisoformat(cur["time"]).replace(tzinfo=timezone.utc)
            for api_field, metric in FIELDS.items():
                if cur.get(api_field) is not None:
                    rows.append(obs(ts, cell, self.name, metric, cur[api_field]))
        return rows
