"""Station observations from the National Weather Service (api.weather.gov).

Polls every station in NWS_STATIONS (KCQT = USC/Downtown, KLAX as fallback);
stations that are offline (404) are skipped. Each station's readings are stored
on the area (res 6) cell containing it.
"""
import logging
from datetime import datetime

import requests
from flask import current_app

from ..services.h3_utils import to_cell
from .base import Ingestor, obs

log = logging.getLogger(__name__)
FIELDS = {
    "temperature": "temp_c",
    "relativeHumidity": "humidity_pct",
    "visibility": "visibility_m",
    "windSpeed": "wind_speed_kmh",
    "precipitationLastHour": "precip_mm",
}


class NWSIngestor(Ingestor):
    name = "nws"
    interval_setting = "POLL_NWS_MINUTES"
    description = "Latest observations from NWS stations near USC"

    def fetch(self):
        stations = [s.strip() for s in current_app.config["NWS_STATIONS"].split(",") if s.strip()]
        results, errors = [], []
        for station in stations:
            try:
                results.append((station, self.http_get(
                    f"https://api.weather.gov/stations/{station}/observations/latest",
                    headers={"Accept": "application/geo+json"},
                )))
            except requests.RequestException as exc:
                log.warning("NWS station %s unavailable: %s", station, exc)
                errors.append(f"{station}: {exc}")
        if not results:
            raise RuntimeError("all NWS stations failed: " + "; ".join(errors))
        return results

    def normalize(self, raw):
        rows = []
        for station, payload in raw:
            props = payload.get("properties") or {}
            coords = (payload.get("geometry") or {}).get("coordinates")
            if not props.get("timestamp") or not coords:
                continue
            ts = datetime.fromisoformat(props["timestamp"])
            cell = to_cell(coords[1], coords[0], current_app.config["H3_AREA_RES"])
            for api_field, metric in FIELDS.items():
                val = (props.get(api_field) or {}).get("value")
                if val is not None:
                    rows.append(obs(ts, cell, self.name, metric, val, {"station": station}))
        return rows
