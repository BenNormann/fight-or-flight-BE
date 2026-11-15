"""USC Learning Environments' Room Finder (sites.usc.edu/roomfinder) -> classroom inventory.

    python -m scripts.usc_data rooms

The site is WordPress; every classroom is a post titled "<BLDG> <room>" whose body is a table
(Classroom Type, Capacity, Furniture Type, Equipment, ...). We read them all through the
public WP REST API (/wp-json/wp/v2/posts).

Writes data/usc/roomfinder_rooms.json:
  {"rooms": [{"room_id": "THH 101", "building": "THH", "room": "101", "capacity": 94, ...}],
   "buildings": {"THH": {"room_count": 37, "total_seats": ..., "name": ..., "lat": ..., "lng": ...}}}

Room Finder only lists centrally-scheduled general-use classrooms (220 of them), not every room
in a building (offices, labs, department-owned rooms aren't there).
"""
import argparse
import html
import re
from collections import defaultdict

from .common import USC, building, get, now_iso, session, write_json

API = "https://sites.usc.edu/roomfinder/wp-json/wp/v2/posts"
ROW_RX = re.compile(r"<tr>\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*</tr>", re.S)


def text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse_room(post: dict) -> dict | None:
    title = text(post["title"]["rendered"]).upper()
    m = re.match(r"^([A-Z]{2,4})[\s-]+([A-Z]?\d+[A-Z]?|B\d+)\b", title)
    if not m:
        return None
    table = {text(k).rstrip(":"): text(v) for k, v in ROW_RX.findall(post["content"]["rendered"])}
    cap = re.search(r"\d+", table.get("Capacity", ""))
    return {
        "room_id": f"{m.group(1)} {m.group(2)}",
        "building": m.group(1),
        "room": m.group(2),
        "classroom_type": table.get("Classroom Type"),
        "capacity": int(cap.group()) if cap else None,
        "furniture": table.get("Furniture Type"),
        "equipment": [e.strip() for e in re.split(r"\n+", table.get("Equipment", "")) if e.strip()],
        "url": post["link"],
    }


def scrape() -> list[dict]:
    s = session()
    rooms, page = {}, 1
    while True:
        r = get(s, API, params={"per_page": 100, "page": page, "_fields": "title,content,link"})
        for post in r.json():
            room = parse_room(post)
            if room:
                rooms.setdefault(room["room_id"], room)   # a few rooms have duplicate posts
        if page >= int(r.headers.get("X-WP-TotalPages", 1)):
            break
        page += 1
    return sorted(rooms.values(), key=lambda x: (x["building"], x["room"]))


def summarize(rooms: list[dict]) -> dict:
    by_bldg = defaultdict(list)
    for r in rooms:
        by_bldg[r["building"]].append(r)
    out = {}
    for code, rs in sorted(by_bldg.items()):
        b = building(code) or {}
        out[code] = {"name": b.get("name"), "lat": b.get("lat"), "lng": b.get("lng"),
                     "room_count": len(rs), "total_seats": sum(r["capacity"] or 0 for r in rs),
                     "rooms": [r["room"] for r in rs]}
    return out


def main(argv=None):
    argparse.ArgumentParser(prog="usc_data rooms").parse_args(argv)
    rooms = scrape()
    buildings = summarize(rooms)
    write_json(f"{USC}/roomfinder_rooms.json", {"fetched_at": now_iso(), "source": API,
                                                 "buildings": buildings, "rooms": rooms})
    no_loc = [c for c, b in buildings.items() if b["lat"] is None]
    print(f"roomfinder_rooms.json: {len(rooms)} rooms in {len(buildings)} buildings"
          + (f"; no coordinates for {no_loc}" if no_loc else ""))
