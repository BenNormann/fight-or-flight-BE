"""Ingestion process: runs every registered source on its own interval.

Runs as its own container so gunicorn's workers don't each start a scheduler.
"""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app import create_app
from app.ingestion import REGISTRY

log = logging.getLogger("ingestor")


def main():
    app = create_app()
    scheduler = BlockingScheduler(timezone="UTC")

    def make_job(name):
        def job():
            with app.app_context():
                REGISTRY[name]().run()
        return job

    with app.app_context():
        for name, cls in REGISTRY.items():
            minutes = cls().interval_minutes
            scheduler.add_job(
                make_job(name), IntervalTrigger(minutes=minutes), id=name,
                max_instances=1, coalesce=True, misfire_grace_time=60,
                next_run_time=datetime.now(timezone.utc),  # run once immediately
            )
            log.info("scheduled %s every %s min", name, minutes)
    scheduler.start()


if __name__ == "__main__":
    main()
