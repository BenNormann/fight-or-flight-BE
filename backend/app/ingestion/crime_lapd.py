"""LAPD crime incidents from LA Open Data (Socrata SODA API).

Two datasets, two sources (they don't overlap in time, so no double counting):
  lapd        "Crime Data from 2020 to 2024"  (2nrs-mtv8, legacy, frozen)
  lapd_nibrs  "LAPD NIBRS Offenses Dataset"   (k7nn-b2ep, 2025 onward, updated ~weekly)

Published crime data lags by days, so this is mainly history/training data.
Each run re-pulls a 7-day overlap so late-arriving records are picked up; upserts dedupe.
"""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from ..extensions import db
from ..models import CrimeIncident
from ..services.h3_utils import in_study_area, study_bbox, to_cell
from .base import Ingestor

log = logging.getLogger(__name__)
LA_TZ = ZoneInfo("America/Los_Angeles")
PAGE = 50000
MAX_PAGES = 20


class SocrataCrimeIngestor(Ingestor):
    """Shared fetch/load for Socrata crime datasets. Subclasses define fields + parse()."""

    dataset_setting = ""
    backfill_setting = ""
    date_field = "date_occ"
    lat_field = "lat"
    lon_field = "lon"
    id_field = ""

    def __init__(self, since: datetime | None = None):
        self.since_override = since  # one-off backfills (scripts/backfill_crime.py)

    def _since(self) -> datetime:
        if self.since_override:
            return self.since_override.replace(tzinfo=LA_TZ) if self.since_override.tzinfo is None \
                else self.since_override
        latest = db.session.execute(
            text("SELECT max(ts) FROM crime_incidents WHERE source = :s"), {"s": self.name}
        ).scalar()
        if latest:
            return latest.astimezone(LA_TZ) - timedelta(days=7)
        return datetime.fromisoformat(current_app.config[self.backfill_setting]).replace(tzinfo=LA_TZ)

    def fetch(self):
        cfg = current_app.config
        min_lat, min_lng, max_lat, max_lng = study_bbox()
        since = self._since().strftime("%Y-%m-%dT%H:%M:%S")
        where = (f"{self.date_field} >= '{since}' "
                 f"AND {self.lat_field} BETWEEN {min_lat} AND {max_lat} "
                 f"AND {self.lon_field} BETWEEN {min_lng} AND {max_lng}")
        headers = {"X-App-Token": cfg["SOCRATA_APP_TOKEN"]} if cfg["SOCRATA_APP_TOKEN"] else {}
        url = f"https://data.lacity.org/resource/{cfg[self.dataset_setting]}.json"
        records = []
        for page in range(MAX_PAGES):
            batch = self.http_get(url, headers=headers, params={
                "$where": where, "$order": f"{self.date_field}, {self.id_field}",
                "$limit": PAGE, "$offset": page * PAGE,
            })
            records.extend(batch)
            if len(batch) < PAGE:
                break
        return records

    def parse(self, r) -> dict | None:
        """Return {external_id, local_dt, category, crime_code, severity, raw} or None."""
        raise NotImplementedError

    def normalize(self, raw):
        rows = []
        for r in raw:
            try:
                lat, lng = float(r.get(self.lat_field, 0)), float(r.get(self.lon_field, 0))
                if not lat or not lng or not in_study_area(lat, lng):
                    continue
                p = self.parse(r)
                if not p:
                    continue
                rows.append({
                    "source": self.name,
                    "external_id": p["external_id"],
                    "ts": p["local_dt"].replace(tzinfo=LA_TZ).astimezone(timezone.utc),
                    "geom": f"SRID=4326;POINT({lng} {lat})",
                    "h3_cell": to_cell(lat, lng),
                    "category": p["category"],
                    "crime_code": p["crime_code"],
                    "severity": p["severity"],
                    "raw": p["raw"],
                })
            except (KeyError, ValueError, TypeError) as exc:
                log.debug("skipping malformed record: %s", exc)
        return rows

    def load(self, rows):
        if not rows:
            return 0
        table = CrimeIncident.__table__
        for i in range(0, len(rows), 5000):
            stmt = insert(table).values(rows[i : i + 5000])
            stmt = stmt.on_conflict_do_update(
                index_elements=["source", "external_id", "ts"],
                set_={c: stmt.excluded[c] for c in ("geom", "h3_cell", "category", "crime_code", "severity", "raw")},
            )
            db.session.execute(stmt)
        db.session.commit()
        # Back-filled history falls outside the cagg policy window; refresh it explicitly.
        lo, hi = min(r["ts"] for r in rows), max(r["ts"] for r in rows) + timedelta(hours=1)
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("CALL refresh_continuous_aggregate('crime_hourly', :lo, :hi)"), {"lo": lo, "hi": hi})
        return len(rows)


def _keep(r, keys):
    return {k: r[k].strip() if isinstance(r[k], str) else r[k] for k in keys if k in r}


class LAPDCrimeIngestor(SocrataCrimeIngestor):
    name = "lapd"
    interval_setting = "POLL_LAPD_MINUTES"
    description = "LAPD crime 2020-2024 (legacy dataset, data.lacity.org)"
    dataset_setting = "LAPD_DATASET_ID"
    backfill_setting = "CRIME_BACKFILL_START"
    id_field = "dr_no"

    def parse(self, r):
        day = datetime.fromisoformat(r["date_occ"][:19])
        hhmm = str(r.get("time_occ", "0")).zfill(4)
        return {
            "external_id": str(r["dr_no"]),
            "local_dt": day.replace(hour=int(hhmm[:2]) % 24, minute=int(hhmm[2:]) % 60),
            "category": r.get("crm_cd_desc"),
            "crime_code": r.get("crm_cd"),
            "severity": int(r["part_1_2"]) if r.get("part_1_2") else None,
            "raw": _keep(r, ("area_name", "premis_desc", "weapon_desc", "status_desc", "vict_age", "vict_sex")),
        }


class LAPDNIBRSIngestor(SocrataCrimeIngestor):
    """Coordinates are rounded to the hundred-block (~100 m), still finer than a res-9 cell."""

    name = "lapd_nibrs"
    interval_setting = "POLL_LAPD_MINUTES"
    description = "LAPD NIBRS offenses 2025+ (data.lacity.org, ~weekly updates)"
    dataset_setting = "LAPD_NIBRS_DATASET_ID"
    backfill_setting = "NIBRS_BACKFILL_START"
    lat_field = "hndrdth_lat"
    lon_field = "hndrdth_lon"
    id_field = "uniquenibrno"

    def parse(self, r):
        desc = (r.get("nibr_description") or "").strip()
        code, _, label = desc.partition(" - ")
        return {
            "external_id": r["uniquenibrno"],
            "local_dt": datetime.fromisoformat(r["date_occ"][:19]),  # date_occ includes time
            "category": label.strip() or desc or None,
            "crime_code": code.strip() or None,
            "severity": None,
            "raw": _keep(r, ("caseno", "area_name", "premis_desc", "victim_shot", "domestic_violence_crime",
                             "gang_related_crime", "transit_related_crime", "totalvictimcount")),
        }
