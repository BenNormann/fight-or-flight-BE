# Crime Cell Scoring Risk Model

Backend for SafeRoute: crime-aware walking routes around USC. This folder
(`algorithm/` on the `algorithm-init` branch) holds the **crime-risk model**.
It is a gradient-boosted (XGBoost) classifier that scores every H3 cell
(res 9 hexagons, ~370 m across, ~0.12 km²) within **5 miles of USC** for every hour of the day. It also
holds the `score_cell` / `explain_cell` functions the Flask API will call.

**Step-by-step training, running and inference parameters:
[MODEL_GUIDE.md](MODEL_GUIDE.md).**

## Setup

Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r algorithm/requirements.txt
```

## Train (run from the repo root, the folder that contains `algorithm/`)

```bash
python -m algorithm.data.ingest_crime        # ~400k LAPD incidents, 2020-2024, 5 mi of USC -> algorithm/data/crime.db (~1 min)
python -m algorithm.models.train_xgb         # builds features, trains, evaluates, saves model + plots (~1-2 min, ~1.5 GB RAM)
python -m algorithm.scoring.score --build-cache --date 2026-11-14   # every cell x 24 h -> algorithm/scoring/cache/risk_cache.json (~5 s)
```

Options:
- `python -m algorithm.models.train_xgb --target violent` trains on violent incidents only.
- `--skip-sklearn` skips the sklearn comparison model.
- `python -m algorithm.features.build_dataset` writes the sampled training frame
  to `models/artifacts/dataset.parquet` if you want to inspect it.

## Notebook

`explore_train_infer.ipynb` (in this folder) is the testing ground before the
model goes into the backend. Open it from `algorithm/` and use Restart & Run
All (~2 min). Its sections are:

1. **Data:** volume over time (with the NIBRS drop), hour × weekday harm, hex maps with the blue-light phones.
2. **Per-crime weights:** the severity mix, recency decay curves, the time-of-day kernel, the blue-light factor.
3. **Features:** every feature with its definition and target correlation, the feature correlation matrix, and "does more history mean more risk?" curves.
4. **Train:** XGBoost (monotone constraints) and sklearn GBC.
5. **Evaluate:** grid-weighted 2023 metrics, ROC / PR / calibration, heatmap-level check.
6. **Explain:** SHAP importance and learned direction.
7. **Inference:** `score_cell` for landmarks, `explain_cell`, a blue-light what-if, hourly heatmaps.
8. **Sanity checks** on the saved model, each printing PASS or FLAG: dangerous vs quiet places, most-harm vs quietest cells, predicted vs actual per cell, monotonicity, blue-light, time of day.
9. **Model evaluation summary:** the final metrics table and decile calibration.

`risk_map_interactive.ipynb` (or `python -m algorithm.scoring.risk_map --open`)
writes `notebooks/risk_map.html`: a hover map of the final scores. Hover a tile
to see its risk, probability, top reasons and nearest blue-light phone; drag
the slider to change the hour. It's plain SVG + JavaScript, so it opens in any
browser with no extra packages.

## Project structure

```
fight-or-flight-BE/
└── algorithm/                      the risk-model package (import as `algorithm.*`)
    ├── common/                     shared building blocks, no I/O of their own
    │   ├── config.py               every knob: area, H3 res, dates, half-lives, blue-light, paths
    │   ├── geo.py                  H3 cell universe, darkness (astral), blue-light phones + protection
    │   └── severity.py             LAPD crime code -> 0-10 harm score (per-crime weight)
    ├── data/
    │   ├── ingest_crime.py         LAPD Socrata API -> crime.db           [offline step 1]
    │   ├── bluelight_from_concept3d.py  USC map export -> bluelight_phones.csv
    │   └── crime.db                SQLite incidents table (git-ignored, ~75 MB)
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
    ├── risk_map_interactive.ipynb  writes + opens the hover map (scoring/risk_map.py)
    ├── MODEL_GUIDE.md          how to train, run, and call the model (inference parameters)
    └── requirements.txt
```

### How the pieces connect

```
  LAPD open data (Socrata 2nrs-mtv8)
          │  data/ingest_crime.py            filter to 5-mile circle, tag H3 cell
          ▼
  data/crime.db  (incidents table)
          │  features/build_dataset.py       History: per-crime weights (severity x recency x time of day)
          ▼
  feature rows  (cell × date × hour, 18 features, target = ≥1 incident)
          │  models/train_xgb.py             time split, XGBoost + early stopping, prior correction
          ▼
  models/artifacts/xgb_risk.ubj + model_meta.json   (features, neg_rate, risk quantiles, area)
          │  scoring/score.py                loads model + History once, builds features for any
          │                                  (cell, hour, date), predicts, applies blue-light factor, SHAP
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
- `severity.py` gives every crime a 0-10 harm score from its LAPD crime code
  (homicide 10 ... fraud 0.25, weapon x1.5). The features weight each crime by
  this score, its recency and how close its hour is to the row's hour.

**`data/`: raw data in, one table out.** `ingest_crime.py` is the only code
that touches the network. It pages through the LAPD API over the circle's
bounding box and keeps rows inside the circle. It drops redacted (0,0)
locations, adds the H3 cell and a display tier, and does a full refresh of
`crime.db`. Everything downstream reads only `crime.db`, so the pipeline runs
offline after this step.

**`features/`: the leakage-safe feature engine.** `History` loads `crime.db`
once. It computes **per-crime weighted history**: each past crime counts
`severity x 0.5**(age / half-life) x exp(-hour_gap**2 / (2 x 2h**2))`, summed per
cell (half-lives 7, 30 and 365 days) and over ring-1 / ring-2 neighbours via
sparse H3 adjacency. One forward pass over the calendar keeps these decayed
sums up to date. Plain prefix sums give long-run and same-hour counts.
Everything ends *before* the row's date, so no future data leaks in. The same `History.features()`
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
correction, multiplies by the blue-light factor (up to 25% lower next to a
phone) and maps the probability to a percentile. `explain_cell` adds the
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
  Use it for heatmap colour and routing cost. Busy areas (downtown, MacArthur
  Park, Koreatown) sit near 1.0 all day; to compare blocks inside them, use `prob`.
- `prob` is the calibrated probability that at least one incident is reported
  in that cell during that hour (typically 0.05%-3%), after the blue-light factor.
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
| `score.py --build-cache` | **No, cron** | regenerate the cache daily (~5 s), e.g. just after midnight |
| notebook, `notebooks/` | No | testing ground and reports |

**Warm-up and cost (measured here):** the first call loads the model plus
`History`, which takes about 3 s and about 470 MB of RAM. After that,
`explain_cell` takes about 4 ms per cell-hour. Call `score._load()` once at
app start so the first user doesn't wait. Each gunicorn worker loads its own
copy, so use 1-2 workers with threads rather than many processes.

### Endpoints

| Endpoint | Uses | Returns |
|---|---|---|
| `GET /health` | `model_meta.json`, `metrics.json`, cache header | model trained_at, area, 2023 AUC, cache date, modelled / no-data cell counts |
| `GET /hotzones?hour=23[&date=YYYY-MM-DD]` | `risk_cache.json` (rebuild with `build_cache(date)` if the date differs, ~5 s) | for that hour: `[{cell, risk, prob, factors}]` for modelled cells + `no_data_cells`. The frontend draws them with deck.gl's `H3HexagonLayer`; no GeoJSON needed |
| `GET /score?lat=..&lng=..&hour=..[&date=]` | `h3.latlng_to_cell` → `explain_cell` | `{cell, risk, prob, status, harm_score, bluelight, history_as_of, factors:[{label, value, effect}]}`; `status` is `ok`, `no_data` or `outside` (return 400 for `outside`) |
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
- **Area:** a 5-mile circle around Tommy Trojan (`CENTER`, `RADIUS_MILES` in
  `common/config.py`). That is 1,724 H3 cells, of which **1,451 are modelled**.
- **No-data cells:** the other 273 cells in the circle are mostly policed by
  other departments: Vernon, Huntington Park, the north edge of Inglewood, and
  LA County Sheriff islands. LAPD doesn't record them, so they are `no_data`,
  not safe. LASD publishes incident data, but its historical files stop at
  2021, and Vernon / Huntington Park / Inglewood publish no incident-level
  data. So these areas are not included yet.
- **Years:** see the note above. For dates after 2023, history features are
  anchored at the end of 2023 (`LIVE_HISTORY_END`); calendar and darkness
  features use the real date.
- **Redacted locations** (lat/lon 0,0) are dropped. LAPD uses `time_occ = 1200`
  when the time is unknown, so noon is slightly over-represented (about 3%).
- **Severity** (`common/severity.py`): a 0-10 harm score per LAPD crime code,
  modelled on crime-harm indices (Cambridge CHI): homicide 10, rape 8,
  kidnapping 7, shootings / aggravated assault 6, robbery 5, sexual battery /
  brandishing 4, simple assault / theft from a person 3, burglary 2, vehicle
  crime and plain theft 1, fraud 0.25. Firearms, knives and blunt weapons
  (`weapon_used_cd` 100-399) raise a crime by 50%, capped at 6. The stored
  `tier` (violent = severity ≥ 3, property, other) is only for charts.
- **Darkness:** civil dawn/dusk for LA from `astral`.
- **Blue-light phones:** 83 USC emergency phones (University Park and Health
  Sciences campuses) in `data/bluelight_phones.csv`, taken from the USC campus
  map's "Emergency Phones" layer (category 55276). To refresh, export that
  layer's JSON from the map in a browser (see the docstring in
  `data/bluelight_from_concept3d.py`), then run
  `python -m algorithm.data.bluelight_from_concept3d data/categories-3.json`
  and retrain. Phones always **lower** risk: `dist_bluelight_m` is
  monotone-constrained in the model, and scoring multiplies the probability by
  `1 - 0.25 x exp(-distance / 150 m)` (`config.BLUELIGHT_*`). The cut is a policy
  choice, not learned, because phones sit where people already walk.
  `geo.bluelight_protection()` exposes the same 0-1 value for routing.
- **Streetlights:** TODO, not loaded yet (LA Bureau of Street Lighting data).

## Model

Rows are (cell, date, hour) for the modelled cells. Target = at least one
incident. All history features use **only data from before the row's date**:

| Group | Features |
|---|---|
| Time | hour, weekday, month, is_weekend, is_dark |
| Place | cell centroid lat/lon, distance to nearest blue-light phone |
| Per-crime weighted history | `harm_7d`, `harm_30d` (Σ severity × recency, half-life 7 / 30 days); `harm_30d_tod`, `harm_365d_tod` (also × time-of-day match, σ = 2 h) |
| Neighbours | `nb1_harm_30d`, `nb2_harm_30d`: `harm_30d` over H3 rings 1 and 2 |
| Long-run counts | same-hour ±1 over the past 12 months, long-run rate, long-run same-hour rate |
| Area | all incidents in the modelled area in the last 7 days |

Each past crime's weight is

    severity x 0.5 ** (age_days / half_life) x exp(-hour_gap**2 / (2 x sigma**2))

so a robbery last week at 10 pm counts far more for a 10 pm row than a car
theft last year at noon. With a 30-day half-life, a crime 3 months old counts
1/8. Compared with the old violent/property counts on the same 5-mile data,
AUC is the same (0.746 vs 0.747) with fewer, more explainable features.

The full grid is about 12.7M cell-hours a year, so nothing materialises it.
Training keeps every positive plus 5 sampled negatives each, then undoes the
sampling with a prior correction, so outputs are real probabilities.
Evaluation keeps every positive plus 10 negatives each, weighted by 1/sample
rate. Split by time, never at random: train 2020-04 to 2022-06, early-stop on
2022-07 to 2022-12, test on 2023. XGBoost (`hist`, depth 4, lr 0.03, early
stopping) is compared with sklearn's `GradientBoostingClassifier`.

The history features and `dist_bluelight_m` (`MONOTONE_INCREASING` in
`config.py`) carry XGBoost **monotone constraints**: more crime history can
only raise the score, and being farther from a blue-light phone can never
lower it. Without them, correlated features split credit and a feature can
end up pointing the wrong way. The notebook's monotonicity check tests this.

## Results (2023, full-grid estimate: 12.7M cell-hours, base rate 0.69%)

| Model | ROC-AUC | PR-AUC | Top-10% capture |
|---|---|---|---|
| **XGBoost** | **0.746** | **0.0298** | **36.7%** |
| sklearn GradientBoosting | 0.741 | 0.0258 | 35.4% |
| Past-year same-hour count (baseline) | 0.734 | 0.0291 | 36.0% |

On the 2024 out-of-time check, XGBoost gets AUC 0.766. Heatmap-level rank
correlation with what actually happened in 2023 is 0.72, and calibration is
within 0.17 points in every decile. All six score sanity checks in the
notebook pass. AUC is lower than the earlier 10-mile model (0.785) because a
smaller area has fewer very different places to tell apart, not because the
model got worse. Details, SHAP and calibration plots:
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
