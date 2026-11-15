from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB

from ..extensions import db


class CrimeIncident(db.Model):
    """Point events (TimescaleDB hypertable on ts)."""

    __tablename__ = "crime_incidents"
    source = db.Column(db.Text, primary_key=True)
    external_id = db.Column(db.Text, primary_key=True)
    ts = db.Column(db.DateTime(timezone=True), primary_key=True)
    geom = db.Column(Geometry("POINT", srid=4326, spatial_index=False), nullable=False)
    h3_cell = db.Column(db.String(16), nullable=False)
    category = db.Column(db.Text)
    crime_code = db.Column(db.Text)
    severity = db.Column(db.SmallInteger)
    raw = db.Column(JSONB)
