"""Map an LAPD crime description to a severity tier: violent / property / other.

Simple keyword lookup on purpose: it's explainable and easy to tune. Crimes
against people are "violent" because that is what a walker worries about.
"""

VIOLENT = (
    "HOMICIDE", "MANSLAUGHTER", "RAPE", "SODOMY", "ORAL COPULATION",
    "SEXUAL PENETRATION", "SEX,UNLAWFUL", "ROBBERY", "ASSAULT", "BATTERY",
    "KIDNAP", "SHOTS FIRED", "BRANDISH", "CRIMINAL THREATS", "LEWD",
    "INDECENT EXPOSURE", "STALKING", "PURSE SNATCHING", "INTIMATE PARTNER",
    "CHILD ABUSE", "HUMAN TRAFFICKING", "LYNCHING", "DISCHARGE FIREARMS",
)
PROPERTY = (
    "BURGLARY", "THEFT", "STOLEN", "VANDALISM", "SHOPLIFTING", "PICKPOCKET",
    "EMBEZZLEMENT", "ARSON", "BIKE", "VEHICLE", "TILL TAP", "BUNCO",
)


def severity_tier(desc: str | None) -> str:
    d = (desc or "").upper()
    if any(k in d for k in VIOLENT):
        return "violent"
    if any(k in d for k in PROPERTY):
        return "property"
    return "other"
