"""Render a planned route as a standalone Leaflet HTML map (no extra Python deps).

Shows the safe route and the plain shortest route, the H3 risk hexes at the
chosen hour, blue-light phones (the ones the safe route passes are
highlighted), and a summary panel. Open the HTML file in any browser.
"""
import html
import json
from pathlib import Path

import h3
import numpy as np

from algorithm.common import geo

RISK_STOPS = [(0.0, "#1a9850"), (0.25, "#91cf60"), (0.5, "#fee08b"), (0.75, "#fc8d59"), (1.0, "#d73027")]
NO_DATA_COLOR = "#9ca3af"


def _hex(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def risk_color(r):
    if r is None:
        return NO_DATA_COLOR
    r = min(max(float(r), 0.0), 1.0)
    for (t0, c0), (t1, c1) in zip(RISK_STOPS, RISK_STOPS[1:]):
        if r <= t1:
            f = (r - t0) / (t1 - t0)
            a, b = _hex(c0), _hex(c1)
            return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))
    return RISK_STOPS[-1][1]


def _hex_features(result, risk, margin_deg=0.006):
    pts = np.array(result["safe"].coords + result["shortest"].coords)
    s, n = pts[:, 0].min() - margin_deg, pts[:, 0].max() + margin_deg
    w, e = pts[:, 1].min() - margin_deg, pts[:, 1].max() + margin_deg
    cells = h3.polygon_to_cells(h3.LatLngPoly([(s, w), (s, e), (n, e), (n, w)]), risk.res)
    feats = []
    for c in cells:
        r, known = risk.risk(c, result["hour"])
        ring = [[lon, lat] for lat, lon in h3.cell_to_boundary(c)]
        ring.append(ring[0])
        feats.append({"type": "Feature",
                      "geometry": {"type": "Polygon", "coordinates": [ring]},
                      "properties": {"color": risk_color(r if known else None),
                                     "tip": f"cell {c}<br>risk {r:.2f}" if known else
                                            f"cell {c}<br>no LAPD data (neutral {r:.2f})"}})
    return {"type": "FeatureCollection", "features": feats}, (s, w, n, e)


def _row(label, a, b):
    return f"<tr><td>{label}</td><td><b>{a}</b></td><td>{b}</td></tr>"


def render_map(result, risk, out_path, title="SafeRoute"):
    safe, short = result["safe"], result["shortest"]
    hexes, (s, w, n, e) = _hex_features(result, risk)
    passed = {(p["lat"], p["lon"]) for p in safe.phones_passed}
    phones = [{"lat": la, "lon": lo, "name": nm, "passed": (la, lo) in passed}
              for la, lo, nm in geo.bluelight_phones()
              if s - 0.01 <= la <= n + 0.01 and w - 0.01 <= lo <= e + 0.01]
    hr = result["hour"]
    p = result["params"]
    same = safe.coords == short.coords
    summary = (
        f"<div class='t'>{html.escape(title)}</div>"
        f"<div class='m'>{hr:02d}:00, risk cache {result['risk_date']}, "
        f"detour cap {p['max_detour'] * 100:.0f}%</div>"
        "<table><tr><th></th><th style='color:#1d4ed8'>Safe</th><th style='color:#4b5563'>Shortest</th></tr>"
        + _row("Distance", f"{safe.length_m / 1000:.2f} km", f"{short.length_m / 1000:.2f} km")
        + _row("Detour", f"{safe.detour_pct:+.1f}%", "0%")
        + _row("Mean risk", f"{safe.mean_risk:.2f}", f"{short.mean_risk:.2f}")
        + _row("Max risk", f"{safe.max_risk:.2f}", f"{short.max_risk:.2f}")
        + _row(f"Risk &ge; 0.8", f"{safe.high_risk_m:.0f} m", f"{short.high_risk_m:.0f} m")
        + _row("Phones passed", f"{safe.n_phones}", f"{short.n_phones}")
        + _row("No-data length", f"{safe.no_data_pct:.0f}%", f"{short.no_data_pct:.0f}%")
        + "</table>"
        + (f"<div class='m'>Safe route is the shortest route here.</div>" if same else "")
        + f"<div class='m'>{result['n_feasible']} of {result['n_candidates']} candidate routes "
          f"within the cap; picked by mean risk &minus; {p['phone_weight']} &times; phones.</div>"
        + "<div class='lg'>" + "".join(
            f"<span style='background:{risk_color(t)}'></span>" for t in (0, .25, .5, .75, 1))
        + "<em>risk 0 &rarr; 1</em><span style='background:" + NO_DATA_COLOR + "'></span><em>no data</em></div>"
    )
    data = {"hexes": hexes, "safe": safe.coords, "short": short.coords, "phones": phones,
            "start": result["start"], "end": result["end"], "bounds": [[s, w], [n, e]]}
    page = TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__SUMMARY__", summary) \
                   .replace("__DATA__", json.dumps(data))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path


TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 html,body,#map{height:100%;margin:0;background:#f3f4f6}
 .panel{position:absolute;top:12px;right:12px;z-index:1000;background:#fff;padding:12px 14px;
   border-radius:10px;box-shadow:0 2px 12px rgba(0,0,0,.18);font:13px/1.4 system-ui,sans-serif;max-width:300px}
 .panel .t{font-weight:700;font-size:15px}.panel .m{color:#6b7280;font-size:12px;margin:4px 0}
 .panel table{border-collapse:collapse;width:100%;margin:6px 0}
 .panel td,.panel th{padding:2px 6px;text-align:left}.panel tr:nth-child(even){background:#f9fafb}
 .lg{display:flex;align-items:center;gap:3px;font-size:11px;color:#6b7280;margin-top:6px}
 .lg span{display:inline-block;width:16px;height:10px;border-radius:2px}.lg em{margin:0 6px 0 3px}
 .key{display:flex;gap:10px;font-size:12px;margin-top:6px}
 .key i{display:inline-block;width:22px;height:4px;vertical-align:middle;margin-right:4px}
 @media (max-width:600px){.panel{left:12px;right:12px;max-width:none}}
</style></head><body>
<div id="map"></div>
<div class="panel">__SUMMARY__
 <div class="key"><span><i style="background:#1d4ed8"></i>safe</span>
 <span><i style="background:repeating-linear-gradient(90deg,#374151 0 6px,transparent 6px 10px)"></i>shortest</span>
 <span>&#9679; phone</span></div>
</div>
<script>
const D = __DATA__;
const map = L.map('map');
map.fitBounds(L.latLngBounds(D.safe.concat(D.short)).pad(0.15));
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
  {maxZoom: 20, attribution: '&copy; OpenStreetMap contributors &copy; CARTO'}).addTo(map);
const hex = L.geoJSON(D.hexes, {style: f => ({color: f.properties.color, weight: 0.5,
  fillColor: f.properties.color, fillOpacity: 0.38, opacity: 0.6}),
  onEachFeature: (f, l) => l.bindTooltip(f.properties.tip, {sticky: true})}).addTo(map);
const shortL = L.polyline(D.short, {color: '#374151', weight: 4, dashArray: '8 6', opacity: 0.85}).addTo(map);
L.polyline(D.safe, {color: '#ffffff', weight: 10, opacity: 0.9}).addTo(map);
const safeL = L.polyline(D.safe, {color: '#1d4ed8', weight: 6, opacity: 0.95}).addTo(map);
const ph = L.layerGroup(D.phones.map(p => L.circleMarker([p.lat, p.lon], p.passed ?
  {radius: 8, color: '#ffffff', weight: 2, fillColor: '#2563eb', fillOpacity: 1} :
  {radius: 4, color: '#1e3a8a', weight: 1, fillColor: '#60a5fa', fillOpacity: 0.9})
  .bindTooltip(p.name + (p.passed ? ' (on route)' : '')))).addTo(map);
L.marker(D.start).bindTooltip('A (start)', {permanent: true, direction: 'left'}).addTo(map);
L.marker(D.end).bindTooltip('B (end)', {permanent: true, direction: 'left'}).addTo(map);
L.control.layers(null, {'Risk hexes': hex, 'Safe route': safeL, 'Shortest route': shortL,
  'Blue-light phones': ph}, {position: 'bottomleft'}).addTo(map);
</script></body></html>
"""
