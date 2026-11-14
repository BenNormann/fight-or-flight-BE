"""Central knobs for the SafeRoute risk model. Tune here, not in code."""
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

# Area of interest: USC University Park + surrounding blocks
# (Exposition Park north to Adams, Vermont east to Figueroa/110).
# (south, west, north, east)
BBOX = (34.005, -118.305, 34.040, -118.265)
CENTER = (34.0224, -118.2851)  # Tommy Trojan
TIMEZONE = "America/Los_Angeles"
LAT, LON = 34.0224, -118.2851  # for sunrise / twilight

H3_RES = 9  # ~175 m edge, ~0.1 km^2 per cell

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
# Daily features need this much history before a row is usable.
WARMUP_DAYS = 90

# Negatives sampled per positive when building the training frame. The test
# years are scored on the FULL cell x hour x date grid, so metrics are honest.
NEG_PER_POS = 15
SEED = 42

SEVERITY_WEIGHT = {"violent": 1.0, "property": 0.4, "other": 0.2}
