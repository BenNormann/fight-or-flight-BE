from flask import current_app, request
from flask_smorest import Blueprint, abort

from ..db_utils import fetch_all
from ..ingestion import REGISTRY, get_ingestor

blp = Blueprint("admin", __name__, url_prefix="/api/v1", description="Ingestion sources + manual triggers")


def _check_token():
    token = current_app.config["ADMIN_TOKEN"]
    if token and request.headers.get("X-Admin-Token") != token:
        abort(401, message="missing or invalid X-Admin-Token")


@blp.route("/sources")
@blp.response(200)
def sources():
    """Registered ingestion sources, their poll intervals and last run."""
    last = {r["source"]: r for r in fetch_all("""
        SELECT DISTINCT ON (source) source, status, started_at, finished_at, rows, error
        FROM ingest_runs ORDER BY source, started_at DESC
    """)}
    out = []
    for name, cls in REGISTRY.items():
        ing = cls()
        out.append({"name": name, "description": ing.description, "enabled": ing.enabled(),
                    "interval_minutes": ing.interval_minutes, "last_run": last.get(name)})
    return out


@blp.route("/admin/ingest/<string:source>", methods=["POST"])
@blp.response(200)
def trigger(source):
    """Run one ingestor synchronously (may take a while for crime backfills)."""
    _check_token()
    ing = get_ingestor(source)
    if not ing:
        abort(404, message=f"unknown source '{source}'; options: {sorted(REGISTRY)}")
    run = ing.run()
    return {"source": source, "status": run.status, "rows": run.rows, "error": run.error,
            "started_at": run.started_at.isoformat(), "finished_at": run.finished_at.isoformat()}
