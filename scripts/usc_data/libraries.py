"""USC Libraries bookable study rooms (LibCal) with availability.

    python -m scripts.usc_data libraries [--days 7]

For each library page (libcal.usc.edu/spaces?lid=...) this reads the room list embedded in the
page (`resources.push({...})`: name, capacity, group) and then asks the same endpoint the page's
calendar uses (POST /spaces/availability/grid) for every bookable slot in the next N days.
A slot with a `className` is booked/unavailable; one without is free.

Writes:
  data/pois/usc_library_rooms.geojson   one Point per room (type "study"), at its library building,
                                        with `free_slots` per date for the scraped window
  data/usc/library_availability.json    the same availability, keyed by room, plus fetch time

LibCal availability changes minute to minute; re-run this (e.g. every 15 min from cron) to refresh.
"""
import argparse
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from .common import POIS, USC, building, feature, get, now_iso, point, session, write_geojson, write_json

BASE = "https://libcal.usc.edu"

# LibCal location id -> library name and building code (coordinates come from usc_places.json).
LIBRARIES = {
    2893: ("Doheny Memorial Library", "DOH"),
    2895: ("Leavey Library", "LVL"),
    13141: ("Science & Engineering Library", "SSL"),
}

RESOURCE_RX = re.compile(r"resources\.push\(\{(.*?)\}\);", re.S)
FIELD_RX = re.compile(r'^\s*(\w+):\s*(".*?"|-?\d+|true|false|null)\s*,?\s*$', re.M)


def parse_rooms(page: str) -> list[dict]:
    rooms = []
    for block in RESOURCE_RX.findall(page):
        r = {k: json.loads(v) for k, v in FIELD_RX.findall(block)}
        if "eid" in r:
            rooms.append(r)
    return rooms


def fetch_grid(s, lid: int, start: date, end: date) -> list[dict]:
    r = s.post(f"{BASE}/spaces/availability/grid", timeout=60,
               headers={"Referer": f"{BASE}/spaces?lid={lid}", "X-Requested-With": "XMLHttpRequest"},
               data={"lid": lid, "gid": 0, "eid": -1, "seat": 0, "seatId": 0, "zone": 0,
                     "start": start.isoformat(), "end": end.isoformat(), "pageIndex": 0, "pageSize": 500})
    r.raise_for_status()
    return r.json().get("slots") or []


def merge(slots: list[tuple[str, str]]) -> list[list[str]]:
    """Merge back-to-back free slots into [start, end] HH:MM ranges."""
    out = []
    for a, b in sorted(slots):
        if out and out[-1][1] == a:
            out[-1][1] = b
        else:
            out.append([a, b])
    return out


def scrape(days: int) -> tuple[list[dict], dict]:
    s = session()
    start = date.today()
    end = start + timedelta(days=days)
    features, availability = [], {}
    for lid, (library, code) in LIBRARIES.items():
        page = get(s, f"{BASE}/spaces?lid={lid}").text
        rooms = {r["eid"]: r for r in parse_rooms(page)}
        free = defaultdict(lambda: defaultdict(list))
        booked = defaultdict(int)
        total = defaultdict(int)
        for slot in fetch_grid(s, lid, start, end):
            eid = slot.get("itemId")
            if eid not in rooms:
                continue    # seats / items that aren't listed as rooms on the page
            t0 = datetime.fromisoformat(slot["start"])
            t1 = datetime.fromisoformat(slot["end"])
            total[eid] += 1
            if slot.get("className"):
                booked[eid] += 1
            else:
                free[eid][t0.date().isoformat()].append((t0.strftime("%H:%M"), t1.strftime("%H:%M") if t1.date() == t0.date() else "24:00"))
        b = building(code)
        if not b:
            print(f"  warning: no coordinates for {code} ({library}); run `usc_data places`")
        for eid, r in rooms.items():
            name = re.sub(r"\s*\(Capacity \d+\)\s*$", "", r.get("title", "")).strip()
            free_slots = {d: merge(v) for d, v in sorted(free[eid].items())}
            availability[str(eid)] = {"name": name, "library": library, "free_slots": free_slots,
                                      "slots_total": total[eid], "slots_booked": booked[eid]}
            if b:
                features.append(feature(
                    point(b["lng"], b["lat"]), id=f"libcal-{eid}", type="study", name=f"{library}: {name}",
                    source="USC Libraries LibCal", library=library, building_code=code, room=name,
                    group=r.get("grouping"), capacity=r.get("capacity"),
                    booking_url=f"{BASE}{r['url']}" if r.get("url") else f"{BASE}/space/{eid}",
                    free_slots=free_slots, availability_window=[start.isoformat(), end.isoformat()]))
        print(f"  {library}: {len(rooms)} rooms, {sum(booked[e] for e in rooms)}/{sum(total[e] for e in rooms)} slots booked")
    return features, availability


def main(argv=None):
    ap = argparse.ArgumentParser(prog="usc_data libraries")
    ap.add_argument("--days", type=int, default=7, help="days of availability to fetch (default 7)")
    args = ap.parse_args(argv)
    features, availability = scrape(args.days)
    fetched = now_iso()
    write_geojson(f"{POIS}/usc_library_rooms.geojson", features, fetched_at=fetched)
    write_json(f"{USC}/library_availability.json", {"fetched_at": fetched, "rooms": availability})
    print(f"usc_library_rooms.geojson: {len(features)} rooms")
