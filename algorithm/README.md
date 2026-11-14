# fight-or-flight-BE

Backend for SafeRoute: crime-aware walking routes around USC. This branch
(`algorithm-init`) holds the **crime-risk model**: a gradient-boosted
(XGBoost) classifier that scores every H3 cell (res 9, ~175 m) for every hour,
plus the `score_cell` hook the Flask API will call.

```
common/      config (bbox, dates, paths), severity tiers, geo helpers (H3, darkness, blue-light phones)
data/        ingest_crime.py -> data/crime.db (SQLite, git-ignored)
features/    build_dataset.py -> cell x hour x date training frame
models/      train_xgb.py -> artifacts/xgb_risk.ubj + model_meta.json + metrics.json
scoring/     score.py (score_cell / explain_cell / build_cache) and cache/risk_cache.json
notebooks/   model_report.md + SHAP and calibration plots
```

## Setup

Python 3.10+.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Train (run from the repo root)

```bash
python -m data.ingest_crime        # ~37k LAPD incidents, 2020-2024, USC bbox -> data/crime.db (~1 min)
python -m models.train_xgb         # builds features, trains, evaluates, saves model + plots (~1 min)
python -m scoring.score --build-cache --date 2026-11-14   # risk for every cell x 24 h -> scoring/cache/risk_cache.json
```

Options: `python -m models.train_xgb --target violent` trains on violent
incidents only, and `--skip-sklearn` skips the sklearn comparison model.
`python -m features.build_dataset` writes the sampled training frame to
`models/artifacts/dataset.parquet` if you want to inspect it.

## Use from the API

```python
from scoring.score import score_cell, explain_cell

score_cell("8929a1d66b3ffff", hour=22, date="2026-11-14")        # -> 0.89
score_cell("8929a1d66b3ffff", 14, "2026-11-14", {"is_dark": 1})  # override any feature
explain_cell("8929a1d66b3ffff", 22, "2026-11-14")                # risk, prob, top SHAP factors
```

- `risk` (what `score_cell` returns) is 0..1 and **relative**: the percentile of
  this cell-hour's predicted probability among all USC-area cell-hours in 2023.
  Use it for the heatmap colour and the routing penalty.
- `prob` is the calibrated probability that at least one incident is reported
  in that cell during that hour (typically 0.1%-3%).
- For the heatmap, serve `scoring/cache/risk_cache.json`: per cell it has
  `lat`, `lon`, and 24-length `risk`, `prob` and `top_factors` lists, indexed by hour.

## Data provenance

- **Source:** LA City Open Data, "Crime Data from 2020 to Present" (LAPD),
  Socrata dataset `2nrs-mtv8`, https://data.lacity.org/resource/2nrs-mtv8.json.
  Fields kept: `dr_no, date_occ, time_occ, lat, lon, crm_cd, crm_cd_desc, weapon_used_cd, part_1_2`.
- **Area:** lat 34.005-34.040, lng -118.305 to -118.265 (Exposition Park north
  to Adams, Vermont east to Figueroa). Change it in `common/config.py`.
- **Years:** that dataset ends in December 2024 and has no 2025 rows. In
  March 2024 LAPD moved to NIBRS reporting, and 2024 counts in this dataset drop
  to about half. So we **train on 2020-2022, test on 2023, and use 2024 only as
  an extra out-of-time check**. For dates after 2024, history features are
  anchored at the last available day (Dec 2024); calendar and darkness use the real date.
- **Redacted locations** (lat/lon 0,0) are dropped. LAPD uses `time_occ = 1200`
  when the time is unknown, so noon is slightly over-represented.
- **Severity tiers** (`common/severity.py`): keyword lookup into violent
  (robbery, assault, battery, homicide, sex crimes, ...), property, other.
- **Darkness:** civil dawn/dusk for LA from `astral`.
- **Blue-light phones:** TODO. `common/geo.py` uses approximate campus-landmark
  points as **placeholders**. Put the real USC DPS phone coordinates in
  `data/bluelight_phones.csv` (`lat,lon,name`), then retrain.
- **Streetlights:** TODO, not loaded yet (LA Bureau of Street Lighting data).

## Model

Rows are (cell, date, hour) for the 116 area cells. Target = at least one incident.
Features (all history features use **only data from before the row's date**):

| Group | Features |
|---|---|
| Time | hour, weekday, month, season, is_weekend, is_dark |
| Place | cell centroid lat/lon, distance to nearest blue-light phone |
| Cell history | counts over the last 7/30/90 days, severity-weighted 90d, violent 90d, same-hour ±1 past year, long-run rate, long-run same-hour rate |
| Neighbours | H3 ring-1 30d and 90d counts, ring-2 30d count |
| Area | all incidents in the bbox last 7 days |

Training keeps every positive plus 15 sampled negatives each, then undoes the
sampling with a prior correction, so the outputs are real probabilities.
Split by time, never at random: train 2020-04 to 2022-06, early-stop on 2022-07
to 2022-12, test on the full 2023 grid. XGBoost (`hist`, depth 4, lr 0.03,
early stopping) is compared with sklearn's `GradientBoostingClassifier`.

## Results (full 2023 grid, 1.0M cell-hours, base rate 0.73%)

| Model | ROC-AUC | PR-AUC | Top-10% capture |
|---|---|---|---|
| **XGBoost** | **0.686** | 0.0169 | **27.9%** |
| sklearn GradientBoosting | 0.669 | 0.0139 | 24.0% |
| Past-year same-hour count (baseline) | 0.674 | 0.0175 | 27.1% |

On the 2024 out-of-time check XGBoost gets AUC 0.722. Heatmap-level rank
correlation with what actually happened in 2023 is 0.69, and calibration is
close to perfect. Details, SHAP and calibration plots: [notebooks/model_report.md](notebooks/model_report.md).

**Where this goes next:** spatio-temporal deep models such as ST-SHN and
DeepCrime learn cross-region and cross-category dependencies and are the
natural scale-up once there is more data and more signals. For a hackathon,
gradient boosting trains in under a minute, and every score can be explained.

## Bias and responsible use

LAPD data records what gets **reported and policed**, not where harm actually
happens. Areas with more patrols produce more records, and some crimes are
reported far less than others. That bias flows straight into this model. So:

- Every score ships with its SHAP factors (`explain_cell`, `top_factors` in
  the cache). The UI should show *why* a block scores high, not just a colour.
- Use risk as a **cost on route segments**, not a hard block. Do not silently
  route users around whole neighbourhoods.
- Cell lat/lon is a feature, so the model partly learns "where". Watch for it
  turning into a proxy for neighbourhood demographics, and consider dropping
  it if the SHAP values are dominated by location.
