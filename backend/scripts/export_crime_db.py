"""Build the risk model's crime history (algorithm/data/crime.db) from the backend DB.

The model (algorithm/, see algorithm/README.md) computes its history features from
a SQLite `incidents` table of LAPD 2020-2024 incidents. Instead of a second LAPD
download (algorithm/data/ingest_crime.py), this exports the same records from
crime_incidents (source 'lapd', dataset 2nrs-mtv8), so the backend is the single
crime source.

    docker compose exec api python -m scripts.backfill_crime --source lapd --start 2020-01-01   # once
    docker compose exec api python -m scripts.export_crime_db
"""
import datetime as dt
import sqlite3
import time
from zoneinfo import ZoneInfo

from sqlalchemy import text

from algorithm.common import config as mcfg
from algorithm.common import geo
from algorithm.common.severity import severity_tier
from app import create_app
from app.extensions import db

LA = ZoneInfo(mcfg.TIMEZONE)
SQL = text("""
    SELECT external_id, ts, ST_Y(geom) AS lat, ST_X(geom) AS lon, crime_code, category, severity, h3_cell,
           raw->>'weapon_used_cd' AS weapon
    FROM crime_incidents
    WHERE source = 'lapd' AND ts >= :start AND ts < :end
    ORDER BY ts
""")


def main():
    app = create_app()
    with app.app_context():
        t = time.time()
        start = dt.datetime.fromisoformat(mcfg.DATA_START).replace(tzinfo=LA)
        end = (dt.datetime.fromisoformat(mcfg.DATA_END) + dt.timedelta(days=1)).replace(tzinfo=LA)
        rows = db.session.execute(SQL, {"start": start, "end": end}).fetchall()

        recs = []
        missing_weapon = 0
        for ext_id, ts, lat, lon, code, desc, sev, cell, weapon in rows:
            if not geo.in_radius(lat, lon):  # model's own circle definition
                continue
            local = ts.astimezone(LA)
            if weapon is None:
                missing_weapon += 1
            recs.append((ext_id, local.date().isoformat(), local.hour * 100 + local.minute, local.hour,
                         lat, lon, code, desc, weapon, str(sev) if sev is not None else None,
                         cell, severity_tier(desc, code, weapon)))

        path = mcfg.DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".db.tmp")
        tmp.unlink(missing_ok=True)
        con = sqlite3.connect(tmp)
        con.executescript(_schema())
        con.executemany("INSERT OR REPLACE INTO incidents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", recs)
        con.executemany("INSERT OR REPLACE INTO ingest_meta VALUES (?, ?)", [
            ("source", "backend postgres crime_incidents (lapd, 2nrs-mtv8)"),
            ("fetched_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
        ])
        con.commit()
        lo, hi = con.execute("SELECT MIN(date_occ), MAX(date_occ) FROM incidents").fetchone()
        con.close()
        tmp.replace(path)
        print(f"wrote {len(recs):,} incidents ({lo} .. {hi}) to {path} in {time.time() - t:.0f}s")
        years = {}
        for r in recs:
            years[r[1][:4]] = years.get(r[1][:4], 0) + 1
        print("  per year:", dict(sorted(years.items())))
        if missing_weapon > 0.9 * max(len(recs), 1):
            print("  WARNING: weapon codes missing (rows ingested before weapon_used_cd was stored); armed crimes "
                  "will be under-weighted. Re-run scripts.backfill_crime --source lapd --start 2020-01-01, then export again.")
        if not years.get("2023"):
            print("  WARNING: no 2023 rows; the model's live history is anchored at 2023. "
                  "Run scripts.backfill_crime --source lapd --start 2020-01-01 first.")


def _schema():
    from algorithm.data.ingest_crime import SCHEMA
    return SCHEMA


if __name__ == "__main__":
    main()
