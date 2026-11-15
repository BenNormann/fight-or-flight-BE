# SafeRoute crime-risk model: results

Model: XGBoost classifier on H3 res-9 cell × clock-hour × date rows, for the
**10-mile circle around USC**. Target: at least one LAPD-reported incident in
that cell during that hour. Reproduce with `python -m algorithm.models.train_xgb`.
The numbers below come from `models/artifacts/metrics.json`. For the full
walkthrough with plots, see `../explore_train_infer.ipynb`.

## Area and data

- 6,898 H3 cells lie within 10 miles. **3,432 are modelled**, meaning they had
  at least 5 LAPD incidents in 2020–2022.
- The other 3,466 cells are mostly other police jurisdictions (Inglewood,
  Culver City, Beverly Hills, Vernon, LA County Sheriff areas). They are
  reported as `no_data`, never as safe.
- 647,921 incidents, 2020–2024. Training uses 2020-04 to 2022-12: 1.76M rows,
  with 288k positives plus 5 sampled negatives per positive.

## Out-of-time evaluation

The full 2023 grid has about 30M cell-hours. Evaluation keeps every
incident cell-hour plus 10 sampled negatives each, and weights the negatives
by 1/sample rate. Every metric below is therefore a **full-grid estimate**.

### 2023 test year (30.1M cell-hours, 142,201 with an incident, base rate 0.47%)

| Model | ROC-AUC | PR-AUC | Brier | Top-10% capture* |
|---|---|---|---|---|
| **XGBoost** | **0.786** | **0.0273** | 0.0047 | **41.5%** |
| sklearn GradientBoostingClassifier | 0.783 | 0.0235 | 0.0047 | 40.9% |
| Baseline: past-12-month count in cell at this hour ±1 | 0.772 | 0.0261 | n/a | 40.4% |

\* The share of all incident cell-hours that fall in the 10% of cell-hours the
model ranks riskiest.

Heatmap-level check: summing predictions per (cell, hour of day) over 2023 and
comparing them with observed counts gives a Spearman rank correlation of **0.72**.

### 2024 extra check (30.1M cell-hours, base rate 0.25%)

| Model | ROC-AUC | PR-AUC | Top-10% capture |
|---|---|---|---|
| **XGBoost** | **0.803** | **0.0202** | **47.0%** |
| sklearn GradientBoostingClassifier | 0.796 | 0.0145 | 45.4% |
| Baseline | 0.787 | 0.0194 | 44.7% |

In 2024 the model over-predicts on average (0.38% predicted vs 0.25%
observed). This is expected: the 2024 drop is LAPD's NIBRS reporting change,
not less crime.

## What changed vs the USC-only model

- AUC rose from 0.69 to 0.79, because the area now runs from quiet residential
  blocks to downtown and Hollywood. Ranking across very different places is
  easier than ranking within a few blocks of campus.
- XGBoost still beats sklearn's GradientBoosting and the past-year baseline on
  every metric.
- Calibration on 2023 stays close to the diagonal across all deciles. For
  example, a predicted 0.04% came out as 0.03% observed, and a predicted 2.01%
  came out as 1.85% observed.

## Feature importance (mean |SHAP|, log-odds, 2023 grid-weighted sample)

![SHAP summary](shap_summary.png)

1. `cell_rate_all`: the cell's long-run incident rate
2. `hour`: time of day
3. `cell_90d`: the cell's count over the last 90 days
4. `cell_hourband_rate_all`: the cell's long-run rate at this hour ±1
5. `cell_30d`, `cell_hourband_365d`, `cell_7d`: recent counts

## Caveats

- **Saturation:** `risk` is a percentile over the whole 10-mile area, so busy
  areas such as downtown, Hollywood and Koreatown sit at or near 1.0 all day.
  To compare blocks inside a busy area, use `prob`, or a percentile
  computed only over the route's neighbourhood.
- Blue-light phone coordinates are still **placeholders**, so ignore
  `dist_bluelight_m` until the real ones are loaded.

![Calibration](calibration.png)
