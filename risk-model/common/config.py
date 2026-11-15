"""Central knobs for the SafeRoute risk model. Tune here, not in code."""
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DB_PATH = ROOT / "data" / "crime.db"
BLUELIGHT_CSV = ROOT / "data" / "bluelight_phones.csv"
ARTIFACT_DIR = ROOT / "models" / "artifacts"
DATASET_PATH = ARTIFACT_DIR / "dataset.parquet"
MODEL_PATH = ARTIFACT_DIR / "xgb_risk.ubj"
META_PATH = ARTIFACT_DIR / "model_meta.json"
CACHE_PATH = ROOT / "scoring" / "cache" / "risk_cache.json"
REPORT_DIR = ROOT / "notebooks"

# Area of interest: a circle of RADIUS_MILES around USC (Tommy Trojan).
# 5 miles reaches roughly Koreatown/Mid-City to the north-west, Downtown,
# Boyle Heights and Vernon to the east, and Hyde Park/Florence to the south.
# A few slices of it (Vernon, Huntington Park, county islands, Inglewood's
# north edge) are NOT policed by LAPD, so they have no data in this dataset;
# see MIN_CELL_INCIDENTS below.
CENTER = (34.0224, -118.2851)  # Tommy Trojan
RADIUS_MILES = 5.0
RADIUS_M = RADIUS_MILES * 1609.344
_dlat = RADIUS_M / 111_320.0
_dlon = RADIUS_M / (111_320.0 * math.cos(math.radians(CENTER[0])))
# Bounding box of the circle, (south, west, north, east): used for the API
# query and map extents; rows are then filtered to the circle itself.
BBOX = (CENTER[0] - _dlat, CENTER[1] - _dlon, CENTER[0] + _dlat, CENTER[1] + _dlon)
TIMEZONE = "America/Los_Angeles"
LAT, LON = CENTER  # for sunrise / twilight

H3_RES = 9  # hexagons ~210 m per side, ~370 m across, ~0.12 km^2 each (near USC)

# LAPD "Crime Data from 2020 to Present" (2nrs-mtv8) ends in Dec 2024, and
# 2024 drops to ~half volume after March (LAPD's NIBRS switch). We ingest
# 2020-2024 but train on 2020-2022, test on 2023, and treat 2024 as an extra
# out-of-time check, not training data.
DATA_START = "2020-01-01"
DATA_END = "2024-12-31"
TRAIN_END = "2022-12-31"      # train: 2020-01-01 .. 2022-12-31
VALID_START = "2022-07-01"    # early-stopping slice carved from the end of train
TEST_START, TEST_END = "2023-01-01", "2023-12-31"
EXTRA_START, EXTRA_END = "2024-01-01", "2024-12-31"
# Live scoring (dates after the data) anchors history features here: the last
# full year before the NIBRS reporting drop, so live scores aren't dragged
# down by the thin late-2024 counts.
LIVE_HISTORY_END = TEST_END
# Daily features need this much history before a row is usable.
WARMUP_DAYS = 90

# Negatives sampled per positive. Training uses NEG_PER_POS; the out-of-time
# evaluation frames use EVAL_NEG_PER_POS and carry weights so metrics equal
# full-grid metrics in expectation (the full grid is ~150M cell-hours).
NEG_PER_POS = 5
EVAL_NEG_PER_POS = 10
SEED = 42

# A cell is modelled only if it had at least this many incidents in the
# training years. Cells below it are mostly outside LAPD's jurisdiction, where
# no records means no data, not "safe"; they are reported as no_data.
MIN_CELL_INCIDENTS = 5

# ---- Per-incident weights (see common/severity.py for the full formula) -----
# Each past crime counts severity x recency x time-of-day match.
#
# Recency is LOGARITHMIC (a power law), not a fixed half-life:
#     recency(age) = 1 / (1 + age_days / RECENCY_TAU_DAYS) ** RECENCY_POWER
# so the weight drops by the same factor every time a crime's age multiplies:
#     1 day 0.75 | 1 week 0.30 | 1 month 0.09 | 3 months 0.03 | 1 year 0.008
# A crime yesterday counts ~8x one from last month and ~90x one from last
# year, so recent crime moves the score far more than old crime. The long tail
# still lets a year-old shooting count a little; the long-run count features
# (cell_rate_all, ...) carry the place's baseline.
# (Implementation: the curve is fitted as a sum of exponentials, RECENCY_BASIS
# half-lives, so it can be updated day by day; max error ~1.5%.)
RECENCY_TAU_DAYS = 3.0
RECENCY_POWER = 1.0
RECENCY_BASIS = (0.5, 1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)
#   time of day = exp(-dh**2 / (2 * TOD_SIGMA_HOURS**2)), dh = circular hour gap.
#               With sigma = 2 h a crime 2 h away counts 0.61, 4 h away 0.14,
#               6 h away 0.01, so a 10 pm row mostly sees evening crimes.
TOD_SIGMA_HOURS = 2.0

# ---- Blue-light phones: an explicit, protective effect -------------------
# Two layers so a phone can only ever LOWER risk:
#  1. The model sees dist_bluelight_m with a +1 monotone constraint: farther
#     from a phone can never mean safer.
#  2. Scoring then multiplies the model's probability by
#        1 - BLUELIGHT_MAX_REDUCTION * exp(-dist_m / BLUELIGHT_RANGE_M)
#     i.e. up to 25% lower right at a phone, ~13% at 100 m, ~5% at 250 m,
#     ~0 beyond 600 m. This is a policy choice, not learned: phones are
#     placed where people already walk, so the raw data can't show their
#     deterrent effect. geo.bluelight_protection() returns the same 0..1
#     protection for the route planner.
BLUELIGHT_MAX_REDUCTION = 0.25
BLUELIGHT_RANGE_M = 150.0

# Features the model is forced to treat as "more history => more risk"
# (XGBoost monotone_constraints = +1), plus dist_bluelight_m (farther from a
# phone => never safer). A safety app must never say "more violent crime here,
# so lower risk", which unconstrained trees can do with correlated features.
MONOTONE_INCREASING = [
    "harm_recent", "harm_recent_tod", "nb1_harm_recent", "nb2_harm_recent",
    "cell_hourband_365d", "cell_rate_all", "cell_hourband_rate_all",
    "dist_bluelight_m",
]
