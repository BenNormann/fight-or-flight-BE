from sqlalchemy.dialects.postgresql import JSONB

from ..extensions import db


class Observation(db.Model):
    """Narrow space-time table (TimescaleDB hypertable on ts).

    h3_cell may be a fine (res 9) cell or a coarse area cell (res 6) for
    signals that don't vary at block scale (sun position, weather).
    """

    __tablename__ = "observations"
    h3_cell = db.Column(db.String(16), primary_key=True)
    metric = db.Column(db.Text, primary_key=True)
    ts = db.Column(db.DateTime(timezone=True), primary_key=True)
    source = db.Column(db.Text, primary_key=True)
    value = db.Column(db.Float, nullable=False)
    meta = db.Column(JSONB)
