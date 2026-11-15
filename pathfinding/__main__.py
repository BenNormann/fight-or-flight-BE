"""Plan a safe walking route and draw it on a map.

Run from the repo root (the folder that contains algorithm/ and pathfinding/):

    python -m pathfinding                                   # example: USC Village -> Expo/Vermont station, 10 PM
    python -m pathfinding --from 34.0256,-118.2853 --to 34.0300,-118.2930 --hour 23 --max-detour 0.15
    python -m pathfinding --refresh-graph                   # re-download the OSM walk graph

Writes pathfinding/out/route_map.html and prints a JSON summary.
"""
import argparse
import json
import time
from pathlib import Path

from .graph import download_graph, load_graph
from .map import render_map
from .planner import MAX_DETOUR, PASS_RADIUS_M, PHONE_WEIGHT, plan_route
from .risk import load_risk

EXAMPLE_FROM = (34.0256, -118.2853)   # USC Village (Jefferson Blvd side)
EXAMPLE_TO = (34.0183, -118.2916)     # Expo/Vermont Metro station
OUT = Path(__file__).resolve().parent / "out" / "route_map.html"


def _pt(s):
    lat, lon = (float(x) for x in s.split(","))
    return lat, lon


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="start", type=_pt, default=EXAMPLE_FROM, help="lat,lon")
    ap.add_argument("--to", dest="end", type=_pt, default=EXAMPLE_TO, help="lat,lon")
    ap.add_argument("--hour", type=int, default=22, help="local clock hour 0-23 (default 22)")
    ap.add_argument("--max-detour", type=float, default=MAX_DETOUR,
                    help=f"max extra length over the shortest path, 0.2 = 20%% (default {MAX_DETOUR})")
    ap.add_argument("--phone-weight", type=float, default=PHONE_WEIGHT,
                    help=f"mean-risk points one passed phone is worth (default {PHONE_WEIGHT})")
    ap.add_argument("--pass-radius", type=float, default=PASS_RADIUS_M,
                    help=f"metres from a phone that count as passing it (default {PASS_RADIUS_M:g})")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--refresh-graph", action="store_true", help="re-download the OSM walk graph first")
    args = ap.parse_args()

    if args.refresh_graph:
        download_graph()
        load_graph.cache_clear()
    t0 = time.time()
    g, rg = load_graph(), load_risk()
    t1 = time.time()
    res = plan_route(args.start, args.end, hour=args.hour, max_detour=args.max_detour,
                     phone_weight=args.phone_weight, pass_radius_m=args.pass_radius, graph=g, risk=rg)
    t2 = time.time()
    out = render_map(res, rg, args.out, title=f"SafeRoute at {args.hour:02d}:00")
    print(json.dumps({
        "safe": res["safe"].summary(), "shortest": res["shortest"].summary(),
        "candidates": res["n_candidates"], "within_cap": res["n_feasible"],
        "search_nodes": res["search_nodes"],
        "timing_s": {"load": round(t1 - t0, 2), "plan": round(t2 - t1, 2)},
    }, indent=2))
    print(f"\nMap written to {out}")


if __name__ == "__main__":
    main()
