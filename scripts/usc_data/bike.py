"""USC Bicycle Coalition's campus bike map (Google My Maps) -> bike POIs.

    python -m scripts.usc_data bike

Downloads the map as KML (google.com/maps/d/kml?mid=...) and maps each layer to a POI type.
The map says it is "up to date as of October 12, 2015", so racks may have moved since; the
`OUTDATED` and `Removed` layers are kept/dropped as noted below.

Writes data/pois/usc_bike_resources.geojson.
"""
import argparse
import re
import xml.etree.ElementTree as ET

from .common import POIS, feature, get, now_iso, point, session, write_geojson

MID = "1QhqwIWcEQfKin3I5hLaO_kaaVI4"
KML_URL = f"https://www.google.com/maps/d/kml?mid={MID}&forcekml=1"
VIEWER_URL = f"https://www.google.com/maps/d/viewer?mid={MID}"
NS = {"k": "http://www.opengis.net/kml/2.2"}

# KML layer (folder) name prefix -> (POI type, extra props). None = skip the layer.
LAYERS = [
    ("Bike Racks: New", ("bike_parking", {"rack_status": "current"})),
    ("Bike Racks: OUTDATED", ("bike_parking", {"rack_status": "outdated"})),
    ("Bike Racks: Removed", None),
    ("Bike Standing", ("bike_parking", {"rack_status": "informal"})),
    ("Secure Bike Lockers", ("bike_parking", {"rack_status": "locker"})),
    ("Bike Pumps", ("bike_pump", {})),
    ("Bike Shops", ("bike_service", {})),
    ("Bike Lanes", ("bike_lane", {})),
    ("Bike Friendly Public Transit", ("bike_transit", {})),
    ("Zones", ("bike_zone", {})),
]


def layer_type(folder: str):
    for prefix, spec in LAYERS:
        if folder.startswith(prefix):
            return spec
    return ("bike_other", {})


def coords(text: str) -> list[list[float]]:
    out = []
    for tok in text.split():
        lng, lat, *_ = (float(x) for x in tok.split(","))
        out.append([round(lng, 7), round(lat, 7)])
    return out


def geometry(pm: ET.Element) -> dict | None:
    if (el := pm.find(".//k:Point/k:coordinates", NS)) is not None:
        lng, lat = coords(el.text)[0]
        return point(lng, lat)
    if (el := pm.find(".//k:LineString/k:coordinates", NS)) is not None:
        return {"type": "LineString", "coordinates": coords(el.text)}
    if (el := pm.find(".//k:Polygon/k:outerBoundaryIs//k:coordinates", NS)) is not None:
        return {"type": "Polygon", "coordinates": [coords(el.text)]}
    return None


def clean(desc: str | None) -> str | None:
    if not desc:
        return None
    text = re.sub(r"<img[^>]*>|<br\s*/?>", " ", desc)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip() or None


def scrape() -> list[dict]:
    root = ET.fromstring(get(session(), KML_URL).content)
    features = []
    for folder in root.iter(f"{{{NS['k']}}}Folder"):
        layer = folder.findtext("k:name", namespaces=NS) or ""
        spec = layer_type(layer)
        if spec is None:
            continue
        poi_type, extra = spec
        for i, pm in enumerate(folder.findall("k:Placemark", NS)):
            geom = geometry(pm)
            if geom is None:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", layer.lower()).strip("-")[:24]
            features.append(feature(geom, id=f"bike-{slug}-{i + 1:03d}", type=poi_type,
                                    name=(pm.findtext("k:name", namespaces=NS) or "").strip() or None,
                                    description=clean(pm.findtext("k:description", namespaces=NS)),
                                    layer=layer, source="USC Bicycle Coalition map (2015)", **extra))
    return features


def main(argv=None):
    argparse.ArgumentParser(prog="usc_data bike").parse_args(argv)
    features = scrape()
    write_geojson(f"{POIS}/usc_bike_resources.geojson", features, fetched_at=now_iso(), source_url=VIEWER_URL)
    counts = {}
    for f in features:
        counts[f["properties"]["type"]] = counts.get(f["properties"]["type"], 0) + 1
    print(f"usc_bike_resources.geojson: {len(features)} features {counts}")
