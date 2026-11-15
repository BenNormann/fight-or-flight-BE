# Model guide: train, run, and call the crime-risk model

A practical guide to the SafeRoute crime-risk model: how to train it, how to
run it, and exactly what it expects at inference time. For the design and
results, see [README.md](README.md) and [notebooks/model_report.md](notebooks/model_report.md).

All commands run from the **repo root** (the folder that contains `algorithm/`).

---

## 1. Setup

Python 3.10 or newer.

```bash
python -m venv .venv
.venv\Scripts\activate             # macOS/Linux: source .venv/bin/activate
pip install -r algorithm/requirements.txt
```

`shap` is only used for the beeswarm plot. If it won't install on your Python
version, skip it: training, scoring and the notebook still work without it.

---

## 2. Train

| Step | Command | Time | Output |
|---|---|---|---|
| 1. Pull LAPD data | `python -m algorithm.data.ingest_crime` | ~1 min | `algorithm/data/crime.db` (~400k incidents, 2020–2024, 5 mi of USC) |
| 2. *(optional)* Refresh blue-light phones | `python -m algorithm.data.bluelight_from_concept3d algorithm/data/categories-3.json` | seconds | `algorithm/data/bluelight_phones.csv` |
| 3. Train + evaluate + save | `python -m algorithm.models.train_xgb` | ~1–2 min, ~1.5 GB RAM | `models/artifacts/xgb_risk.ubj`, `model_meta.json`, `metrics.json`, plots in `notebooks/` |
| 4. Build the heatmap cache | `python -m algorithm.scoring.score --build-cache --date 2026-11-14` | ~5 s | `scoring/cache/risk_cache.json` |
| 5. *(optional)* Notebook | open `algorithm/explore_train_infer.ipynb`, Restart & Run All | ~2 min | exploration, evaluation, sanity checks |

Training options:
- `--target violent` trains on violent incidents only (severity ≥ 3).
- `--skip-sklearn` skips the sklearn comparison model (saves ~40 s).

Step 1 is the only one that needs the network. Rerun steps 3–4 whenever you
change the data, a severity score, a half-life, or the blue-light settings.
Then restart the API so it loads the new files.

### What training does

1. **Load history** (`features/build_dataset.py → History`). Read `crime.db`,
   give every incident a severity score (`common/severity.py`), and decide which
   cells are modelled (≥ 5 incidents in 2020–2022) and which are no-data.
2. **Build the frame.** One row per (cell, date, hour) that had an incident, plus
   5 random no-incident rows each. History features use only crimes **before**
   the row's date.
3. **Split by time.** Train on 2020-04 → 2022-06, early-stop on 2022-07 → 2022-12,
   test on 2023, and run an extra check on 2024.
4. **Fit XGBoost** (depth 4, learning rate 0.03, early stopping 150 rounds) with
   monotone constraints. Fit sklearn's `GradientBoostingClassifier` for comparison.
5. **Evaluate** with grid weights, so the metrics equal what the full ~12.7M
   cell-hour grid would give. Save the model, its metadata, metrics and plots.

### Current results (2023 test year, base rate 0.69%)

| Model | ROC-AUC | PR-AUC | Top-10% capture |
|---|---|---|---|
| **XGBoost** | **0.746** | **0.0298** | **36.7%** |
| sklearn GradientBoosting | 0.741 | 0.0258 | 35.4% |
| Baseline (past-year count at this hour) | 0.734 | 0.0291 | 36.0% |

The 2024 check gives AUC 0.766. Calibration is within 0.17 points in every decile.

---

## 3. Run the model

### From Python (what the Flask API uses)

```python
import h3
from algorithm.scoring.score import score_cell, explain_cell, cell_status, build_cache

cell = h3.latlng_to_cell(34.0254, -118.2851, 9)       # lat, lng -> H3 res-9 cell
score_cell(cell, 22, "2026-11-14")                     # -> 0.72  (0..1 risk, None if no data)
explain_cell(cell, 22, "2026-11-14")                   # -> dict with risk, prob, factors, ...
cell_status(cell)                                      # -> "ok" | "no_data" | "outside"
cache = build_cache("2026-11-14")                      # every modelled cell x 24 hours
```

The first call loads the model and history: about 3 s and ~470 MB of RAM, once
per process. After that, a live `explain_cell` call takes about 4 ms. Call
`algorithm.scoring.score._load()` at app start so the first user doesn't wait.

### From the command line

```bash
python -m algorithm.scoring.score --cell 8929a1d66abffff --hour 22 --date 2026-11-14   # prints explain_cell JSON
python -m algorithm.scoring.score --build-cache --date 2026-11-14                      # writes risk_cache.json
```

---

## 4. Inference parameters

### `score_cell(h3_id, hour, date=None, context_features=None) -> float | None`

| Parameter | Type | Meaning | Valid values |
|---|---|---|---|
| `h3_id` | `str` | H3 cell at **resolution 9** (`config.H3_RES`). Get it with `h3.latlng_to_cell(lat, lng, 9)`. | A cell inside the 5-mile circle around (34.0224, −118.2851) |
| `hour` | `int` | Clock hour in **Los Angeles local time**. | 0–23 (taken modulo 24) |
| `date` | `datetime.date`, `datetime.datetime`, `"YYYY-MM-DD"` or `None` | Day to score. `None` means today. Sets weekday, month and darkness. History features use crimes before this date, capped at 2023-12-31 (see below). | Any date |
| `context_features` | `dict[str, float]` or `None` | Overrides for named model features, for example `{"is_dark": 1}` or `{"dist_bluelight_m": 50}`. Keys must be feature names (table below), or it raises `KeyError`. | Any value in the feature's range |

**Returns** the relative risk from 0 to 1: the percentile of this cell-hour's
probability among all modelled cell-hours in 2023 (0.9 means riskier than 90%
of them). It returns `None` for a **no-data** cell (inside the circle but not
policed by LAPD), which means unknown, not safe. It raises `KeyError` for a
cell **outside** the circle.

### `explain_cell(h3_id, hour, date=None, context_features=None, top_k=5) -> dict`

Same inputs, plus `top_k`, the number of SHAP factors to return. Output:

```json
{
  "cell": "8929a1d66abffff", "hour": 22, "date": "2026-11-14", "status": "ok",
  "risk": 0.5995,            // 0..1 percentile (what score_cell returns)
  "prob": 0.00625,           // calibrated P(>=1 incident this cell-hour), after the blue-light factor
  "harm_score": 0.471,       // per-crime weighted sum: severity x recency x time-of-day (harm_30d_tod)
  "bluelight": {"dist_m": 1043, "protection": 0.001, "factor": 1.0},
  "history_as_of": "2023-12-31",
  "factors": [{"feature": "hour", "label": "time of day", "value": 22.0, "effect": 0.2086}, ...]
}
```

`effect` is the SHAP contribution in log-odds: positive raises risk, negative
lowers it. For a no-data or outside cell, `risk` and `prob` are `null`, and
`status` and `note` explain why.

### `build_cache(date=None, top_k=3) -> dict`

Scores every modelled cell for all 24 hours. The result looks like
`cells[h3_id] = {lat, lon, bluelight_dist_m, bluelight_protection, risk[24], prob[24], top_factors[24]}`,
plus `features`, `feature_labels`, `no_data_cells`, `date` and `history_as_of`.
Each entry in `top_factors[h]` is a `[feature_index, effect]` pair, and the index
points into `features`.

### The 18 model features

`score.py` builds these for you. You only need them for `context_features`, or
if you call the booster directly. In that case the column order is
`model_meta.json["features"]`.

| Feature | Unit / range | What it is |
|---|---|---|
| `hour` | 0–23 | clock hour (LA time) |
| `weekday` | 0–6 | 0 = Monday |
| `month` | 1–12 | calendar month |
| `is_weekend` | 0/1 | Saturday or Sunday |
| `is_dark` | 0/1 | the hour's midpoint is before civil dawn or after civil dusk |
| `cell_lat`, `cell_lon` | degrees | cell centre |
| `dist_bluelight_m` | metres, ≥ 0 | distance to the nearest of the 83 USC blue-light phones |
| `harm_7d` | ≥ 0 | Σ severity × 0.5^(age/7 d), this cell |
| `harm_30d` | ≥ 0 | Σ severity × 0.5^(age/30 d), this cell |
| `harm_30d_tod` | ≥ 0 | as `harm_30d`, × time-of-day weight exp(−Δh²/8) |
| `harm_365d_tod` | ≥ 0 | Σ severity × 0.5^(age/365 d) × time-of-day weight |
| `nb1_harm_30d` | ≥ 0 | `harm_30d` summed over the 6 adjacent cells |
| `nb2_harm_30d` | ≥ 0 | `harm_30d` summed over the 12 cells two rings out |
| `cell_hourband_365d` | count | incidents here at this hour ±1, previous 12 months |
| `cell_rate_all` | incidents/year | long-run incident rate here |
| `cell_hourband_rate_all` | incidents/year | long-run rate here at this hour ±1 |
| `area_7d` | count | incidents in the whole area, previous 7 days |

### Calling the booster directly (not recommended)

`prob = p·r / (p·r + 1 − p) × bluelight_factor(dist_bluelight_m)`. Here `p` is
`booster.predict(DMatrix(X))`, `r` is `model_meta.json["neg_rate"]`, and
`bluelight_factor(d) = 1 − 0.25·exp(−d / 150)`. Risk is then
`np.interp(prob, model_meta["risk_quantiles"], np.linspace(0, 1, 101))`.
`score.py` does exactly this, so use it instead.

### Dates after 2023

LAPD's dataset ends in December 2024, and its counts halve from March 2024
(the NIBRS switch). For any date after 2023, history features stop at
**2023-12-31** (`config.LIVE_HISTORY_END`). Calendar and darkness features
still use the real date, and `history_as_of` in the output tells you which
cutoff was used.

---

## 5. Settings you can tune (`common/config.py`, `common/severity.py`)

| Setting | Default | Effect |
|---|---|---|
| `RADIUS_MILES` | 5.0 | size of the area. Rerun ingest, training and the cache |
| `HALF_LIFE_DAYS` | `{"7d": 7, "30d": 30, "365d": 365}` | how fast a crime's weight fades. Keep the keys: they name the features |
| `TOD_SIGMA_HOURS` | 2.0 | width of the time-of-day match (2 h away counts 0.61, 4 h 0.14) |
| `SEVERITY_BY_CODE`, `WEAPON_MULTIPLIER` | see `severity.py` | per-crime harm score (homicide 10 … fraud 0.25); weapon ×1.5, capped at 6 |
| `BLUELIGHT_MAX_REDUCTION` | 0.25 | how much a phone right next to a cell cuts its probability |
| `BLUELIGHT_RANGE_M` | 150 | distance scale of that effect (~13% cut at 100 m, ~0 beyond 600 m) |
| `MONOTONE_INCREASING` | history features + `dist_bluelight_m` | features that may only raise risk as they grow |
| `MIN_CELL_INCIDENTS` | 5 | cells with fewer incidents in 2020–2022 are no-data |
| `LIVE_HISTORY_END` | 2023-12-31 | where history stops for live dates |

After changing any of these, retrain (step 3) and rebuild the cache (step 4).
`geo.bluelight_protection(dist_m)` (0–1) and `geo.bluelight_factor(dist_m)`
are plain functions, so a route planner can reuse the same blue-light effect
on street edges.
