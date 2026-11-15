"""How much each reported crime counts: its severity, on a 0-10 harm scale.

Every incident gets its own weight in the model's history features:

    weight_i(row) = severity_i                       (this file)
                  x 1 / (1 + age_days / tau)         (log-scale recency, config.RECENCY_*)
                  x exp(-dh**2 / (2 * sigma**2))     (time of day, config.TOD_SIGMA_HOURS)

where `age_days` is how long before the row's date the crime happened and `dh`
is the circular gap in hours between the crime's hour and the row's hour.
features/build_dataset.py sums these weights per cell.

How the severities were chosen
------------------------------
The scale follows the idea behind crime-harm indices such as the Cambridge
Crime Harm Index (Sherman et al., 2016): weight each offence by how much harm
it does to a victim, roughly on a log scale of sentencing severity, instead of
counting every report as 1. We care about a person walking at night, so the
anchors are crimes against a person in public:

    10  homicide
     8  rape and other forcible sexual assault
     7  kidnapping, human trafficking
     6  shootings, aggravated assault / assault with a deadly weapon
     5  robbery (theft by force or threat: the archetypal street crime)
     4  attempted robbery, sexual battery, brandishing a weapon
     3  simple assault / battery, theft from a person (pickpocket, purse
        snatching), criminal threats, arson
     2  burglary, stalking, indecent exposure, restraining-order violations
     1  vehicle crime, plain theft, vandalism (felony), trespass
   0.5  minor property / public-order offences, anything unlisted
  0.25  fraud, identity theft, forgery, embezzlement: the reported location
        is usually the victim's home or a bank, so they say little about
        street safety

A real weapon raises a crime below 6 by 50%, capped at 6, because an armed
incident is more dangerous than the same offence unarmed. "Real weapon" means
LAPD `weapon_used_cd` 100-399 (firearms, knives and cutting tools, blunt
objects). Strong-arm (400, hands and fists), verbal threats (511), "unknown
weapon" (500) and similar codes do not count: they are already part of what
the offence code describes.

Tune the numbers in SEVERITY_BY_CODE / WEAPON_MULTIPLIER; nothing else needs to
change. `severity_tier` (violent / property / other) is kept only for charts.
"""

# LAPD crime codes (crm_cd) -> severity. Codes not listed fall back to keyword
# rules, then to DEFAULT_SEVERITY.
SEVERITY_BY_CODE = {
    # 10: homicide
    110: 10, 113: 10,
    # 8: rape and forcible sexual assault
    121: 8, 122: 8, 815: 8, 820: 8, 821: 8,
    # 7: kidnapping, trafficking
    910: 7, 920: 7, 921: 7, 822: 7, 922: 6,
    # 6: shootings, aggravated assault
    230: 6, 231: 6, 235: 6, 236: 6, 250: 6, 251: 6, 753: 6, 435: 6, 436: 6,
    # 5: robbery
    210: 5,
    # 4: attempted robbery, sexual battery, brandishing
    220: 4, 860: 4, 761: 4, 760: 4, 840: 4, 812: 4,
    # 3: simple assault, theft from a person, threats, arson
    624: 3, 625: 3, 626: 3, 627: 3, 623: 3, 622: 3, 647: 3,
    350: 3, 351: 3, 352: 3, 353: 3, 450: 3, 451: 3, 452: 3,
    930: 3, 928: 2, 648: 3, 434: 3, 940: 3, 756: 3,
    # 2: burglary, stalking, exposure, protective-order violations
    310: 2, 320: 2, 763: 2, 850: 2, 762: 2, 932: 2, 933: 2, 813: 2,
    900: 2, 901: 2, 902: 2, 903: 1, 904: 2, 956: 1, 237: 2, 870: 2,
    # 1: vehicle crime, plain theft, felony vandalism, trespass
    510: 1, 520: 1, 522: 1, 433: 1, 480: 1, 485: 1, 487: 1,
    330: 1, 331: 1, 410: 1, 420: 1, 421: 1,
    341: 1, 343: 1, 440: 1, 441: 1, 442: 1, 443: 1,
    740: 1, 888: 1, 886: 1, 437: 1, 438: 1, 755: 1, 882: 1, 884: 1,
    # 0.5: minor property / public order
    745: 0.5, 890: 0.5, 949: 0.5, 943: 0.5, 880: 0.5, 924: 0.5,
    470: 0.5, 471: 0.5, 473: 0.5, 474: 0.5, 475: 0.5, 444: 0.5, 345: 0.5,
    # 0.25: fraud / paper crimes
    354: 0.25, 662: 0.25, 664: 0.25, 666: 0.25, 668: 0.25, 670: 0.25,
    649: 0.25, 651: 0.25, 652: 0.25, 653: 0.25, 654: 0.25, 660: 0.25,
    661: 0.25, 347: 0.25, 349: 0.25, 446: 0.25, 439: 0.25, 942: 0.25,
    944: 0.25, 948: 0.25, 950: 0.25, 951: 0.25, 954: 0.25, 845: 0.25,
    814: 0.25, 805: 0.25, 806: 0.25, 865: 0.25, 931: 0.25,
}
# Used for codes missing above (new LAPD codes): first keyword that matches wins.
KEYWORD_SEVERITY = (
    ("HOMICIDE", 10), ("RAPE", 8), ("KIDNAP", 7), ("TRAFFICKING", 7),
    ("SHOTS FIRED", 6), ("DEADLY WEAPON", 6), ("AGGRAVATED", 6), ("ROBBERY", 5),
    ("ASSAULT", 3), ("BATTERY", 3), ("BURGLARY", 2), ("STOLEN", 1), ("THEFT", 1),
    ("VANDALISM", 1),
)
DEFAULT_SEVERITY = 0.5
WEAPON_MULTIPLIER = 1.5
WEAPON_CAP = 6.0
MAX_SEVERITY = 10.0


def is_armed(weapon_used_cd) -> bool:
    """True for firearms, knives/cutting tools and blunt objects (LAPD 100-399)."""
    try:
        return 100 <= int(float(weapon_used_cd)) <= 399
    except (TypeError, ValueError):
        return False


def incident_severity(crm_cd, desc: str | None = None, weapon_used_cd=None) -> float:
    """Severity of one incident on the 0-10 scale above."""
    try:
        s = SEVERITY_BY_CODE.get(int(crm_cd))
    except (TypeError, ValueError):
        s = None
    if s is None:
        d = (desc or "").upper()
        s = next((v for k, v in KEYWORD_SEVERITY if k in d), DEFAULT_SEVERITY)
    if is_armed(weapon_used_cd) and s < WEAPON_CAP:
        s = min(s * WEAPON_MULTIPLIER, WEAPON_CAP)
    return float(s)


# ---- display-only tiers (charts in the notebook) -----------------------------
def severity_tier(desc: str | None, crm_cd=None, weapon_used_cd=None) -> str:
    """violent (crimes against a person, severity >= 3), property (1-2.9), other."""
    s = incident_severity(crm_cd, desc, weapon_used_cd) if crm_cd is not None else None
    if s is None:  # description only (old callers)
        d = (desc or "").upper()
        s = next((v for k, v in KEYWORD_SEVERITY if k in d), DEFAULT_SEVERITY)
    if s >= 3:
        return "violent"
    if s >= 1:
        return "property"
    return "other"
