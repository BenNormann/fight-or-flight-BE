from geoalchemy2 import Geometry
from sqlalchemy.dialects.postgresql import JSONB

from ..extensions import db


class H3Cell(db.Model):
    """Every fine (res 9) cell in the study area plus its static features."""

    __tablename__ = "h3_cells"
    cell = db.Column(db.String(16), primary_key=True)
    res = db.Column(db.SmallInteger, nullable=False)
    parent_r6 = db.Column(db.String(16), nullable=False)
    geom = db.Column(Geometry("POLYGON", srid=4326, spatial_index=False), nullable=False)
    centroid = db.Column(Geometry("POINT", srid=4326, spatial_index=False), nullable=False)
    building_count = db.Column(db.Integer, nullable=False, server_default="0")
    avg_height_m = db.Column(db.Float)
    streetlight_count = db.Column(db.Integer, nullable=False, server_default="0")
    road_length_m = db.Column(db.Float, nullable=False, server_default="0")
    lit_road_length_m = db.Column(db.Float, nullable=False, server_default="0")
    intersection_count = db.Column(db.Integer, nullable=False, server_default="0")
    poi_counts = db.Column(JSONB, nullable=False, server_default="{}")
