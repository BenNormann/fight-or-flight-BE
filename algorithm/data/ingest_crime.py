"""Pull LAPD crime incidents for the USC area into SQLite (data/crime.db).

Usage (from the repo root):
    python -m algorithm.data.ingest_crime

Pulls every incident inside config.RADIUS_MILES of USC for 2020-2024
(~400k rows for 5 miles, ~1 min). Each run is a full refresh.

Source: LA City Open Data, "Crime Data from 2020 to Present"
(Socrata dataset 2nrs-mtv8, https://data.lacity.org/resource/2nrs-mtv8.json).
Set SOCRATA_APP_TOKEN in your env for higher rate limits (optional).
"""
import datetime as dt
import os
import sqlite3
import sys
import time

import h3
import requests

from algorithm.common import config, geo
from algorithm.common.severity import severity_tier

URL = "https://data.lacity.org/resource/2nrs-mtv8.json"
PAGE = 50_000
FIELDS = "dr_no,date_occ,time_occ,lat,lon,crm_cd,crm_cd_desc,weapon_used_cd,part_1_2"

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    dr_no          TEXT PRIMARY KEY,
    date_occ       TEXT NOT NULL,     -- YYYY-MM-DD (local)
    time_occ       INTEGER NOT NULL,  -- HHMM as LAPD reports it
    hour           INTEGER NOT NULL,  -- 0-23
    lat            REAL NOT NULL,
    lon            REAL NOT NULL,
    crm_cd         TEXT,
    crm_cd_desc    TEXT,
    weapon_used_cd TEXT,
    part_1_2       TEXT,
    cell           TEXT NOT NULL,     -- H3 res 9
    tier           TEXT NOT NULL      -- violent / property / other (charts only; the model uses common/severity.py scores)
);
CREATE INDEX IF NOT EXISTS idx_inc_cell_date ON incidents(cell, date_occ);
CREATE INDEX IF NOT EXISTS idx_inc_date ON incidents(date_occ);
CREATE TABLE IF NOT EXISTS ingest_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def fetch_pages():
    s, w, n, e = config.BBOX
    where = (
        f"date_occ between '{config.DATA_START}T00:00:00' and '{config.DATA_END}T23:59:59' "
        f"AND lat between {s} and {n} AND lon between {w} and {e}"
    )
    headers = {}
    if os.getenv("SOCRATA_APP_TOKEN"):
        headers["X-App-Token"] = os.environ["SOCRATA_APP_TOKEN"]
    offset = 0
    while True:
        params = {"$select": FIELDS, "$where": where, "$order": "dr_no",
                  "$limit": PAGE, "$offset": offset}
        for attempt in range(4):
            try:
                r = requests.get(URL, params=params, headers=headers, timeout=300)
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 3:
                    raise
                print(f"  retrying page at offset {offset}: {e}", file=sys.stderr)
                time.sleep(5 * (attempt + 1))
        rows = r.json()
        if not rows:
            return
        yield rows
        offset += len(rows)
        print(f"  fetched {offset} rows...", file=sys.stderr)


def to_record(row):
    try:
        lat, lon = float(row["lat"]), float(row["lon"])
    except (KeyError, ValueError, TypeError):
        return None
    if lat == 0 or lon == 0:  # LAPD uses 0,0 for redacted locations
        return None
    if not geo.in_radius(lat, lon):  # API query is the bounding box; keep the circle
        return None
    date = dt.date.fromisoformat(row["date_occ"][:10])
    t = int(row.get("time_occ") or 0)
    hour = (t // 100) % 24
    return (
        str(row["dr_no"]), date.isoformat(), t, hour, lat, lon,
        row.get("crm_cd"), row.get("crm_cd_desc"), row.get("weapon_used_cd"),
        row.get("part_1_2"),
        h3.latlng_to_cell(lat, lon, config.H3_RES),
        severity_tier(row.get("crm_cd_desc"), row.get("crm_cd"), row.get("weapon_used_cd")),
    )


def main():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM incidents")  # full refresh: the area may have changed
    total = 0
    for page in fetch_pages():
        recs = [r for r in map(to_record, page) if r]
        conn.executemany(
            "INSERT OR REPLACE INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", recs)
        total += len(recs)
    conn.execute("INSERT OR REPLACE INTO ingest_meta VALUES ('source', ?)", (URL,))
    conn.execute("INSERT OR REPLACE INTO ingest_meta VALUES ('fetched_at', ?)",
                 (dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),))
    conn.execute("INSERT OR REPLACE INTO ingest_meta VALUES ('area', ?)",
                 (f"{config.RADIUS_MILES} miles around {config.CENTER}",))
    conn.commit()
    n, lo, hi = conn.execute(
        "SELECT COUNT(*), MIN(date_occ), MAX(date_occ) FROM incidents").fetchone()
    conn.close()
    print(f"Stored {total} rows this run; {n} incidents in {config.DB_PATH} ({lo} .. {hi})")


if __name__ == "__main__":
    main()
