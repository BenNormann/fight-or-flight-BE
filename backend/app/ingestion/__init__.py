from .crime_lapd import LAPDCrimeIngestor, LAPDNIBRSIngestor
from .la311_streetlights import LA311StreetlightIngestor
from .sun_astral import SunIngestor
from .weather_nws import NWSIngestor
from .weather_openmeteo import OpenMeteoIngestor

REGISTRY = {cls.name: cls for cls in (
    SunIngestor, OpenMeteoIngestor, NWSIngestor, LAPDCrimeIngestor, LAPDNIBRSIngestor,
    LA311StreetlightIngestor,
)}


def get_ingestor(name):
    cls = REGISTRY.get(name)
    return cls() if cls else None
