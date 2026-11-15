from ..extensions import db


class IngestRun(db.Model):
    __tablename__ = "ingest_runs"
    id = db.Column(db.BigInteger, primary_key=True)
    source = db.Column(db.Text, nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False)
    finished_at = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.Text, nullable=False)  # running | ok | error | skipped
    rows = db.Column(db.Integer)
    error = db.Column(db.Text)
