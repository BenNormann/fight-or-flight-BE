"""Artificial-light signal: open streetlight-outage requests from MyLA311 (Socrata).

MyLA311 publishes one dataset per year, so set LA311_DATASET_ID to the current
year's id. Disabled when empty. Writes one row per fine cell with >=1 open outage
(missing cell = 0 outages, which ml_features fills in).
"""
from collections import Counter
from datetime import datetime, timezone

from flask import current_app

from ..services.h3_utils import in_study_area, study_bbox, to_cell
from .base import Ingestor, obs


class LA311StreetlightIngestor(Ingestor):
    name = "la311"
    interval_setting = "POLL_LA311_MINUTES"
    description = "Open streetlight outage requests per cell (MyLA311)"

    def enabled(self):
        return bool(current_app.config["LA311_DATASET_ID"])

    def fetch(self):
        cfg = current_app.config
        min_lat, min_lng, max_lat, max_lng = study_bbox()
        headers = {"X-App-Token": cfg["SOCRATA_APP_TOKEN"]} if cfg["SOCRATA_APP_TOKEN"] else {}
        return self.http_get(
            f"https://data.lacity.org/resource/{cfg['LA311_DATASET_ID']}.json",
            headers=headers,
            params={
                "$select": "srnumber, latitude, longitude",
                "$where": (
                    "upper(requesttype) like '%STREETLIGHT%' AND status NOT IN ('Closed', 'Cancelled') "
                    f"AND latitude BETWEEN {min_lat} AND {max_lat} AND longitude BETWEEN {min_lng} AND {max_lng}"
                ),
                "$limit": 50000,
            },
        )

    def normalize(self, raw):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        counts = Counter()
        for r in raw:
            try:
                lat, lng = float(r["latitude"]), float(r["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if in_study_area(lat, lng):
                counts[to_cell(lat, lng)] += 1
        return [obs(now, cell, self.name, "streetlight_outages_open", n) for cell, n in counts.items()]
