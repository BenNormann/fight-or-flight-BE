"""Text normalization shared by the place loader (writes name_norm/search_text) and
the search query path (normalizes q). Both sides MUST use this one function so the
stored and queried forms agree. Dependency-free on purpose.
"""
import re
import unicodedata

# Apostrophe-like characters are dropped without splitting ("Trojan's" -> "trojans").
_APOSTROPHES = re.compile("['‘’ʼ`´]")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize(text) -> str:
    """Lowercase, strip accents (NFKD), drop apostrophes, other non-alphanumerics -> space,
    collapse whitespace. None/empty -> ''."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold()  # like lower(), but also folds e.g. "ß" -> "ss"
    s = _APOSTROPHES.sub("", s)
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())
