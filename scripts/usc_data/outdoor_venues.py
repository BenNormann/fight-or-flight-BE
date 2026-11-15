"""USC outdoor event venues (Trojan Event Services' outdoor diagrams) -> POIs.

    python -m scripts.usc_data venues

Reads the venue list and diagram links from trojanevents.usc.edu, and places each venue using
data/usc/usc_places.json (OpenStreetMap). Venues without a known location are reported and skipped.

Writes data/pois/usc_outdoor_venues.geojson (type "outdoor_venue"; Trousdale Parkway is a LineString).
"""
import argparse
import html
import re

from .common import POIS, feature, get, load_places, now_iso, point, session, write_geojson

URL = "https://trojanevents.usc.edu/venues/outdoor-venues/outdoor-diagrams/"
LINK_RX = re.compile(r'<a[^>]+href="([^"]+\.(?:pdf|jpe?g|png))"[^>]*>(.*?)</a>', re.S | re.I)


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower().replace("’", "'")).strip()


def scrape() -> tuple[list[dict], list[str]]:
    page = get(session(), URL).text
    diagrams: dict[str, list[str]] = {}
    for href, label in LINK_RX.findall(page):
        alt = re.search(r'alt="([^"]*)"', label)    # the link is usually an image whose alt names the venue
        label = alt.group(1) if alt else re.sub(r"<[^>]+>", "", label)
        label = re.sub(r"\s+", " ", html.unescape(label)).strip()
        if not label.lower().endswith("diagram"):
            continue
        venue = label[: -len("diagram")].strip()
        if venue.lower().startswith("trousdale"):
            venue = "Trousdale Parkway"
        diagrams.setdefault(re.sub(r"\s+", " ", venue), []).append(href)

    places = {norm(k): (k, v) for k, v in load_places()["venues"].items()}
    features, missing = [], []
    for venue, links in diagrams.items():
        hit = places.get(norm(venue))
        if not hit:
            missing.append(venue)
            continue
        key, p = hit
        geom = {"type": "LineString", "coordinates": p["line"]} if "line" in p else point(p["lng"], p["lat"])
        features.append(feature(geom, id="venue-" + norm(key).replace(" ", "-"), type="outdoor_venue",
                                name=venue, source="USC Trojan Event Services", diagram_urls=sorted(set(links)),
                                location_approx=p.get("approx"), osm=p.get("osm")))
    return features, missing


def main(argv=None):
    argparse.ArgumentParser(prog="usc_data venues").parse_args(argv)
    features, missing = scrape()
    write_geojson(f"{POIS}/usc_outdoor_venues.geojson", features, fetched_at=now_iso(), source_url=URL)
    print(f"usc_outdoor_venues.geojson: {len(features)} venues" + (f"; no location for {missing}" if missing else ""))
