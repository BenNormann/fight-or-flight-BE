# pathfinding: safe walking routes

Plans a walking route from A to B that

1. keeps the cumulative crime risk of the H3 cells it crosses low,
2. walks past as many USC blue-light emergency phones as it can, and
3. is never more than a set percentage longer than the plain shortest path
   (default 20%),

then draws it on a map next to the shortest route.

It reads the model's output and never retrains anything: risk comes from
`algorithm/scoring/cache/risk_cache.json` (every cell x 24 hours) and phones
from `algorithm/data/bluelight_phones.csv` via `algorithm.common.geo`.
Nothing in `algorithm/` is modified.

## Run it (from the repo root)

```bash
pip install -r algorithm/requirements.txt     # needs requests, numpy, h3 (already listed there)
python -m pathfinding                         # example: USC Village -> Expo/Vermont station at 10 PM
```

The first run downloads the OpenStreetMap walking network for the 5-mile area
from the Overpass API (a minute or two, needs internet) and caches it in
`pathfinding/cache/walk_graph.json.gz`. Later runs load it in a few seconds.
Each route then takes well under a second.

Output: `pathfinding/out/route_map.html` (open it in a browser) plus a JSON
summary in the terminal. The map shows the safe route (blue), the shortest
route (dashed grey), risk hexes at that hour (green to red, grey = no LAPD
data), and phones (big blue dots are the ones the safe route passes).

Your own trip:

```bash
python -m pathfinding --from 34.0256,-118.2853 --to 34.0300,-118.2930 --hour 23 --max-detour 0.15
python -m pathfinding.graph        # or: python -m pathfinding --refresh-graph   (re-download OSM)
```

| Option | Default | Meaning |
|---|---|---|
| `--from`, `--to` | USC Village, Expo/Vermont | `lat,lon` |
| `--hour` | 22 | local clock hour 0-23; risk changes a lot by hour |
| `--max-detour` | 0.20 | hard cap: at most 20% longer than the shortest path |
| `--phone-weight` | 0.05 | how much one more phone passed is worth, in mean-risk points |
| `--pass-radius` | 35 | metres from a phone that count as passing it |

Other knobs (`LAMBDAS`, `BETAS`, `DETOUR_WEIGHT`, `PHONE_SNAP_M`) are at the
top of `planner.py`; `NO_DATA_RISK` (0.5) is in `risk.py`.

## How it picks the route

1. **Shortest path first.** Snap A and B to the nearest OSM nodes and run A*
   by length. Its length `L0` sets the budget `(1 + max_detour) x L0`.
2. **Prune to an ellipse.** Keep only nodes with
   `dist(A, n) + dist(n, B) <= budget` (straight-line). Straight-line distance
   can't exceed walking distance, so no valid route is lost, and the search
   shrinks to a few thousand nodes.
3. **Score edges.** Each street segment gets the length-weighted mean risk of
   the H3 cells it crosses at that hour (sampled every third of a cell edge), and a phone
   protection `exp(-d / 150 m)` from its midpoint to the nearest phone (same
   curve as `geo.bluelight_protection`). Cells with no LAPD data get a neutral
   0.5, so routes never favour "unknown" areas.
4. **Generate candidates.** For each pair of weights `lam` (risk) and `beta`
   (phone pull) in a small grid, edge cost is
   `length x (1 + lam x risk) x (1 - beta x protection)`. Dijkstra runs once from
   A and once from B, which gives the direct cheapest route and, for every
   phone in the ellipse, the route A -> phone -> B for free (join the two
   trees at the phone). That's how routes that actually reach phones get
   considered.
5. **Enforce the cap, then choose.** Drop every candidate longer than the
   budget. Among the rest pick the lowest
   `mean_risk - phone_weight x phones_passed + 0.05 x detour`. The shortest path
   is always a candidate, so there's always an answer.

Why this and not one weighted shortest path: a single fixed weight either
ignores risk or blows past the detour limit. Sweeping the weights is a cheap
Lagrangian search for the constrained problem, and the via-phone joins let
the planner trade a short detour for actually walking past phones, which a
plain edge bonus rarely does.

## Use from the Flask API

```python
from pathfinding.graph import load_graph
from pathfinding.risk import load_risk
from pathfinding.planner import plan_route

g, rg = load_graph(), load_risk()          # once at app start (~2-5 s)
res = plan_route((34.0256, -118.2853), (34.0183, -118.2916), hour=22,
                 max_detour=0.2, graph=g, risk=rg)
res["safe"].coords          # [[lat, lon], ...] polyline for the frontend
res["safe"].summary()       # length_m, detour_pct, mean_risk, max_risk, n_phones, phones_passed, ...
res["shortest"].summary()
```

## Zero-risk zones (USC campus and USC Village)

Every H3 cell whose centre is inside the USC University Park Campus outline
(Vermont, Jefferson, Figueroa, Exposition) or the USC Village outline
(McClintock, 30th St, Hoover, Jefferson) scores risk 0 at every hour, whatever
the cache says. The outlines are in `campus.py` (`ZERO_RISK_ZONES`), traced from
the OSM street centrelines in the cached walk graph. The cells are recomputed at
the risk cache's resolution, so it works on res 9 and res 10 without changes.
`python -m pathfinding.campus` lists the cells it zeroes.

Limits: risk is per H3 cell (the resolution follows `config.H3_RES` and the
cache, so it changes when the model does), so it can't tell the two sides of
a street apart; the risk cache is for one date (rebuild it daily); the OSM
graph doesn't know about gates that close at night on campus.

Street data © OpenStreetMap contributors (ODbL).
