from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import ARRAY

from ..extensions import db


class RoadNode(db.Model):
    __tablename__ = "road_nodes"
    osmid = db.Column(db.BigInteger, primary_key=True)
    geom = db.Column(Geometry("POINT", srid=4326, spatial_index=False), nullable=False)
    street_count = db.Column(db.SmallInteger)
    h3_cell = db.Column(db.String(16))


class RoadEdge(db.Model):
    __tablename__ = "road_edges"
    id = db.Column(db.BigInteger, primary_key=True)
    u = db.Column(db.BigInteger, nullable=False)
    v = db.Column(db.BigInteger, nullable=False)
    key = db.Column(db.SmallInteger, nullable=False, server_default="0")
    osmid = db.Column(db.Text)
    name = db.Column(db.Text)
    highway = db.Column(db.Text)
    lit = db.Column(db.Text)
    sidewalk = db.Column(db.Text)
    footway = db.Column(db.Text)
    length_m = db.Column(db.Float, nullable=False)
    geom = db.Column(Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=False)
    h3_cell = db.Column(db.String(16))
    h3_cells = db.Column(ARRAY(db.String(16)))
