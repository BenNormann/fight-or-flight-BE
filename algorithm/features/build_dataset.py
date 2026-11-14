"""Build the cell x hour x date training frame for the risk model.

Usage (from the repo root, after data.ingest_crime):
    python -m features.build_dataset            # writes models/artifacts/dataset.parquet

Each row is one (H3 cell, date, hour). Target = 1 if at least one incident was
reported in that cell during that clock hour on that date.

Leakage rule: every history feature for a row on date D uses ONLY incidents
from dates strictly before D. Nothing from the row's own day leaks in.

The training years (2020-2022) keep every positive plus NEG_PER_POS randomly
sampled negatives; `neg_rate` is stored so the model can undo the sampling
(prior correction) and output real probabilities. Test years are scored on the
full grid in models/train_xgb.py via `frame_for_range`.
"""
import datetime as dt
import sqlite3

from algorithm.common import config
import h3
import numpy as np
import pandas as pd

from algorithm.common import geo

FEATURES = [
    "hour", "weekday", "month", "season", "is_weekend", "is_dark",
    "cell_lat", "cell_lon", "dist_bluelight_m",
    "cell_7d", "cell_30d", "cell_90d", "cell_sev_90d", "cell_violent_90d",
    "cell_hourband_365d", "cell_rate_all", "cell_hourband_rate_all",
    "nb1_30d", "nb2_30d", "nb1_90d",
    "area_7d",
]


class History:
    """Daily / hourly incident counts per cell, with prefix sums for fast,
    leakage-free window lookups. Day index 0 = config.DATA_START."""

    def __init__(self, db_path=config.DB_PATH):
        con = sqlite3.connect(db_path)
        inc = pd.read_sql("SELECT date_occ, hour, cell, tier FROM incidents", con)
        con.close()

        self.start = dt.date.fromisoformat(config.DATA_START)
        self.end = dt.date.fromisoformat(config.DATA_END)
        self.n_days = (self.end - self.start).days + 1

        # Rows are area cells; counting also uses partial edge cells that
        # incidents fell into so neighbour counts near the bbox edge are fuller.
        self.cells = geo.area_cells()
        extra = sorted(set(inc["cell"]) - set(self.cells))
        self.universe = self.cells + extra
        self.cidx = {c: i for i, c in enumerate(self.universe)}
        nu = len(self.universe)

        inc["d"] = (pd.to_datetime(inc["date_occ"]).dt.date - self.start).map(lambda x: x.days)
        inc = inc[(inc["d"] >= 0) & (inc["d"] < self.n_days)]
        ci = inc["cell"].map(self.cidx).to_numpy()
        di = inc["d"].to_numpy()
        hi = inc["hour"].to_numpy()
        w = inc["tier"].map(config.SEVERITY_WEIGHT).to_numpy()
        viol = (inc["tier"] == "violent").to_numpy().astype(np.int32)

        cube = np.zeros((nu, self.n_days, 24), dtype=np.int32)
        np.add.at(cube, (ci, di, hi), 1)
        self.cube = cube                       # [cell, day, hour] incident counts
        self.violent_cube = np.zeros_like(cube)
        np.add.at(self.violent_cube, (ci, di, hi), viol)
        daily = cube.sum(axis=2)
        sev = np.zeros((nu, self.n_days))
        np.add.at(sev, (ci, di), w)
        violent_daily = self.violent_cube.sum(axis=2)

        def prefix(a):  # P[:, d] = sum of a over days < d
            z = np.zeros(a.shape[:1] + (1,) + a.shape[2:], dtype=a.dtype)
            return np.concatenate([z, np.cumsum(a, axis=1)], axis=1)

        self.P_daily = prefix(daily)
        self.P_sev = prefix(sev)
        self.P_viol = prefix(violent_daily)
        self.P_hour = prefix(cube)             # [cell, day+1, hour]
        self.P_area = np.concatenate([[0], np.cumsum(daily.sum(axis=0))])

        # Neighbour matrices: ring 1 (6 cells) and ring 2 (12 cells), self excluded.
        self.R1 = np.zeros((nu, nu), dtype=np.float32)
        self.R2 = np.zeros((nu, nu), dtype=np.float32)
        for c, i in self.cidx.items():
            d1 = set(h3.grid_disk(c, 1)) - {c}
            d2 = set(h3.grid_disk(c, 2)) - d1 - {c}
            for n in d1:
                if n in self.cidx:
                    self.R1[i, self.cidx[n]] = 1
            for n in d2:
                if n in self.cidx:
                    self.R2[i, self.cidx[n]] = 1

        cent = [h3.cell_to_latlng(c) for c in self.universe]
        self.cell_lat = np.array([p[0] for p in cent])
        self.cell_lon = np.array([p[1] for p in cent])
        self.dist_bl = np.array([geo.dist_to_bluelight_m(a, b) for a, b in cent])

    # -- helpers -------------------------------------------------------------
    def day_index(self, date: dt.date) -> int:
        return (date - self.start).days

    def _win(self, P, ci, di, w):
        """Sum over days [d-w, d) using prefix array P (leakage-free)."""
        d = np.clip(di, 0, self.n_days)
        lo = np.clip(d - w, 0, None)
        return P[ci, d] - P[ci, lo]

    def features(self, ci, di, hi, dates=None) -> pd.DataFrame:
        """Feature frame for index arrays (cell idx, day idx, hour).

        `di` may point past the end of the data (live scoring); history windows
        are clipped to what exists. `dates` (array of datetime.date) overrides
        the calendar features when the history day differs from the real day.
        """
        ci, di, hi = np.asarray(ci), np.asarray(di), np.asarray(hi)
        if dates is None:
            base = np.datetime64(self.start)
            dates = (base + di.astype("timedelta64[D]")).astype(object)
        dates = pd.to_datetime(pd.Series(dates))
        weekday = dates.dt.day_of_week.to_numpy()
        month = dates.dt.month.to_numpy()

        # is_dark per unique date (astral is the slow bit)
        uniq = {d: d for d in dates.dt.date.unique()}
        dark_tab = {d: np.array([geo.is_dark(d, h) for h in range(24)]) for d in uniq}
        is_dark = np.array([dark_tab[d][h] for d, h in zip(dates.dt.date, hi)])

        d = np.clip(di, 0, self.n_days)
        win30 = self.P_daily[:, d] - self.P_daily[:, np.clip(d - 30, 0, None)]   # [cells, rows]
        win90 = self.P_daily[:, d] - self.P_daily[:, np.clip(d - 90, 0, None)]
        rows = np.arange(len(ci))
        nb1_30 = (self.R1[ci] * win30.T).sum(axis=1)
        nb2_30 = (self.R2[ci] * win30.T).sum(axis=1)
        nb1_90 = (self.R1[ci] * win90.T).sum(axis=1)

        # Same cell, same hour +/- 1, previous 365 days.
        lo365 = np.clip(d - 365, 0, None)
        band = np.zeros(len(ci))
        for off in (-1, 0, 1):
            h = (hi + off) % 24
            band += self.P_hour[ci, d, h] - self.P_hour[ci, lo365, h]

        # Long-run rates: incidents per year using all history before the row's day.
        yrs = np.maximum(d, 1) / 365.0
        band_all = sum(self.P_hour[ci, d, (hi + off) % 24] for off in (-1, 0, 1))

        area7 = self.P_area[d] - self.P_area[np.clip(d - 7, 0, None)]

        return pd.DataFrame({
            "hour": hi,
            "weekday": weekday,
            "month": month,
            "season": [geo.season(m) for m in month],
            "is_weekend": (weekday >= 5).astype(int),
            "is_dark": is_dark,
            "cell_lat": self.cell_lat[ci],
            "cell_lon": self.cell_lon[ci],
            "dist_bluelight_m": self.dist_bl[ci],
            "cell_7d": self._win(self.P_daily, ci, di, 7),
            "cell_30d": win30[ci, rows],
            "cell_90d": win90[ci, rows],
            "cell_sev_90d": self._win(self.P_sev, ci, di, 90),
            "cell_violent_90d": self._win(self.P_viol, ci, di, 90),
            "cell_hourband_365d": band,
            "cell_rate_all": self.P_daily[ci, d] / yrs,
            "cell_hourband_rate_all": band_all / yrs,
            "nb1_30d": nb1_30,
            "nb2_30d": nb2_30,
            "nb1_90d": nb1_90,
            "area_7d": area7,
        })[FEATURES].astype("float32")

    def target(self, ci, di, hi, kind="any"):
        cube = self.violent_cube if kind == "violent" else self.cube
        return (cube[ci, di, hi] > 0).astype(np.int8)

    # -- frames --------------------------------------------------------------
    def _grid(self, d0, d1):
        """All (area cell, day, hour) index triples for day indices [d0, d1]."""
        nc = len(self.cells)
        c, d, h = np.meshgrid(np.arange(nc), np.arange(d0, d1 + 1), np.arange(24), indexing="ij")
        return c.ravel(), d.ravel(), h.ravel()

    def frame_for_range(self, start: str, end: str, kind="any", chunk_days=60):
        """Full-grid frame (every area cell x hour x day) between two dates."""
        d0 = max(self.day_index(dt.date.fromisoformat(start)), config.WARMUP_DAYS)
        d1 = self.day_index(dt.date.fromisoformat(end))
        parts = []
        for a in range(d0, d1 + 1, chunk_days):
            ci, di, hi = self._grid(a, min(a + chunk_days - 1, d1))
            X = self.features(ci, di, hi)
            X["y"] = self.target(ci, di, hi, kind)
            X["day"] = di
            X["cell_idx"] = ci
            parts.append(X)
        return pd.concat(parts, ignore_index=True)

    def training_frame(self, kind="any", neg_per_pos=config.NEG_PER_POS, seed=config.SEED):
        """All positives + sampled negatives from DATA_START+warmup .. TRAIN_END."""
        d0 = config.WARMUP_DAYS
        d1 = self.day_index(dt.date.fromisoformat(config.TRAIN_END))
        ci, di, hi = self._grid(d0, d1)
        y = self.target(ci, di, hi, kind)
        pos = np.flatnonzero(y == 1)
        neg_all = np.flatnonzero(y == 0)
        rng = np.random.default_rng(seed)
        n_neg = min(len(neg_all), neg_per_pos * len(pos))
        neg = rng.choice(neg_all, size=n_neg, replace=False)
        idx = np.sort(np.concatenate([pos, neg]))
        X = self.features(ci[idx], di[idx], hi[idx])
        X["y"] = y[idx]
        X["day"] = di[idx]
        X["cell_idx"] = ci[idx]
        neg_rate = n_neg / len(neg_all)
        return X, neg_rate, len(pos) / len(y)


def main():
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["any", "violent"], default="any")
    args = ap.parse_args()

    hist = History()
    X, neg_rate, base_rate = hist.training_frame(kind=args.target)
    config.ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    X.to_parquet(config.DATASET_PATH, index=False)
    info = {"rows": len(X), "positives": int(X["y"].sum()), "neg_rate": neg_rate,
            "true_base_rate": base_rate, "cells": len(hist.cells), "target": args.target}
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
