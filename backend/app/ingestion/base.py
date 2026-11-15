"""Ingestor contract: fetch() -> normalize() -> load(). Loads are idempotent upserts,
so polling faster than a source refreshes never creates duplicate rows."""
import logging
from datetime import datetime, timezone

import requests
from flask import current_app
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy.dialects.postgresql import insert

from ..extensions import db
from ..models import IngestRun, Observation

log = logging.getLogger(__name__)

# Shared session: retries flaky upstreams (timeouts, 429, 5xx) with backoff 1s, 2s, 4s.
_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, connect=3, read=3, backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",),
)))


class Ingestor:
    name: str = ""
    interval_setting: str = ""  # config key holding the poll interval in minutes
    description: str = ""

    # ---- override these ----
    def enabled(self) -> bool:
        return True

    def fetch(self):
        raise NotImplementedError

    def normalize(self, raw) -> list[dict]:
        """Return observation rows: {ts, h3_cell, source, metric, value, meta?}."""
        raise NotImplementedError

    def load(self, rows: list[dict]) -> int:
        return upsert_observations(rows)

    # ---- shared ----
    @property
    def interval_minutes(self) -> float:
        return float(current_app.config[self.interval_setting])

    def http_get(self, url, params=None, headers=None):
        h = {"User-Agent": current_app.config["HTTP_USER_AGENT"]}
        h.update(headers or {})
        timeout = (5, current_app.config["HTTP_TIMEOUT"])  # (connect, read)
        resp = _session.get(url, params=params, headers=h, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def run(self) -> IngestRun:
        run = IngestRun(source=self.name, started_at=_now(), status="running")
        db.session.add(run)
        db.session.commit()
        try:
            if not self.enabled():
                run.status, run.rows = "skipped", 0
            else:
                rows = self.normalize(self.fetch())
                run.rows = self.load(rows)
                run.status = "ok"
        except Exception as exc:  # record and keep the scheduler alive
            db.session.rollback()
            log.exception("ingestor %s failed", self.name)
            run.status, run.error = "error", f"{type(exc).__name__}: {exc}"[:2000]
        run.finished_at = _now()
        db.session.add(run)
        db.session.commit()
        log.info("ingestor %s: %s rows=%s", self.name, run.status, run.rows)
        return run


def _now():
    return datetime.now(timezone.utc)


def upsert_observations(rows: list[dict], chunk=5000) -> int:
    if not rows:
        return 0
    table = Observation.__table__
    for i in range(0, len(rows), chunk):
        stmt = insert(table).values(rows[i : i + chunk])
        stmt = stmt.on_conflict_do_update(
            index_elements=["h3_cell", "metric", "ts", "source"],
            set_={"value": stmt.excluded.value, "meta": stmt.excluded.meta},
        )
        db.session.execute(stmt)
    db.session.commit()
    return len(rows)


def obs(ts, cell, source, metric, value, meta=None):
    return {"ts": ts, "h3_cell": cell, "source": source, "metric": metric, "value": float(value), "meta": meta}
