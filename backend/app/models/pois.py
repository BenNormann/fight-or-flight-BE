from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB

from ..extensions import db


class Poi(db.Model):
    __tablename__ = "pois"
    id = db.Column(db.Text, primary_key=True)
    type = db.Column(db.Text, nullable=False)
    name = db.Column(db.Text)
    source = db.Column(db.Text)
    source_file = db.Column(db.Text, nullable=False)
    props = db.Column(JSONB, nullable=False, server_default="{}")
    geom = db.Column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    h3_cell = db.Column(db.String(16))
