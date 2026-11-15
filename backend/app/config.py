import os


def _env(name, default, cast=str):
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return cast(val)


class Config:
    SQLALCHEMY_DATABASE_URI = _env(
        "DATABASE_URL", "postgresql+psycopg2://safe:safe@localhost:5432/saferoute"
    )
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    DATA_DIR = _env("DATA_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data")))

    # Study area
    USC_LAT = _env("USC_LAT", 34.0224, float)
    USC_LNG = _env("USC_LNG", -118.2851, float)
    RADIUS_KM = _env("RADIUS_KM", 16.09, float)
    H3_RES = _env("H3_RES", 9, int)
    H3_AREA_RES = _env("H3_AREA_RES", 6, int)

    # Ingestion intervals (minutes)
    POLL_SUN_MINUTES = _env("POLL_SUN_MINUTES", 1, float)
    POLL_OPENMETEO_MINUTES = _env("POLL_OPENMETEO_MINUTES", 5, float)
    POLL_NWS_MINUTES = _env("POLL_NWS_MINUTES", 10, float)
    POLL_LAPD_MINUTES = _env("POLL_LAPD_MINUTES", 60, float)
    POLL_LA311_MINUTES = _env("POLL_LA311_MINUTES", 30, float)

    # External sources
    SOCRATA_APP_TOKEN = _env("SOCRATA_APP_TOKEN", "")
    LAPD_DATASET_ID = _env("LAPD_DATASET_ID", "2nrs-mtv8")
    CRIME_BACKFILL_START = _env("CRIME_BACKFILL_START", "2024-01-01")
    LAPD_NIBRS_DATASET_ID = _env("LAPD_NIBRS_DATASET_ID", "k7nn-b2ep")
    NIBRS_BACKFILL_START = _env("NIBRS_BACKFILL_START", "2025-01-01")
    LA311_DATASET_ID = _env("LA311_DATASET_ID", "")
    NWS_STATIONS = _env("NWS_STATIONS", "KCQT,KLAX")
    HTTP_USER_AGENT = _env("HTTP_USER_AGENT", "usc-safe-route-hackathon")
    HTTP_TIMEOUT = _env("HTTP_TIMEOUT", 15, int)

    ADMIN_TOKEN = _env("ADMIN_TOKEN", "")

    # flask-smorest / OpenAPI
    API_TITLE = "USC Safe-Route Data API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.0.3"
    OPENAPI_URL_PREFIX = "/api"
    OPENAPI_JSON_PATH = "openapi.json"
    OPENAPI_SWAGGER_UI_PATH = "/docs"
    OPENAPI_SWAGGER_UI_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.11.0/"
    JSON_SORT_KEYS = False
