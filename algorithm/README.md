# Crime Cell Scoring Risk Model

Backend for SafeRoute: crime-aware walking routes around USC. This folder
(`algorithm/` on the `algorithm-init` branch) holds the **crime-risk model**.
It is a gradient-boosted (XGBoost) classifier that scores every H3 cell
(res 9, ~175 m) within **10 miles of USC** for every hour of the day. It also
holds the `score_cell` / `explain_cell` functions the Flask API will call.

## Setup

Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r algorithm/requirements.txt
```

## Train (run from the repo root, the folder that contains `algorithm/`)

```bash
python -m algorithm.data.ingest_crime        # ~650k LAPD incidents, 2020-2024, 10 mi of USC -> algorithm/data/crime.db (~1-5 min)
python -m algorithm.models.train_xgb         # builds features, trains, evaluates, saves model + plots (~2 min, ~1.5 GB RAM)
python -m algorithm.scoring.score --build-cache --date 2026-11-14   # every cell x 24 h -> algorithm/scoring/cache/risk_cache.json (~20 s)
```

Options:
- `python -m algorithm.models.train_xgb --target violent` trains on violent incidents only.
- `--skip-sklearn` skips the sklearn comparison model.
- `python -m algorithm.features.build_dataset` writes the sampled training frame
  to `models/artifacts/dataset.parquet` if you want to inspect it.

## Notebook

`explore_train_infer.ipynb` (in this folder) is the testing ground before the
model goes into the backend. It explores the data, trains and evaluates the
model inline with SHAP and calibration plots, then runs inference with
`score_cell` / `explain_cell` and draws the hourly heatmap. Open it from
`algorithm/` and use Restart & Run All (~3-4 min).

## Project structure

```
fight-or-flight-BE/
└── algorithm/                      the risk-model package (import as `algorithm.*`)
    ├── common/                     shared building blocks, no I/O of their own
    │   ├── config.py               every knob: area, H3 res, dates/splits, sampling, paths
    │   ├── geo.py                  H3 cell universe, darkness (astral), blue-light phones, distances
    │   └── severity.py             crime description -> violent / property / other
    ├── data/
    │   ├── ingest_crime.py         LAPD Socrata API -> crime.db           [offline step 1]
    │   └── crime.db                SQLite incidents table (git-ignored, ~130 MB)
    ├── features/
    │   └── build_dataset.py        History class: crime.db -> feature rows [offline step 2]
    ├── models/
    │   ├── train_xgb.py            train + evaluate + save                 [offline step 3]
    │   └── artifacts/              xgb_risk.ubj, model_meta.json, metrics.json
    ├── scoring/
    │   ├── score.py                score_cell / explain_cell / build_cache [online + step 4]
    │   └── cache/risk_cache.json   every modelled cell x 24 h, precomputed
    ├── notebooks/                  model_report.md + SHAP / calibration plots
    ├── explore_train_infer.ipynb   interactive explore -> train -> evaluate -> infer
    └── requirements.txt
```

### How the pieces connect

```
  LAPD open data (Socrata 2nrs-mtv8)
          │  data/ingest_crime.py            filter to 10-mile circle, tag H3 cell + severity tier
          ▼
  data/crime.db  (incidents table)
          │  features/build_dataset.py       History: prefix sums per cell/day/hour, neighbour rings
          ▼
  feature rows  (cell × date × hour, 21 features, target = ≥1 incident)
          │  models/train_xgb.py             time split, XGBoost + early stopping, prior correction
          ▼
  models/artifacts/xgb_risk.ubj + model_meta.json   (features, neg_rate, risk quantiles, area)
          │  scoring/score.py                loads model + History once, builds features for any
          │                                  (cell, hour, date), predicts, explains with SHAP
          ├──────────────► score_cell / explain_cell   (live, per request)
          ▼
  scoring/cache/risk_cache.json  (all cells × 24 h for one date, with top factors)
```

### What each part does

**`common/`: the shared contract.** Every other module imports its
constants from `config.py`, so changing the area (`RADIUS_MILES`), the H3
resolution, or the train/test years in one place changes the whole pipeline.
- `geo.py` decides which cells exist: `area_cells()` returns every res-9 cell
  inside the circle. It also answers "is it dark at this hour on this date?"
  and "how far is the nearest blue-light phone?".
- `severity.py` turns LAPD's free-text crime descriptions into the three tiers
  the features use.

**`data/`: raw data in, one table out.** `ingest_crime.py` is the only code
that touches the network. It pages through the LAPD API over the circle's
bounding box and keeps rows inside the circle. It drops redacted (0,0)
locations, adds the H3 cell and severity tier, and does a full refresh of
`crime.db`. Everything downstream reads only `crime.db`, so the pipeline runs
offline after this step.

**`features/`: the leakage-safe feature engine.** `History` loads `crime.db`
once and precomputes prefix sums: daily counts per cell (plain, severity-weighted
and violent), hour-of-day counts per cell per month, and ring-1 / ring-2
neighbour counts via sparse H3 adjacency. A window like "incidents in this cell
in the last 30 days" is then two array lookups. Every window ends *before* the
row's date, so no future data leaks in; this is verified by deleting future
incidents and checking the features don't change. The same `History.features()`
call is used for training rows, evaluation rows and live API requests, so
training and serving can't drift apart. History also decides which cells are
**modelled** (at least `MIN_CELL_INCIDENTS` in the training years) and which
are **no-data** (mostly other police jurisdictions).

**`models/`: training and evaluation.** `train_xgb.py` takes a sampled frame
(all positives + 5 negatives per positive) and splits it by time. It trains
XGBoost with early stopping and compares it with sklearn's
GradientBoostingClassifier and a past-year baseline. It evaluates on 2023 and
2024 with grid weights, so the metrics equal full-grid metrics. It saves three
things the rest of the package depends on:
- `xgb_risk.ubj`: the booster.
- `model_meta.json`: feature order, `neg_rate` for prior correction, the 2023
  probability quantiles that turn a probability into a 0-1 percentile risk,
  and the area definition.
- `metrics.json`: results, calibration and SHAP importances.

**`scoring/`: the only part the backend imports.** `score.py` lazily loads the
model, the metadata and a `History` (once per process). For any (cell, hour,
date) it builds features with the same code as training, applies the prior
correction and maps the probability to a percentile. `explain_cell` adds the
SHAP factors, and `build_cache` does all cells × 24 hours in one vectorised call.
For dates after 2023, history windows are anchored at the end of 2023 (see
the note below). Cells in the circle without LAPD data return `None`.

**`notebooks/` and the notebook.** These are reports and the testing ground.
They don't feed the pipeline.

### The "package with the model"

At serve time, the minimal set of files the backend needs is:

| Needed at runtime | Why |
|---|---|
| `algorithm/common/*.py` | config, H3 + darkness helpers |
| `algorithm/features/build_dataset.py` | the same feature code as training |
| `algorithm/scoring/score.py` | `score_cell`, `explain_cell`, `build_cache` |
| `algorithm/models/artifacts/xgb_risk.ubj`, `model_meta.json` | the trained model and how to read its output |
| `algorithm/data/crime.db` | history the features are computed from |
| `algorithm/scoring/cache/risk_cache.json` | optional: precomputed heatmap for fast `/hotzones` |

`ingest_crime.py` and `train_xgb.py` are offline tools. Rerun them to refresh
the data or retrain, then restart the API so it picks up the new artifacts.

## Use from Python

```python
from algorithm.scoring.score import score_cell, explain_cell, cell_status

score_cell("8929a1d66b3ffff", hour=22, date="2026-11-14")        # -> 0.0..1.0, or None if no data
score_cell("8929a1d66b3ffff", 14, "2026-11-14", {"is_dark": 1})  # override any feature
explain_cell("8929a1d66b3ffff", 22, "2026-11-14")                # risk, prob, status, top SHAP factors
cell_status("8929a56eb77ffff")                                   # "ok" | "no_data" | "outside"
```

- `risk` (what `score_cell` returns) is 0..1 and **relative**: the percentile of
  this cell-hour's predicted probability among all modelled cell-hours in 2023.
  Use it for heatmap colour and routing cost. Busy areas (downtown, Hollywood,
  Koreatown) sit near 1.0 all day; to compare blocks inside them, use `prob`.
- `prob` is the calibrated probability that at least one incident is reported
  in that cell during that hour (typically 0.03%-3%).
- `risk_cache.json` holds per cell `lat`, `lon`, and 24-length `risk`, `prob`
  and `top_factors` lists, indexed by hour. Each factor is
  `[feature_index, shap_effect]`, and the index points into the cache's
  `features` list. `no_data_cells` lists cells to draw grey.

## Flask API integration

The Flask app only needs **`algorithm.scoring.score`** (plus `h3` to turn
lat/lng into cells). Everything else in `algorithm/` is either imported by
`score.py` behind the scenes, or is an offline tool.

### What the API imports vs what stays offline

| Module | In the API? | Role |
|---|---|---|
| `algorithm/scoring/score.py` | **Yes, import directly** | `score_cell`, `explain_cell`, `cell_status`, `build_cache`, `LABELS` |
| `algorithm/common/config.py` | Yes, read constants | `CENTER`, `RADIUS_MILES`, `H3_RES`, `CACHE_PATH` for `/health` and validation |
| `algorithm/common/geo.py` | Yes, indirectly | `in_radius()` to validate a lat/lng; also used by `score.py` |
| `algorithm/features/build_dataset.py` | Indirectly | `score.py` builds live features with `History`, never call it yourself |
| `models/artifacts/xgb_risk.ubj`, `model_meta.json` | Loaded by `score.py` | model + percentile table |
| `data/crime.db` | Loaded by `score.py` | history behind the features |
| `scoring/cache/risk_cache.json` | Read by `/hotzones` | precomputed heatmap |
| `data/ingest_crime.py` | **No, offline** | refresh data (manual / weekly) |
| `models/train_xgb.py` | **No, offline** | retrain, then restart the API |
| `features/build_dataset.py` CLI | **No, offline** | dump the training frame for inspection |
| `score.py --build-cache` | **No, cron** | regenerate the cache daily (~20 s), e.g. just after midnight |
| notebook, `notebooks/` | No | testing ground and reports |

**Warm-up and cost (measured here):** the first call loads the model plus
`History`, which takes about 4 s and about 600 MB of RAM. After that,
`explain_cell` takes about 6 ms per cell-hour. Call `score._load()` once at
app start so the first user doesn't wait. Each gunicorn worker loads its own
copy, so use 1-2 workers with threads rather than many processes.

### Endpoints

| Endpoint | Uses | Returns |
|---|---|---|
| `GET /health` | `model_meta.json`, `metrics.json`, cache header | model trained_at, area, 2023 AUC, cache date, modelled / no-data cell counts |
| `GET /hotzones?hour=23[&date=YYYY-MM-DD]` | `risk_cache.json` (rebuild with `build_cache(date)` if the date differs and it's worth 20 s) | for that hour: `[{cell, risk, prob, factors}]` for modelled cells + `no_data_cells`. The frontend draws them with deck.gl's `H3HexagonLayer`; no GeoJSON needed |
| `GET /score?lat=..&lng=..&hour=..[&date=]` | `h3.latlng_to_cell` → `explain_cell` | `{cell, risk, prob, status, history_as_of, factors:[{label, value, effect}]}`; `status` is `ok`, `no_data` or `outside` (return 400 for `outside`) |
| `GET /route?from=lat,lng&to=lat,lng&hour=..` | routing module (not in `algorithm/` yet) + risk lookup from the cache | `fastest` and `safest` routes with minutes, mean risk, top factors along the way, and how many no-data cells they cross |

`/route` works like this. Each street edge in the OSM walking graph gets a
cost of `length × (1 + λ · risk(cell of the edge midpoint))`, with risk
read from the cache dict for that hour (O(1) per edge, so A* stays fast).
A no-data cell should get a neutral risk (for example 0.5), not 0, and
should be flagged in the response. That way routes never prefer an area just
because it has no data.

### Minimal sketch

```python
# app.py (repo root)
import json, datetime as dt
import h3
from flask import Flask, request, jsonify, abort
from algorithm.common import config, geo
from algorithm.scoring import score

app = Flask(__name__)
score._load()                                           # warm model + history at startup
CACHE = json.loads(config.CACHE_PATH.read_text())       # reload after the nightly cron

@app.get("/hotzones")
def hotzones():
    h = int(request.args.get("hour", dt.datetime.now().hour)) % 24
    feats = CACHE["features"]
    cells = [{"cell": c, "risk": v["risk"][h], "prob": v["prob"][h],
              "factors": [[score.LABELS[feats[i]], e] for i, e in v["top_factors"][h]]}
             for c, v in CACHE["cells"].items()]
    return jsonify(date=CACHE["date"], hour=h, cells=cells, no_data_cells=CACHE["no_data_cells"])

@app.get("/score")
def score_point():
    lat, lng = float(request.args["lat"]), float(request.args["lng"])
    if not geo.in_radius(lat, lng):
        abort(400, f"outside the {config.RADIUS_MILES:g}-mile model area")
    cell = h3.latlng_to_cell(lat, lng, config.H3_RES)
    return jsonify(score.explain_cell(cell, int(request.args.get("hour", 22)),
                                      request.args.get("date")))
```

## Data provenance

> [!NOTE]
> **Why we train only through 2022 and test on 2023 (and don't use 2024–2025)**
>
> The LAPD dataset we use ("Crime Data from 2020 to Present") stops in December 2024 and has **no 2025 rows**. In March 2024 LAPD switched its records system to NIBRS, and from then on this dataset's counts fall to roughly **half** of the usual volume. That drop is a change in how crimes are recorded, not a real drop in crime.
>
> If we trained on 2024, the model would learn that crime "fell" in 2024 and under-score every cell. So we **train on 2020–2022, test on 2023, and use 2024 only as a second out-of-time check** (AUC 0.803 there). For the same reason, live scores for today's date use history up to the end of 2023. LAPD now publishes post-2024 data as a separate NIBRS dataset with a different schema; adding it is a stretch goal.

- **Source:** LA City Open Data, "Crime Data from 2020 to Present" (LAPD),
  Socrata dataset `2nrs-mtv8`, https://data.lacity.org/resource/2nrs-mtv8.json.
  Fields kept: `dr_no, date_occ, time_occ, lat, lon, crm_cd, crm_cd_desc, weapon_used_cd, part_1_2`.
- **Area:** a 10-mile circle around Tommy Trojan (`CENTER`, `RADIUS_MILES` in
  `common/config.py`). That is 6,898 H3 cells, of which **3,432 are modelled**.
- **No-data cells:** the other 3,466 cells in the circle are policed by other
  departments: Inglewood, Culver City, Beverly Hills, Vernon, Huntington Park,
  and LA County Sheriff areas. LAPD doesn't record them, so they are `no_data`,
  not safe. LASD publishes incident data, but its historical files stop at
  2021, and Culver City / Inglewood / Beverly Hills publish no incident-level
  data. So these areas are not included yet.
- **Years:** see the note above. For dates after 2023, history features are
  anchored at the end of 2023 (`LIVE_HISTORY_END`); calendar and darkness
  features use the real date.
- **Redacted locations** (lat/lon 0,0) are dropped. LAPD uses `time_occ = 1200`
  when the time is unknown, so noon is slightly over-represented (about 3%).
- **Severity tiers** (`common/severity.py`): keyword lookup into violent
  (robbery, assault, battery, homicide, sex crimes, ...), property, other.
- **Darkness:** civil dawn/dusk for LA from `astral`.
- **Blue-light phones:** TODO. `common/geo.py` uses approximate campus-landmark
  points as **placeholders**. Put the real USC DPS phone coordinates in
  `data/bluelight_phones.csv` (`lat,lon,name`), then retrain.
- **Streetlights:** TODO, not loaded yet (LA Bureau of Street Lighting data).

## Model

Rows are (cell, date, hour) for the modelled cells. Target = at least one
incident. All history features use **only data from before the row's date**:

| Group | Features |
|---|---|
| Time | hour, weekday, month, season, is_weekend, is_dark |
| Place | cell centroid lat/lon, distance to nearest blue-light phone |
| Cell history | counts over the last 7/30/90 days, severity-weighted 90d, violent 90d, same-hour ±1 over the past 12 months, long-run rate, long-run same-hour rate |
| Neighbours | H3 ring-1 30d and 90d counts, ring-2 30d count |
| Area | all incidents in the modelled area in the last 7 days |

The full grid is about 30M cell-hours a year, so nothing materialises it.
Training keeps every positive plus 5 sampled negatives each, then undoes the
sampling with a prior correction, so outputs are real probabilities.
Evaluation keeps every positive plus 10 negatives each, weighted by 1/sample
rate. Split by time, never at random: train 2020-04 to 2022-06, early-stop on
2022-07 to 2022-12, test on 2023. XGBoost (`hist`, depth 4, lr 0.03, early
stopping) is compared with sklearn's `GradientBoostingClassifier`.

## Results (2023, full-grid estimate: 30.1M cell-hours, base rate 0.47%)

| Model | ROC-AUC | PR-AUC | Top-10% capture |
|---|---|---|---|
| **XGBoost** | **0.786** | **0.0273** | **41.5%** |
| sklearn GradientBoosting | 0.783 | 0.0235 | 40.9% |
| Past-year same-hour count (baseline) | 0.772 | 0.0261 | 40.4% |

On the 2024 out-of-time check, XGBoost gets AUC 0.803. Heatmap-level rank
correlation with what actually happened in 2023 is 0.72, and calibration is
close to perfect. Details, SHAP and calibration plots:
[notebooks/model_report.md](notebooks/model_report.md).

**Where this goes next:** spatio-temporal deep models such as ST-SHN and
DeepCrime learn cross-region and cross-category dependencies. They are the
natural scale-up once there is more data and more signals. For a hackathon,
gradient boosting trains in about two minutes, and every score can be explained.

## Bias and responsible use

LAPD data records what gets **reported and policed**, not where harm actually
happens. Areas with more patrols produce more records, and some crimes are
reported far less than others. That bias flows straight into this model. So:

- Every score ships with its SHAP factors (`explain_cell`, `top_factors` in
  the cache). The UI should show *why* a block scores high, not just a colour.
- Use risk as a **cost on route segments**, not a hard block. Do not silently
  route users around whole neighbourhoods.
- No-data areas must look different from low-risk areas on the map.
- Cell lat/lon is a feature, so the model partly learns "where". Watch for it
  turning into a proxy for neighbourhood demographics, and consider dropping
  it if the SHAP values are dominated by location.
