"""Train, evaluate and save the cell-hour crime-risk model.

Usage (from the repo root, after algorithm.data.ingest_crime):
    python -m algorithm.models.train_xgb                    # any reported incident
    python -m algorithm.models.train_xgb --target violent   # violent incidents only
    python -m algorithm.models.train_xgb --skip-sklearn     # skip the slower sklearn baseline

Split is by time, never random:
    train  2020-04 .. 2022-06   (first 90 days are feature warm-up)
    valid  2022-07 .. 2022-12   (early stopping only)
    test   2023                 (all positives + weighted negative sample = full grid in expectation)
    extra  2024                 (out-of-time check; LAPD reporting changed in 2024)

Outputs (models/artifacts/): xgb_risk.ubj, model_meta.json, metrics.json,
and plots in notebooks/ (calibration.png, shap_summary.png, shap_importance.png).
"""
import argparse
import datetime as dt
import json
import time

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from algorithm.common import config, geo
from algorithm.features.build_dataset import FEATURES, History


def prior_correct(p, neg_rate):
    """Undo negative down-sampling: odds_true = odds_sampled * neg_rate."""
    p = np.clip(p, 1e-7, 1 - 1e-7)
    return p * neg_rate / (p * neg_rate + (1 - p))


def monotone_constraints():
    """+1 for crime-history features and blue-light distance
    (config.MONOTONE_INCREASING), 0 otherwise."""
    return "(" + ",".join("1" if f in config.MONOTONE_INCREASING else "0" for f in FEATURES) + ")"


def weighted_quantiles(x, w, qs):
    o = np.argsort(x)
    cw = np.cumsum(w[o]) / w.sum()
    return np.interp(qs, cw, x[o])


def calibration_bins(y, p, w, n_bins=10):
    """Weighted reliability curve with equal-weight (quantile) bins."""
    edges = weighted_quantiles(p, w, np.linspace(0, 1, n_bins + 1))
    b = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, n_bins - 1)
    mean_pred = [float(np.average(p[b == i], weights=w[b == i])) for i in range(n_bins) if (b == i).any()]
    observed = [float(np.average(y[b == i], weights=w[b == i])) for i in range(n_bins) if (b == i).any()]
    return mean_pred, observed


def evaluate(y, p, name, w=None, probabilistic=True):
    """Metrics on an evaluation frame. `w` are the frame's sample weights, so
    the numbers equal what the full cell x hour x day grid would give."""
    w = np.ones(len(y)) if w is None else np.asarray(w, dtype=float)
    o = np.argsort(-p)
    cw = np.cumsum(w[o])
    top10 = o[: np.searchsorted(cw, 0.10 * cw[-1]) + 1]
    m = {
        "rows": int(len(y)),
        "grid_rows": float(w.sum()),
        "base_rate": float(np.average(y, weights=w)),
        "auc": float(roc_auc_score(y, p, sample_weight=w)),
        "pr_auc": float(average_precision_score(y, p, sample_weight=w)),
        # share of all incident cell-hours that land in the riskiest 10% of cell-hours
        "top10pct_capture": float((y[top10] * w[top10]).sum() / max(1, (y * w).sum())),
    }
    if probabilistic:
        m["brier"] = float(brier_score_loss(y, p, sample_weight=w))
        m["mean_pred"] = float(np.average(p, weights=w))
    print(f"  {name:<28} AUC {m['auc']:.3f}  PR-AUC {m['pr_auc']:.4f}  "
          f"(base {m['base_rate']:.4f})  top-10% capture {m['top10pct_capture']:.1%}")
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["any", "violent"], default="any")
    ap.add_argument("--skip-sklearn", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    plt.switch_backend("Agg")  # plots go to files when run as a script

    print("Loading history from", config.DB_PATH)
    hist = History()
    frame, neg_rate, base_rate = hist.training_frame(kind=args.target)
    vday = hist.day_index(dt.date.fromisoformat(config.VALID_START))
    tr, va = frame[frame["day"] < vday], frame[frame["day"] >= vday]
    print(f"{len(hist.cells):,} covered cells ({len(hist.no_data_cells):,} in the circle have no LAPD data)")
    print(f"Train {len(tr):,} rows ({int(tr.y.sum()):,} pos), valid {len(va):,} rows; "
          f"neg sample rate {neg_rate:.4f}")

    # ---- XGBoost ------------------------------------------------------------
    model = xgb.XGBClassifier(
        n_estimators=2000, learning_rate=0.03, max_depth=4, min_child_weight=20,
        subsample=0.8, colsample_bytree=0.8, reg_lambda=1.0, tree_method="hist",
        eval_metric="logloss", early_stopping_rounds=150, random_state=config.SEED,
        n_jobs=-1, monotone_constraints=monotone_constraints(),
    )
    model.fit(tr[FEATURES], tr["y"], eval_set=[(va[FEATURES], va["y"])], verbose=False)
    print(f"XGBoost best iteration {model.best_iteration} ({time.time() - t0:.0f}s)")

    # ---- sklearn GradientBoostingClassifier (comparison) ----------------------
    gbc = None
    if not args.skip_sklearn:
        t1 = time.time()
        sub = tr.sample(n=min(len(tr), 80_000), random_state=config.SEED)
        gbc = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.1,
                                         subsample=0.8, random_state=config.SEED)
        gbc.fit(sub[FEATURES], sub["y"])
        print(f"sklearn GBC trained on {len(sub):,} rows ({time.time() - t1:.0f}s)")

    # ---- Evaluate out of time (weighted samples = full grid in expectation) ----
    metrics = {"target": args.target, "neg_rate": neg_rate, "train_base_rate": base_rate,
               "best_iteration": int(model.best_iteration)}
    test_probs = None
    for split, (a, b) in {"test_2023": (config.TEST_START, config.TEST_END),
                          "extra_2024": (config.EXTRA_START, config.EXTRA_END)}.items():
        g, _, _ = hist.eval_frame(a, b, kind=args.target)
        y, w = g["y"].to_numpy(), g["w"].to_numpy()
        print(f"{split}: {w.sum():,.0f} cell-hours on the grid, {int(y.sum()):,} with an incident "
              f"(scored {len(g):,} rows)")
        p = prior_correct(model.predict_proba(g[FEATURES])[:, 1], neg_rate)
        res = {"xgboost": evaluate(y, p, "XGBoost", w)}
        # What the API serves: model probability x blue-light factor.
        p_served = p * geo.bluelight_factor(g["dist_bluelight_m"].to_numpy())
        res["xgboost_served"] = evaluate(y, p_served, "XGBoost + blue-light factor", w)
        if gbc is not None:
            pg = prior_correct(gbc.predict_proba(g[FEATURES])[:, 1], neg_rate)
            res["sklearn_gbc"] = evaluate(y, pg, "sklearn GradientBoosting", w)
        # Naive baseline: rank by last-12-month count in that cell at that hour +/-1
        res["baseline_hourband"] = evaluate(
            y, g["cell_hourband_365d"].to_numpy() + 1e-3 * g["cell_rate_all"].to_numpy(),
            "Baseline (past-year count)", w, probabilistic=False)
        # Heatmap-level check: does the model rank (cell, hour-of-day) slots the
        # way the year actually played out? Spearman of mean predicted risk vs
        # observed incident count per cell x hour over the whole split.
        slot = pd.DataFrame({"c": g["cell_idx"], "h": g["hour"], "pw": p * w, "y": y})
        slot = slot.groupby(["c", "h"]).agg(p=("pw", "sum"), y=("y", "sum"))
        res["xgboost"]["heatmap_spearman"] = float(slot["p"].corr(slot["y"], method="spearman"))
        print(f"  heatmap rank correlation (cell x hour-of-day, Spearman): "
              f"{res['xgboost']['heatmap_spearman']:.3f}")
        mean_pred, observed = calibration_bins(y, p, w)
        res["xgboost"]["calibration"] = {"mean_pred": mean_pred, "observed": observed}
        metrics[split] = res
        if split == "test_2023":
            test_probs, test_w, test_frame = p_served, w, g

    # ---- Save model + metadata ------------------------------------------------
    config.ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    model.save_model(config.MODEL_PATH)
    # Reference distribution so a raw probability can be turned into a 0-1
    # *relative* risk (percentile among all covered cell-hours in 2023), using
    # the served probability (with the blue-light factor) so risk is consistent.
    qs = weighted_quantiles(test_probs, test_w, np.linspace(0, 1, 101)).tolist()
    meta = {
        "features": FEATURES, "target": args.target, "neg_rate": neg_rate,
        "best_iteration": int(model.best_iteration), "h3_res": config.H3_RES,
        "center": config.CENTER, "radius_miles": config.RADIUS_MILES,
        "covered_cells": len(hist.cells), "no_data_cells": len(hist.no_data_cells),
        "data_start": config.DATA_START, "data_end": config.DATA_END,
        "train_end": config.TRAIN_END, "risk_quantiles": qs,
        "half_life_days": config.HALF_LIFE_DAYS, "tod_sigma_hours": config.TOD_SIGMA_HOURS,
        "bluelight": {"max_reduction": config.BLUELIGHT_MAX_REDUCTION,
                      "range_m": config.BLUELIGHT_RANGE_M},
        "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    config.META_PATH.write_text(json.dumps(meta, indent=2))
    (config.ARTIFACT_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print("Saved", config.MODEL_PATH, "and", config.META_PATH)

    # ---- Plots: calibration + SHAP --------------------------------------------
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    cal = metrics["test_2023"]["xgboost"]["calibration"]
    fig, ax = plt.subplots(figsize=(5, 5))
    hi = max(max(cal["mean_pred"]), max(cal["observed"])) * 1.1
    ax.plot([0, hi], [0, hi], ls="--", c="gray", lw=1, label="perfect")
    ax.plot(cal["mean_pred"], cal["observed"], marker="o", label="XGBoost (2023)")
    ax.set_xlabel("Predicted probability"); ax.set_ylabel("Observed frequency")
    ax.set_title("Calibration, 2023 (weighted to full grid)"); ax.legend()
    fig.tight_layout(); fig.savefig(config.REPORT_DIR / "calibration.png", dpi=130); plt.close(fig)

    # Weight by `w` so the SHAP sample looks like the real grid, not the
    # positive-enriched evaluation frame.
    sample = test_frame.sample(n=min(5000, len(test_frame)), weights="w",
                               random_state=config.SEED)[FEATURES]
    booster = model.get_booster()
    contribs = booster.predict(xgb.DMatrix(sample), pred_contribs=True)[:, :-1]
    mean_abs = pd.Series(np.abs(contribs).mean(axis=0), index=FEATURES).sort_values()
    metrics["shap_mean_abs"] = mean_abs.sort_values(ascending=False).round(4).to_dict()
    (config.ARTIFACT_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))
    try:
        import shap
        shap.summary_plot(contribs, sample, show=False, max_display=15)
        plt.title("SHAP values (log-odds), 2023 sample"); plt.tight_layout()
        plt.savefig(config.REPORT_DIR / "shap_summary.png", dpi=130, bbox_inches="tight"); plt.close()
    except Exception as e:  # shap is only needed for the pretty beeswarm
        print("shap beeswarm skipped:", e)
    fig, ax = plt.subplots(figsize=(6, 5))
    mean_abs.plot.barh(ax=ax, color="#3b6ea5")
    ax.set_xlabel("mean |SHAP| (log-odds)"); ax.set_title("Feature importance (SHAP)")
    fig.tight_layout(); fig.savefig(config.REPORT_DIR / "shap_importance.png", dpi=130); plt.close(fig)
    print(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
