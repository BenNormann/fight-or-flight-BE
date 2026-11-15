"""One-off crime backfill from an explicit start date (the hourly ingestor only
moves forward from the latest stored incident).

    docker compose exec api python -m scripts.backfill_crime --source lapd --start 2020-01-01

The risk model's history needs LAPD 2020-2024 (see scripts/export_crime_db.py).
"""
import argparse
from datetime import datetime

from app import create_app
from app.ingestion import REGISTRY
from app.ingestion.crime_lapd import MAX_PAGES, PAGE, SocrataCrimeIngestor


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="lapd", choices=["lapd", "lapd_nibrs"])
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (local LA date)")
    args = ap.parse_args()

    app = create_app()
    with app.app_context():
        cls = REGISTRY[args.source]
        assert issubclass(cls, SocrataCrimeIngestor)
        run = cls(since=datetime.fromisoformat(args.start)).run()
        print(f"{args.source} backfill from {args.start}: {run.status}, {run.rows} rows"
              + (f" ({run.error})" if run.error else ""))
        if run.rows and run.rows >= PAGE * MAX_PAGES:
            print("hit the page cap; re-run with a later --start to continue")


if __name__ == "__main__":
    main()
