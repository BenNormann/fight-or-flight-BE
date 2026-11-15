from geoalchemy2 import Geometry

from ..extensions import db


class Building(db.Model):
    __tablename__ = "buildings"
    id = db.Column(db.BigInteger, primary_key=True)
    source_id = db.Column(db.Text)
    geom = db.Column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False)
    height_m = db.Column(db.Float)
    elev_m = db.Column(db.Float)
    h3_cell = db.Column(db.String(16), index=False)


class Streetlight(db.Model):
    __tablename__ = "streetlights"
    id = db.Column(db.BigInteger, primary_key=True)
    source_id = db.Column(db.Text)
    geom = db.Column(Geometry("POINT", srid=4326, spatial_index=False), nullable=False)
    light_type = db.Column(db.Text)
    h3_cell = db.Column(db.String(16))
