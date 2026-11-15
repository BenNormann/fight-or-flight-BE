from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.opening_hours import is_open, parse_hours, parse_range

LA = ZoneInfo("America/Los_Angeles")
NNBSP, EN = " ", "–"  # what Google actually writes: "8 AM–10 PM"


def g(s):
    return s.replace(" AM", NNBSP + "AM").replace(" PM", NNBSP + "PM").replace("-", EN)


@pytest.mark.parametrize("text, expected", [
    (g("8 AM-10 PM"), [[480, 1320]]),
    (g("9:30 AM-5:15 PM"), [[570, 1035]]),
    (g("5-10 PM"), [[1020, 1320]]),          # opening meridiem implied by the closing one
    (g("12-9 PM"), [[720, 1260]]),           # 12 = noon
    (g("11-2 PM"), [[660, 840]]),            # 11 AM, not 11 PM
    (g("6 PM-2 AM"), [[1080, 1560]]),        # past midnight
    (g("6 AM-12 AM"), [[360, 1440]]),        # until midnight
    (g("12 AM-12 AM"), [[0, 1440]]),
    ("Open 24 hours", [[0, 1440]]),
    ("Closed", []),
    ("8 AM - 10 PM", [[480, 1320]]),         # plain ASCII also works
    ("Hours might differ", None),
])
def test_parse_range(text, expected):
    assert parse_range(text) == expected


def test_parse_hours_google_shape():
    raw = {
        "Monday": [g("11 AM-2:30 PM"), g("5-10 PM")],
        "Tuesday": ["Closed"],
        "Wednesday": ["Open 24 hours"],
        "Thursday": [g("8 AM-10 PM")],
        "Friday": [g("6 PM-2 AM")],
        "Saturday": [g("8 AM-10 PM")],
        "Sunday": [g("8 AM-10 PM")],
    }
    h = parse_hours(raw)
    assert list(h) == ["0", "1", "2", "3", "4", "5", "6"]
    assert h["0"] == [[660, 870], [1020, 1320]]
    assert h["1"] == []
    assert h["2"] == [[0, 1440]]
    assert h["4"] == [[1080, 1560]]


def test_parse_hours_unknown():
    assert parse_hours(None) is None
    assert parse_hours({}) is None
    assert parse_hours({"Monday": ["???"]}) is None
    assert parse_hours({"Monday": [g("8 AM-5 PM")]}) == {"0": [[480, 1020]]}


HOURS = {"0": [[480, 1320]], "1": [], "2": [[0, 1440]], "3": [[480, 1320]], "4": [[1080, 1560]],
         "5": [[600, 1200]], "6": [[600, 1200]]}


def la(y, mo, d, h, mi):
    return datetime(2026, mo, d, h, mi, tzinfo=LA) if y is None else datetime(y, mo, d, h, mi, tzinfo=LA)


def test_is_open_basic():
    # 2026-11-16 is a Monday
    assert is_open(HOURS, la(None, 11, 16, 12, 0)) is True
    assert is_open(HOURS, la(None, 11, 16, 7, 59)) is False
    assert is_open(HOURS, la(None, 11, 16, 22, 0)) is False   # close minute is exclusive
    assert is_open(HOURS, la(None, 11, 17, 12, 0)) is False   # Tuesday closed
    assert is_open(HOURS, la(None, 11, 18, 3, 0)) is True     # Wednesday 24h


def test_is_open_previous_day_past_midnight():
    # Friday 6 PM - 2 AM: open Saturday 1:30 AM, closed Saturday 2:00 AM
    assert is_open(HOURS, la(None, 11, 21, 1, 30)) is True
    assert is_open(HOURS, la(None, 11, 21, 2, 0)) is False
    assert is_open(HOURS, la(None, 11, 20, 23, 0)) is True


def test_is_open_converts_timezone():
    # 20:00 UTC on Monday 2026-11-16 = 12:00 PST
    assert is_open(HOURS, datetime(2026, 11, 16, 20, 0, tzinfo=timezone.utc)) is True
    # 05:00 UTC Tuesday = 21:00 PST Monday
    assert is_open(HOURS, datetime(2026, 11, 17, 5, 0, tzinfo=timezone.utc)) is True


def test_is_open_unknown():
    assert is_open(None, la(None, 11, 16, 12, 0)) is None
    assert is_open({"3": [[0, 600]]}, la(None, 11, 16, 12, 0)) is None  # no data for Monday
