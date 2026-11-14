"""Serving hook for the crime-risk model.

The Flask API calls `score_cell` for ad-hoc requests and serves the precomputed
cache for the heatmap.

    from scoring.score import score_cell, explain_cell
    score_cell("8929a1d4b4bffff", 22, datetime.date(2026, 11, 14))   # -> 0.0..1.0

Build the cache (every USC-area cell x 24 hours for one date):
    python -m scoring.score --build-cache --date 2026-11-14
Explain one score from the command line:
    python -m scoring.score --cell 8929a1d4b4bffff --hour 22 --date 2026-11-14

Two numbers come out of the model:
  prob  calibrated probability that >=1 incident is reported in that cell
        during that hour on that date (usually 0.1%-3%).
  risk  0..1 relative score = where `prob` falls among all USC-area
        cell-hours in the 2023 test year (0.9 = riskier than 90% of them).
        This is what the heatmap and routing should use.

History features (recent counts) use incidents strictly before `date`. Our
LAPD data ends Dec 2024, so for later dates they are anchored at the end of
the data; calendar and darkness features always use the real date.
"""
import argparse
import datetime as dt
import json
from functools import lru_cache

import h3
import numpy as np
import pandas as pd
import xgboost as xgb

from algorithm.common import config
from algorithm.features.build_dataset import History

LABELS = {
    "hour": "time of day",
    "weekday": "day of week",
    "month": "month",
    "season": "season",
    "is_weekend": "weekend",
    "is_dark": "after dark",
    "cell_lat": "location (north/south)",
    "cell_lon": "location (east/west)",
    "dist_bluelight_m": "distance to blue-light phone",
    "cell_7d": "incidents here, last 7 days",
    "cell_30d": "incidents here, last 30 days",
    "cell_90d": "incidents here, last 90 days",
    "cell_sev_90d": "severity-weighted incidents here, 90 days",
    "cell_violent_90d": "violent incidents here, 90 days",
    "cell_hourband_365d": "incidents here at this hour, past year",
    "cell_rate_all": "long-run incident rate here",
    "cell_hourband_rate_all": "long-run rate here at this hour",
    "nb1_30d": "incidents in adjacent blocks, 30 days",
    "nb2_30d": "incidents 2 blocks away, 30 days",
    "nb1_90d": "incidents in adjacent blocks, 90 days",
    "area_7d": "area-wide incidents, last 7 days",
}


@lru_cache(maxsize=1)
def _load():
    meta = json.loads(config.META_PATH.read_text())
    booster = xgb.Booster()
    booster.load_model(config.MODEL_PATH)
    return booster, meta, History()


def _prob(raw, neg_rate):
    raw = np.clip(raw, 1e-7, 1 - 1e-7)
    return raw * neg_rate / (raw * neg_rate + (1 - raw))


def _risk(prob, meta):
    qs = np.asarray(meta["risk_quantiles"])
    return np.interp(prob, qs, np.linspace(0, 1, len(qs)))


def _frame(cells, hours, date, context_features=None):
    booster, meta, hist = _load()
    missing = [c for c in cells if c not in hist.cidx]
    if missing:
        raise KeyError(f"cell(s) outside the USC model area: {missing[:3]}")
    ci = np.array([hist.cidx[c] for c in cells])
    di = np.full(len(ci), hist.day_index(date))
    X = hist.features(ci, di, np.asarray(hours), dates=[date] * len(ci))
    for k, v in (context_features or {}).items():
        if k not in X.columns:
            raise KeyError(f"unknown feature override: {k}")
        X[k] = v
    return X[meta["features"]]


def _predict(X, with_contribs=False):
    booster, meta, _ = _load()
    dm = xgb.DMatrix(X)
    prob = _prob(booster.predict(dm), meta["neg_rate"])
    risk = _risk(prob, meta)
    if not with_contribs:
        return prob, risk, None
    contribs = booster.predict(dm, pred_contribs=True)[:, :-1]
    return prob, risk, contribs


def _as_date(date):
    if date is None:
        return dt.date.today()
    if isinstance(date, dt.datetime):
        return date.date()
    if isinstance(date, str):
        return dt.date.fromisoformat(date[:10])
    return date


def score_cell(h3_id: str, hour: int, date=None, context_features: dict | None = None) -> float:
    """0..1 relative crime risk for one H3 cell (res 9) at a clock hour on a date.

    context_features: optional overrides, e.g. {"is_dark": 1}. Keys must be
    model feature names (see LABELS)."""
    X = _frame([h3_id], [int(hour) % 24], _as_date(date), context_features)
    _, risk, _ = _predict(X)
    return float(risk[0])


def explain_cell(h3_id: str, hour: int, date=None, context_features: dict | None = None,
                 top_k: int = 5) -> dict:
    """Score plus the SHAP factors behind it, for the UI and the pitch."""
    date = _as_date(date)
    X = _frame([h3_id], [int(hour) % 24], date, context_features)
    prob, risk, contribs = _predict(X, with_contribs=True)
    order = np.argsort(-np.abs(contribs[0]))[:top_k]
    _, meta, _ = _load()
    feats = meta["features"]
    return {
        "cell": h3_id, "hour": int(hour), "date": date.isoformat(),
        "risk": round(float(risk[0]), 4), "prob": round(float(prob[0]), 5),
        "history_as_of": min(date, dt.date.fromisoformat(meta["data_end"])).isoformat(),
        "factors": [{
            "feature": feats[j], "label": LABELS.get(feats[j], feats[j]),
            "value": round(float(X.iloc[0, j]), 3),
            "effect": round(float(contribs[0, j]), 4),   # + raises risk, - lowers it (log-odds)
        } for j in order],
    }


def build_cache(date=None, top_k: int = 3) -> dict:
    """Risk for every USC-area cell x 24 hours on `date`, with top factors."""
    date = _as_date(date)
    _, meta, hist = _load()
    cells = list(hist.cells)
    cc = [c for c in cells for _ in range(24)]
    hh = [h for _ in cells for h in range(24)]
    X = _frame(cc, hh, date)
    prob, risk, contribs = _predict(X, with_contribs=True)
    feats = meta["features"]
    out = {}
    for i, c in enumerate(cells):
        sl = slice(i * 24, (i + 1) * 24)
        tops = []
        for row in contribs[sl]:
            idx = np.argsort(-np.abs(row))[:top_k]
            tops.append([[feats[j], round(float(row[j]), 3)] for j in idx])
        lat, lon = h3.cell_to_latlng(c)
        out[c] = {"lat": round(lat, 6), "lon": round(lon, 6),
                  "risk": np.round(risk[sl], 4).tolist(),
                  "prob": np.round(prob[sl], 5).tolist(),
                  "top_factors": tops}
    return {
        "date": date.isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "h3_res": meta["h3_res"], "target": meta["target"],
        "history_as_of": min(date, dt.date.fromisoformat(meta["data_end"])).isoformat(),
        "risk_definition": "percentile of calibrated incident probability among "
                           "USC-area cell-hours in 2023 (0..1)",
        "feature_labels": LABELS,
        "cells": out,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--build-cache", action="store_true")
    ap.add_argument("--cell")
    ap.add_argument("--hour", type=int, default=22)
    args = ap.parse_args()
    if args.build_cache:
        cache = build_cache(args.date)
        config.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":")))
        risks = np.array([r for v in cache["cells"].values() for r in v["risk"]])
        print(f"Wrote {len(cache['cells'])} cells x 24 h to {config.CACHE_PATH} "
              f"(median risk {np.median(risks):.2f}, max {risks.max():.2f})")
    if args.cell:
        print(json.dumps(explain_cell(args.cell, args.hour, args.date), indent=2))


if __name__ == "__main__":
    main()
