import pytest

from app.services.text_norm import normalize


@pytest.mark.parametrize("raw, expected", [
    ("Trojan's Grill", "trojans grill"),
    ("Trojan’s", "trojans"),
    ("Café Dulce", "cafe dulce"),
    ("  Von KleinSmid   Center (VKC) ", "von kleinsmid center vkc"),
    ("In-N-Out Burger", "in n out burger"),
    ("7-Eleven #1234", "7 eleven 1234"),
    ("Straße", "strasse"),
    ("Ñandú & Co.", "nandu co"),
    ("", ""),
    (None, ""),
    ("!!!", ""),
])
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_idempotent():
    s = "Edward L. Doheny Jr. Memorial Library"
    assert normalize(normalize(s)) == normalize(s) == "edward l doheny jr memorial library"
