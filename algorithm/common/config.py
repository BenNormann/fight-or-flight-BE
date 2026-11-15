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
# 10 miles reaches roughly Hollywood/Griffith Park to the north, Santa Monica
# Blvd/Century City to the west, LAX/Inglewood to the south-west and
# East LA/Huntington Park to the east. Parts of it (Inglewood, Culver City,
# Beverly Hills, Vernon, county areas) are NOT policed by LAPD, so they have
# no data in this dataset; see MIN_CELL_INCIDENTS below.
CENTER = (34.0224, -118.2851)  # Tommy Trojan
RADIUS_MILES = 10.0
RADIUS_M = RADIUS_MILES * 1609.344
_dlat = RADIUS_M / 111_320.0
_dlon = RADIUS_M / (111_320.0 * math.cos(math.radians(CENTER[0])))
# Bounding box of the circle, (south, west, north, east): used for the API
# query and map extents; rows are then filtered to the circle itself.
BBOX = (CENTER[0] - _dlat, CENTER[1] - _dlon, CENTER[0] + _dlat, CENTER[1] + _dlon)
TIMEZONE = "America/Los_Angeles"
LAT, LON = CENTER  # for sunrise / twilight

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

SEVERITY_WEIGHT = {"violent": 1.0, "property": 0.4, "other": 0.2}
