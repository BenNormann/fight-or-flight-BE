import json
from pathlib import Path

from app.ingestion import REGISTRY
from app.ingestion.crime_lapd import LAPDCrimeIngestor, LAPDNIBRSIngestor
from app.ingestion.sun_astral import SunIngestor
from app.ingestion.weather_nws import NWSIngestor
from app.ingestion.weather_openmeteo import OpenMeteoIngestor
from app.services.h3_utils import area_cells

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_registry():
    assert set(REGISTRY) == {"sun", "openmeteo", "nws", "lapd", "lapd_nibrs", "la311"}


def test_sun(app):
    rows = SunIngestor().normalize(SunIngestor().fetch())
    assert len(rows) == 2 * len(area_cells())
    alts = [r["value"] for r in rows if r["metric"] == "sun_altitude_deg"]
    assert all(-90 <= a <= 90 for a in alts)


def test_openmeteo(app):
    cells = list(area_cells())[:2]
    rows = OpenMeteoIngestor().normalize((cells, load("openmeteo.json")))
    assert {r["h3_cell"] for r in rows} == set(cells)
    temp = next(r for r in rows if r["metric"] == "temp_c" and r["h3_cell"] == cells[0])
    assert temp["value"] == 19.4 and temp["ts"].tzinfo is not None


def test_nws(app):
    rows = NWSIngestor().normalize([("KCQT", load("nws.json"))])
    metrics = {r["metric"]: r["value"] for r in rows}
    assert metrics["temp_c"] == 18.3 and "precip_mm" not in metrics  # null skipped


def test_lapd(app):
    rows = LAPDCrimeIngestor().normalize(load("lapd.json"))
    assert len(rows) == 1  # one zero-coord record, one outside the circle
    r = rows[0]
    assert r["external_id"] == "240104567" and r["severity"] == 1
    assert r["ts"].isoformat() == "2024-03-02T07:30:00+00:00"  # 23:30 PST -> UTC


def test_lapd_nibrs(app):
    rows = LAPDNIBRSIngestor().normalize(load("lapd_nibrs.json"))
    assert len(rows) == 1
    r = rows[0]
    assert r["external_id"] == "251204411__0"
    assert r["category"] == "THEFT FROM MOTOR VEHICLE - PETTY ($950 & UNDER)" and r["crime_code"] == "420"
    assert r["ts"].isoformat() == "2025-07-04T04:15:00+00:00"  # 21:15 PDT -> UTC
