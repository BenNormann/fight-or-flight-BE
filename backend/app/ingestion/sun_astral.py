"""Natural light: sun altitude per area cell, computed locally every minute."""
from datetime import datetime, timezone

import h3
from astral import Observer
from astral.sun import elevation

from ..services.h3_utils import area_cells
from .base import Ingestor, obs

CIVIL_TWILIGHT_DEG = -6.0


class SunIngestor(Ingestor):
    name = "sun"
    interval_setting = "POLL_SUN_MINUTES"
    description = "Sun altitude + darkness flag (astral, local compute)"

    def fetch(self):
        return datetime.now(timezone.utc).replace(second=0, microsecond=0), list(area_cells())

    def normalize(self, raw):
        ts, cells = raw
        rows = []
        for cell in cells:
            lat, lng = h3.cell_to_latlng(cell)
            alt = elevation(Observer(latitude=lat, longitude=lng), ts)
            rows.append(obs(ts, cell, self.name, "sun_altitude_deg", alt))
            rows.append(obs(ts, cell, self.name, "is_dark", 1.0 if alt < CIVIL_TWILIGHT_DEG else 0.0))
        return rows
