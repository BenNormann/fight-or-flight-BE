"""Interactive hover map of the final risk scores, as one self-contained HTML file.

Usage (from the repo root):
    python -m algorithm.scoring.risk_map                    # scores from risk_cache.json -> notebooks/risk_map.html
    python -m algorithm.scoring.risk_map --date 2026-11-14 --open

Open the HTML in any browser: hover a tile to see its risk, probability, top
reasons and nearest blue-light phone; drag the slider to change the hour; click
a tile to pin it. Plain SVG + a few lines of JavaScript: no Jupyter widgets, no
WebGL, no internet, no extra Python packages.

From Python / a notebook:
    from algorithm.scoring.risk_map import write_map
    path = write_map()          # returns the HTML path
"""
import argparse
import json
import math
import webbrowser
from pathlib import Path

import h3

from algorithm.common import config, geo


def zero_risk_cells(res):
    """Cells inside the USC campus / USC Village outlines (pathfinding/campus.py),
    which routing treats as risk 0. Empty if pathfinding isn't importable."""
    try:
        from pathfinding.campus import zero_risk_cells as cells
    except ImportError:
        return frozenset()
    return cells(int(res))


def load_scores(date=None):
    """The cache dict (see score.build_cache) for `date`; rebuilt if needed."""
    if config.CACHE_PATH.exists():
        cache = json.loads(config.CACHE_PATH.read_text())
        same_res = cache.get("h3_res") == config.H3_RES
        if same_res and (date is None or cache["date"] == str(date)):
            return cache
    from algorithm.scoring.score import build_cache
    return build_cache(date)


def _projector(width=900):
    """lat/lng -> SVG x/y (equirectangular, scaled for LA's latitude)."""
    s, w, n, e = config.BBOX
    pad = 0.004  # room for hexes whose centre is inside the circle but whose edge is not
    s, w, n, e = s - pad, w - pad, n + pad, e + pad
    kx = math.cos(math.radians(config.CENTER[0]))
    sx = width / ((e - w) * kx)
    height = (n - s) * sx
    return (lambda lat, lng: (round((lng - w) * kx * sx, 1), round((n - lat) * sx, 1))), width, round(height)


def build_html(cache=None, date=None, hour=22, top_k=3) -> str:
    cache = cache or load_scores(date)
    proj, W, H = _projector()
    zero = zero_risk_cells(cache.get("h3_res", config.H3_RES))

    def path(c):
        pts = [proj(lat, lng) for lat, lng in h3.cell_to_boundary(c)]
        return "M" + "L".join(f"{x},{y}" for x, y in pts) + "Z"

    feats, labels = cache["features"], cache["feature_labels"]
    tiles = []
    for c, v in cache["cells"].items():
        if c in zero:  # USC campus / Village: shown at 0, as routing scores it
            tiles.append({"id": c, "d": path(c), "r": [0.0] * 24, "p": v["prob"], "z": 1,
                          "bl": v.get("bluelight_dist_m"), "pn": v.get("bluelight_phones_150m", 0),
                          "pd": 0, "f": [[] for _ in range(24)]})
            continue
        tiles.append({
            "id": c, "d": path(c), "r": v["risk"], "p": v["prob"],
            "bl": v.get("bluelight_dist_m"),
            # top reasons per hour as [feature index, effect]
            "f": [[[j, e] for j, e in fs[:top_k]] for fs in v["top_factors"]],
        })
    data = {
        "date": cache["date"], "history_as_of": cache.get("history_as_of"), "res": cache.get("h3_res"),
        "labels": [labels.get(f, f) for f in feats], "tiles": tiles,
        "nodata": [path(c) for c in cache.get("no_data_cells", [])],
        "phones": [list(proj(a, b)) + [name] for a, b, name in geo.bluelight_phones()],
        "center": list(proj(*config.CENTER)), "W": W, "H": H, "hour": hour,
        "radius": config.RADIUS_MILES,
    }
    return _TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))


def write_map(path=None, cache=None, date=None, hour=22) -> Path:
    path = Path(path or config.REPORT_DIR / "risk_map.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(cache, date, hour), encoding="utf-8")
    return path


_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SafeRoute risk map</title>
<style>
  body{margin:0;font:14px/1.4 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#fafafa;color:#1f2937}
  .wrap{display:flex;gap:16px;padding:16px;flex-wrap:wrap}
  .map{flex:1 1 640px;min-width:320px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:8px}
  .side{flex:0 1 330px;min-width:280px;display:flex;flex-direction:column;gap:12px}
  .card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px}
  h1{font-size:18px;margin:0 0 4px} .muted{color:#6b7280;font-size:12px}
  svg{width:100%;height:auto;display:block}
  path.t{stroke:#fff;stroke-width:.4;cursor:pointer} path.t:hover{stroke:#1d4ed8;stroke-width:2}
  path.zone{stroke:#047857;stroke-width:1.2}
  path.sel{stroke:#1d4ed8!important;stroke-width:2.5!important}
  #tip{position:fixed;pointer-events:none;background:#fff;border:1px solid #9ca3af;border-radius:6px;
       padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;white-space:nowrap}
  .big{font-size:28px;font-weight:700} .row{display:flex;justify-content:space-between;gap:8px}
  .bar{height:10px;border-radius:5px;background:linear-gradient(90deg,#fcefe4,#f6a47a,#e2463f,#a3104f,#2e0a3a)}
  input[type=range]{width:100%} ul{margin:4px 0 0 18px;padding:0} .up{color:#b91c1c} .down{color:#047857}
</style></head><body>
<div class="wrap">
  <div class="map">
    <h1 id="title"></h1>
    <div class="muted" id="sub"></div>
    <svg id="svg"></svg>
  </div>
  <div class="side">
    <div class="card">
      <div class="row"><b>Hour</b><span id="hl"></span></div>
      <input id="hour" type="range" min="0" max="23" step="1">
      <div class="bar"></div><div class="row muted"><span>0 safest</span><span>risk</span><span>1 riskiest</span></div>
      <div class="muted" style="margin-top:6px">Grey = no LAPD data (another police department): unknown, not safe.
      Blue dots = USC blue-light phones. Green outline = USC campus / USC Village, set to 0 (zero risk for routing). Click a tile to pin it.</div>
    </div>
    <div class="card" id="detail"><span class="muted">Hover a tile to see its score.</span></div>
    <div class="card"><b>24-hour risk</b> <span class="muted" id="cid"></span>
      <svg id="curve" viewBox="0 0 300 120"></svg></div>
  </div>
</div>
<div id="tip"></div>
<script>
const D = __DATA__;
const NS = "http://www.w3.org/2000/svg";
const stops = [[0,[252,239,228]],[.25,[246,164,122]],[.5,[226,70,63]],[.75,[163,16,79]],[1,[46,10,58]]];
function color(r){ for(let i=1;i<stops.length;i++){ if(r<=stops[i][0]){ const [a,ca]=stops[i-1],[b,cb]=stops[i], t=(r-a)/(b-a);
  return `rgb(${ca.map((v,k)=>Math.round(v+(cb[k]-v)*t)).join(",")})`; } } return "rgb(46,10,58)"; }
const svg = document.getElementById("svg"); svg.setAttribute("viewBox", `0 0 ${D.W} ${D.H}`);
const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); (parent||svg).appendChild(e); return e; };
D.nodata.forEach(d => el("path", {d, fill:"#d4d4d4"}));
const paths = D.tiles.map((t, i) => { const p = el("path", {d:t.d, class:"t"}); p.dataset.i = i; return p; });
D.phones.forEach(([x,y,n]) => { const c = el("circle", {cx:x, cy:y, r:2.4, fill:"#1d4ed8"}); el("title", {}, c).textContent = "blue-light phone " + n; });
el("path", {d:`M${D.center[0]-6},${D.center[1]}h12M${D.center[0]},${D.center[1]-6}v12`, stroke:"#111", "stroke-width":2});
let hour = D.hour, pinned = null, current = null;
const slider = document.getElementById("hour"); slider.value = hour;
const hh = h => String(h).padStart(2,"0") + ":00";
function paint(){ paths.forEach((p,i) => { p.classList.toggle("zone", !!D.tiles[i].z); p.setAttribute("fill", color(D.tiles[i].r[hour])); });
  document.getElementById("title").textContent = `Risk at ${hh(hour)} on ${D.date}`;
  document.getElementById("sub").textContent = `${D.tiles.length.toLocaleString()} tiles (H3 res ${D.res}) within ${D.radius} miles of USC; crime history as of ${D.history_as_of}`;
  document.getElementById("hl").textContent = hh(hour);
  if (current !== null) detail(current); }
function detail(i){ const t = D.tiles[i], r = t.r[hour];
  const reasons = t.f[hour].map(([j,e]) => `<li class="${e>0?"up":"down"}">${e>0?"▲":"▼"} ${D.labels[j]} <span class="muted">(${e>0?"+":""}${e.toFixed(2)})</span></li>`).join("");
  document.getElementById("detail").innerHTML =
    `<div class="row"><span class="big" style="color:${color(Math.max(r,.35))}">${r.toFixed(2)}</span><span class="muted">risk at ${hh(hour)}<br>riskier than ${Math.round(r*100)}% of cell-hours</span></div>
     <div>${(t.p[hour]*100).toFixed(2)}% chance of a reported incident this hour</div>
     <div>Nearest blue-light phone: ${t.bl==null?"?":t.bl.toLocaleString()+" m"}</div>
     ${t.z ? '<div class="down"><b>USC campus / USC Village: set to 0 (zero risk for routing)</b></div>' : ''}
     <div style="margin-top:6px"><b>Why</b><ul>${reasons}</ul></div><div class="muted" style="margin-top:6px">${t.id}</div>`;
  document.getElementById("cid").textContent = t.id; drawCurve(t); }
function drawCurve(t){ const c = document.getElementById("curve"); c.innerHTML = "";
  const X = h => 20 + h*270/23, Y = r => 105 - r*95;
  [0,.5,1].forEach(v => { el("line",{x1:20,x2:290,y1:Y(v),y2:Y(v),stroke:"#e5e7eb"},c); const tx = el("text",{x:2,y:Y(v)+4,"font-size":9,fill:"#6b7280"},c); tx.textContent = v; });
  [0,6,12,18,23].forEach(h => { const tx = el("text",{x:X(h)-5,y:118,"font-size":9,fill:"#6b7280"},c); tx.textContent = h; });
  el("line",{x1:X(hour),x2:X(hour),y1:5,y2:105,stroke:"#1d4ed8","stroke-dasharray":"3,3"},c);
  el("polyline",{points:t.r.map((r,h)=>`${X(h)},${Y(r)}`).join(" "),fill:"none",stroke:"#e2463f","stroke-width":2.5},c);
  el("circle",{cx:X(hour),cy:Y(t.r[hour]),r:4,fill:"#e2463f"},c); }
const tip = document.getElementById("tip");
svg.addEventListener("mousemove", ev => { const i = ev.target.dataset ? ev.target.dataset.i : undefined;
  if (i === undefined) { tip.style.display = "none"; return; }
  const t = D.tiles[+i]; tip.innerHTML = `<b>risk ${t.r[hour].toFixed(2)}</b> · ${(t.p[hour]*100).toFixed(2)}% · ${hh(hour)}`;
  tip.style.display = "block"; tip.style.left = (ev.clientX + 14) + "px"; tip.style.top = (ev.clientY + 14) + "px";
  if (pinned === null && current !== +i) { current = +i; detail(current); } });
svg.addEventListener("mouseleave", () => { tip.style.display = "none"; });
svg.addEventListener("click", ev => { const i = ev.target.dataset ? ev.target.dataset.i : undefined;
  if (pinned !== null) paths[pinned].classList.remove("sel");
  if (i === undefined || +i === pinned) { pinned = null; return; }
  pinned = +i; current = +i; paths[pinned].classList.add("sel"); detail(current); });
slider.addEventListener("input", () => { hour = +slider.value; paint(); });
paint();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: the cached date)")
    ap.add_argument("--hour", type=int, default=22)
    ap.add_argument("--out", default=None, help="default: algorithm/notebooks/risk_map.html")
    ap.add_argument("--open", action="store_true", help="open the map in your browser")
    args = ap.parse_args()
    path = write_map(args.out, date=args.date, hour=args.hour)
    print(f"Wrote {path} ({path.stat().st_size / 1e6:.1f} MB). Open it in a browser and hover a tile.")
    if args.open:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
