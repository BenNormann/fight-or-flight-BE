"""Small helpers for raw-SQL endpoints (most queries here are analytical)."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

from .extensions import db


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def fetch_all(sql, **params):
    result = db.session.execute(text(sql), params)
    return [{k: _jsonable(v) for k, v in row._mapping.items()} for row in result]


def fetch_df(sql, **params):
    import pandas as pd

    result = db.session.execute(text(sql), params)
    return pd.DataFrame(result.fetchall(), columns=list(result.keys()))
