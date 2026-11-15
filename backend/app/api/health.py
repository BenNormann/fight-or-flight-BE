from flask_smorest import Blueprint
from sqlalchemy import text

from ..db_utils import fetch_all
from ..extensions import db

blp = Blueprint("health", __name__, url_prefix="/api/v1", description="Liveness + ingestion status")


@blp.route("/health")
@blp.response(200)
def health():
    """DB status and the latest run of each ingestion source."""
    try:
        db.session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        return {"status": "degraded", "db": f"error: {exc}"}, 503
    last_runs = fetch_all("""
        SELECT DISTINCT ON (source) source, status, started_at, finished_at, rows, error
        FROM ingest_runs ORDER BY source, started_at DESC
    """)
    return {"status": "ok", "db": "ok" if db_ok else "error", "ingestors": last_runs}
