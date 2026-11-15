"""Search ranking (pure Python, dict fixtures) and the search endpoints with the DB mocked."""
import sys
import types
from datetime import datetime

import pytest

from app.services import search as svc
from app.services.search import (T_EXACT, T_FUZZY, T_INTENT, T_PREFIX, T_WEAK, T_WORD, build_candidate_sql,
                                 classify, hours_by_weekday, osa_distance, parse_query, rank)
from app.services.text_norm import normalize

USC = (34.0224, -118.2851)
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=svc.LA)


def fake_intent(q):
    table = {"coffee": [{"category": "cafe", "subcategory": None}],
             "cafe": [{"category": "cafe", "subcategory": None}],
             "food": [{"category": "food", "subcategory": None}],
             "thai": [{"category": "food", "subcategory": "thai_restaurant"}],
             "thai food": [{"category": "food", "subcategory": "thai_restaurant"}],
             "parking": [{"category": "parking", "subcategory": None}]}
    if q in table:
        return table[q]
    prefixed = [v for k, v in table.items() if len(q) >= 3 and k.startswith(q)]   # fires on >=3-char prefixes
    return prefixed[0] if prefixed else []


def Q(q):
    return parse_query(q, intent_fn=fake_intent)


def place(name, dlat=0.0, dlng=0.0, kind="place", category="food", subcategory=None, aliases=(),
          address=None, popularity=0.3, hours=None, pid=None):
    return {"id": pid or f"p:{normalize(name).replace(' ', '-')}:{dlat}:{dlng}", "kind": kind, "name": name,
            "name_norm": normalize(name), "aliases": list(aliases), "category": category,
            "subcategory": subcategory, "address": address, "lat": USC[0] + dlat, "lng": USC[1] + dlng,
            "rating": 4.2, "review_count": 100, "popularity": popularity, "hours": hours}


def names(ranked):
    return [r["name"] for r in ranked]


def ranked(q, cands, near=USC, **kw):
    kw.setdefault("is_open_fn", lambda h, now: None)
    return rank(Q(q), cands, near=near, now=NOW, **kw)


VKC = place("Von KleinSmid Center", kind="building", category="campus", aliases=["VKC"], dlat=0.004, popularity=0.5)
DULCE = place("Dulce Cafe", category="cafe", dlat=0.001, popularity=0.4)
DOHENY = place("Edward L. Doheny Jr. Memorial Library", kind="building", category="campus", aliases=["DML"],
               dlat=0.002, popularity=0.7)
SBUX_NEAR = place("Starbucks", category="cafe", dlat=0.001, popularity=0.5, address="3335 S Figueroa St, Los Angeles")
SBUX_FAR = place("Starbucks", category="cafe", dlat=0.02, popularity=0.9, address="800 W Olympic Blvd, Los Angeles")
COFFEE_BEAN = place("The Coffee Bean & Tea Leaf", category="cafe", dlat=0.003)
BLUE_BOTTLE = place("Blue Bottle", category="cafe", dlat=0.0005)
BLUE_BOTTLE_COFFEE = place("Blue Bottle Coffee", category="cafe", dlat=0.01)


# ---------------------------------------------------------------- primitives

def test_osa_distance():
    assert osa_distance("doheney", "doheny") == 1
    assert osa_distance("starbuks", "starbucks") == 1
    assert osa_distance("cafe", "caef") == 1          # transposition counts once
    assert osa_distance("abc", "xyz", max_d=1) == 2   # early exit


def test_parse_query_stop_words_and_intent():
    q = Q("coffee near vermont")
    assert q.sig == ["coffee", "vermont"] and q.token_intents == {"coffee": fake_intent("coffee")}
    assert q.covering == []                  # vermont has no category meaning: must match by text
    assert Q("coffee shop").sig == ["coffee"] and Q("coffee shop").covering
    assert Q("thai food").covering == [{"category": "food", "subcategory": "thai_restaurant"}]
    assert Q("coffee bean").covering == []   # brand, not "any cafe"
    assert Q("the").sig == ["the"]           # all stop words: keep them


# ---------------------------------------------------------------- tiers

def test_exact_building_code_beats_everything():
    vkc_cafe = place("VKC Coffee Cart", category="cafe", dlat=0.0001, popularity=1.0)
    out = ranked("vkc", [vkc_cafe, VKC, place("Vkcentral", dlat=0.0002, popularity=1.0)])
    assert out[0]["name"] == "Von KleinSmid Center" and out[0]["_tier"] == T_EXACT


def test_name_and_word_prefix():
    assert classify(Q("dulc"), DULCE)[0] == T_PREFIX
    assert classify(Q("klein"), VKC)[0] == T_WORD
    assert classify(Q("von klein"), VKC)[0] == T_PREFIX      # tokens prefix name words in order
    assert classify(Q("vonklein"), VKC)[0] == T_WORD         # run-together words
    assert classify(Q("center klein"), VKC)[0] == T_WORD     # any order


def test_dulc():
    out = ranked("dulc", [place("Dulcinea Salon", category="services", dlat=0.03),
                          DULCE, place("Cafe Dulce Vita", category="cafe", dlat=0.0001)])
    assert names(out) == ["Dulce Cafe", "Cafe Dulce Vita", "Dulcinea Salon"]
    assert [r["_tier"] for r in out] == [T_PREFIX, T_WORD, T_PREFIX]   # T1/T2 share a band; distance decides


def test_full_prefix_bonus_within_prefix_band():
    a = place("Dulce", dlat=0.002, pid="full")            # T1
    b = place("Cafe Dulce", dlat=0.002, pid="word")       # T2, same distance and popularity
    assert [r["id"] for r in ranked("dulc", [b, a])] == ["full", "word"]


def test_doheney_typo_finds_doheny():
    out = ranked("doheney", [DOHENY, place("Dohen Market", dlat=0.0001)])
    assert out[0]["name"].startswith("Edward L. Doheny") and out[0]["_tier"] == T_FUZZY


def test_starbuks_typo_and_fuzzy_brand_above_category_only():
    out = ranked("starbuks", [BLUE_BOTTLE, SBUX_FAR, SBUX_NEAR])
    assert [r["_tier"] for r in out] == [T_FUZZY, T_FUZZY]
    assert out[0]["distance_m"] < out[1]["distance_m"]        # chain branches kept, nearest first


def test_prefix_beats_fuzzy():
    out = ranked("starb", [place("Starbucks", dlat=0.05, popularity=0.0),
                           place("Stairb Lounge", dlat=0.0001, popularity=1.0)])
    assert out[0]["name"] == "Starbucks"


def test_fuzzy_beats_category_only_but_not_prefix():
    """'cafe' fuzzy-matching 'care' is noise when the query is a category word."""
    out = ranked("cafe", [place("Care Pharmacy", category="health", dlat=0.0001),
                          BLUE_BOTTLE, place("Cafe Dulce", category="cafe", dlat=0.02)])
    assert names(out) == ["Cafe Dulce", "Blue Bottle"]
    assert [r["_tier"] for r in out] == [T_PREFIX, T_INTENT]


def test_coffee_intent():
    out = ranked("coffee", [BLUE_BOTTLE, BLUE_BOTTLE_COFFEE, COFFEE_BEAN, place("Pizza Place", dlat=0.0001)])
    assert names(out) == ["The Coffee Bean & Tea Leaf", "Blue Bottle Coffee", "Blue Bottle"]
    assert [r["_tier"] for r in out] == [T_PREFIX, T_WORD, T_INTENT]   # leading "the" ignored for prefix


def test_multi_token_name_plus_address():
    out = ranked("starbucks figueroa", [SBUX_FAR, SBUX_NEAR, place("Figueroa Hotel", dlat=0.0001)])
    assert names(out) == ["Starbucks"] and out[0]["address"].startswith("3335")


def test_multi_token_intent_plus_address():
    on_vermont = place("Blue Bottle", category="cafe", dlat=0.01, address="1000 S Vermont Ave")
    out = ranked("coffee near vermont", [BLUE_BOTTLE, on_vermont, place("Vermont Market", dlat=0.0001)])
    assert [(r["name"], r["_tier"]) for r in out] == [("Blue Bottle", T_INTENT)]
    assert out[0]["address"] == "1000 S Vermont Ave"


def test_address_only_is_weak():
    out = ranked("figueroa", [SBUX_NEAR, place("Figueroa Hotel", dlat=0.02)])
    assert names(out) == ["Figueroa Hotel", "Starbucks"] and out[1]["_tier"] == T_WEAK


def test_unmatched_candidates_dropped():
    assert ranked("zzzz", [DULCE, VKC]) == []


# ---------------------------------------------------------------- within-tier ordering

def test_nearer_beats_more_popular_within_tier():
    near_ = place("Starbucks", dlat=0.001, popularity=0.1, pid="a")
    far_ = place("Starbucks", dlat=0.015, popularity=1.0, pid="b")
    assert [r["id"] for r in ranked("starbucks", [far_, near_])] == ["a", "b"]


def test_popularity_breaks_distance_ties():
    a = place("Starbucks", dlat=0.002, popularity=0.2, pid="a")
    b = place("Starbucks", dlng=0.002 / 0.829, popularity=0.8, pid="b")   # same distance, other direction
    assert [r["id"] for r in ranked("starbucks", [a, b])] == ["b", "a"]


def test_no_near_uses_popularity_and_omits_distance():
    a = place("Starbucks", dlat=0.001, popularity=0.2, pid="a")
    b = place("Starbucks", dlat=0.03, popularity=0.8, pid="b")
    out = ranked("starbucks", [a, b], near=None)
    assert [r["id"] for r in out] == ["b", "a"] and "distance_m" not in out[0]


def test_building_bump_within_tier():
    hall = place("Taper Hall", kind="building", category="campus", dlat=0.0021, popularity=0.3)
    shop = place("Taper Shop", dlat=0.002, popularity=0.3)
    assert names(ranked("taper", [shop, hall]))[0] == "Taper Hall"


def test_one_char_query_works_and_leans_on_proximity_popularity():
    cands = [place("Dulce Cafe", dlat=0.0005, popularity=0.4), place("Doheny", dlat=0.03, popularity=0.9),
             place("Ad Astra", dlat=0.0001), place("Blue", dlat=0.0001)]
    out = ranked("d", cands)
    assert names(out) == ["Dulce Cafe", "Doheny"]               # name prefix only; "Ad Astra" not a word prefix
    assert all(r["_tier"] == T_PREFIX for r in out)


def test_empty_query_category_only_orders_by_distance():
    out = ranked("", [place("Far", dlat=0.02, popularity=1.0), place("Near", dlat=0.001)])
    assert names(out) == ["Near", "Far"] and out[0]["_tier"] == T_INTENT


# ---------------------------------------------------------------- open_now

def test_open_now_filters_closed_and_unknown():
    cands = [place("Starbucks", pid="open", hours={"5": [[0, 1440]]}),
             place("Starbucks", pid="closed", hours={"5": [[0, 60]]}),
             place("Starbucks", pid="unknown", hours=None)]
    fn = lambda hours, now: hours["5"][0][1] > 720
    out = rank(Q("starbucks"), cands, near=USC, now=NOW, open_now_only=True, is_open_fn=fn)
    assert [r["id"] for r in out] == ["open"]
    everything = rank(Q("starbucks"), cands, near=USC, now=NOW, is_open_fn=fn)
    assert {r["id"]: r["open_now"] for r in everything} == {"open": True, "closed": False, "unknown": None}


def test_hours_by_weekday():
    out = hours_by_weekday({"0": [[480, 1320]], "4": [[1080, 1560]], "6": [[0, 1440]]})
    assert out["monday"] == [{"open": "08:00", "close": "22:00"}]
    assert out["friday"] == [{"open": "18:00", "close": "02:00", "overnight": True}]
    assert out["sunday"][0]["all_day"] and out["tuesday"] == []
    assert hours_by_weekday(None) is None


# ---------------------------------------------------------------- candidate SQL

def test_candidate_sql_branches():
    sql, p = build_candidate_sql(Q("doheney"), near=USC, bbox=(-118.3, 34.0, -118.2, 34.1), cap=60)
    assert p["_trgm"] is True                                              # caller sets the threshold
    assert ":q <% search_text" in sql and "name_norm LIKE :q_prefix" in sql and "search_text LIKE :q_contains" in sql
    assert sql.count("ST_MakeEnvelope") == sql.count("FROM places WHERE")     # hard filter on every branch
    assert p["q"] == "doheney" and p["cap"] == 60

    short, p = build_candidate_sql(Q("d"), near=USC)
    assert "<%" not in short and not p["_trgm"] and p["q_wordprefix"] == "% d%"
    assert "popularity DESC" in short                                     # popular short-prefix matches kept

    chips, p = build_candidate_sql(Q(""), near=USC, categories=["food"])
    assert "UNION" not in chips and "ORDER BY geom <->" in chips and p["cats"] == ["food"]

    multi, p = build_candidate_sql(Q("starbucks figueroa"), near=USC)
    assert "lower(address) LIKE :t1" in multi and p["t1"] == "%figueroa%"
    assert "((search_text LIKE :t0 OR :tq0 <% search_text OR search_text LIKE :t1 OR :tq1 <% search_text) AND" in multi   # index driver


# ---------------------------------------------------------------- endpoints (DB mocked)

@pytest.fixture
def fake_db(monkeypatch):
    calls = {}

    def fetch(query, near, bbox, categories, cap):
        calls.update(query=query, near=near, bbox=bbox, categories=categories, cap=cap)
        return [dict(c) for c in (VKC, DULCE, DOHENY, SBUX_NEAR, SBUX_FAR, BLUE_BOTTLE, COFFEE_BEAN)]

    monkeypatch.setattr(svc, "fetch_candidates", fetch)
    monkeypatch.setattr(svc, "_category_intent", fake_intent)
    monkeypatch.setattr(svc, "_is_open", lambda hours, now: None)
    return calls


def test_search_endpoint_shape(client, fake_db):
    resp = client.get("/api/v1/search?q=vkc&near=34.0224,-118.2851&limit=5")
    assert resp.status_code == 200
    assert "max-age" in resp.headers["Cache-Control"]
    res = resp.get_json()["results"]
    assert res[0]["name"] == "Von KleinSmid Center"
    assert set(res[0]) == {"id", "kind", "name", "category", "subcategory", "address", "lat", "lng",
                           "distance_m", "rating", "review_count"}           # open_now omitted when unknown
    assert isinstance(res[0]["distance_m"], int)
    assert fake_db["near"] == USC and fake_db["cap"] >= 60


def test_search_without_near_has_no_distance(client, fake_db):
    res = client.get("/api/v1/search?q=starbucks").get_json()["results"]
    assert res and all("distance_m" not in r for r in res)


def test_search_category_only(client, fake_db):
    resp = client.get("/api/v1/search?category=Food,cafe&near=34.0224,-118.2851&open_now=true")
    assert resp.status_code == 200 and fake_db["categories"] == ["food", "cafe"]
    assert fake_db["cap"] > svc.candidate_cap(20, False)                   # over-fetch for open_now


@pytest.mark.parametrize("qs, status", [
    ("", 400), ("q=%20%20", 400), ("q=!!!", 400),
    ("q=a&limit=0", 422), ("q=a&limit=51", 422),
    ("q=a&near=34.02", 422), ("q=a&near=91,0", 422), ("q=a&near=abc,def", 422),
    ("q=a&bbox=1,2,3", 422), ("q=a&bbox=3,2,1,4", 422),
])
def test_search_param_validation(client, fake_db, qs, status):
    resp = client.get("/api/v1/search?" + qs)
    assert resp.status_code == status
    body = resp.get_json()
    # the API's standard error shape: 400 via abort -> message; 422 from arg parsing -> errors
    assert body["code"] == status and ("message" in body if status == 400 else "query" in body["errors"])


def test_categories_endpoint(client, monkeypatch):
    fake_tax = types.ModuleType("app.services.place_taxonomy")
    fake_tax.CATEGORIES = [{"id": "food", "label": "Food"}, {"id": "study", "label": "Study"},
                           {"id": "cafe", "label": "Coffee"}]
    monkeypatch.setitem(sys.modules, "app.services.place_taxonomy", fake_tax)
    rows = [{"category": "cafe", "subcategory": "coffee_shop", "count": 5, "label": "Coffee shop"},
            {"category": "food", "subcategory": "chicken_restaurant", "count": 3, "label": "Chicken restaurant"},
            {"category": "food", "subcategory": "thai_restaurant", "count": 7, "label": "Thai cuisine"}]
    monkeypatch.setattr(svc, "fetch_all", lambda sql, **p: rows)
    resp = client.get("/api/v1/search/categories")
    body = resp.get_json()
    assert resp.status_code == 200 and [c["id"] for c in body] == ["food", "cafe"]   # taxonomy order, no zeros
    assert body[0]["count"] == 10 and body[0]["label"] == "Food"
    assert body[0]["types"] == [{"id": "thai_restaurant", "label": "Thai restaurant", "count": 7},
                                {"id": "chicken_restaurant", "label": "Chicken restaurant", "count": 3}]


def test_place_detail(client, monkeypatch):
    row = {**DULCE, "id": "gmaps:ChIJabc/def", "categories": ["Cafe", "Bakery"], "source": "google_maps",
           "hours": {"0": [[420, 1260]]},
           "detail": {"phone": "(213) 555-0100", "website": "https://x.example", "hours_text": {"Monday": ["7 AM-9 PM"]},
                      "reviews": [{"author": "a", "rating": 5, "text": "good"}]}}
    seen = {}

    def fetch(sql, **p):
        seen.update(p)
        return [dict(row)] if p["id"] == row["id"] else []

    monkeypatch.setattr(svc, "fetch_all", fetch)
    monkeypatch.setattr(svc, "_is_open", lambda hours, now: True)
    resp = client.get("/api/v1/places/gmaps:ChIJabc/def?near=34.0224,-118.2851")
    body = resp.get_json()
    assert resp.status_code == 200 and seen["id"] == "gmaps:ChIJabc/def"
    assert body["hours"]["monday"] == [{"open": "07:00", "close": "21:00"}] and body["hours"]["sunday"] == []
    assert body["phone"] == "(213) 555-0100" and body["open_now"] is True and body["distance_m"] > 0
    assert body["categories"] == ["Cafe", "Bakery"] and body["reviews"][0]["rating"] == 5
    assert "detail" not in body and "name_norm" not in body

    missing = client.get("/api/v1/places/gmaps:nope")
    assert missing.status_code == 404 and missing.get_json()["code"] == 404


def test_reverse_geocode(client, monkeypatch):
    answers = []
    monkeypatch.setattr(svc, "fetch_all", lambda sql, **p: answers.pop(0))

    answers[:] = [[{"id": "bldg:VKC", "kind": "building", "name": "Von KleinSmid Center", "address": None,
                    "distance_m": 12.4}]]
    body = client.get("/api/v1/geocode/reverse?at=34.0219,-118.2840").get_json()
    assert body == {"name": "Von KleinSmid Center", "address": None, "id": "bldg:VKC", "kind": "building",
                    "distance_m": 12}

    answers[:] = [[], [{"name": "['South Figueroa Street', 'Figueroa St']", "distance_m": 30.2}]]
    body = client.get("/api/v1/geocode/reverse?at=34.0219,-118.2840").get_json()
    assert body["name"] == "South Figueroa Street" and body["kind"] == "road"

    answers[:] = [[], []]
    resp = client.get("/api/v1/geocode/reverse?at=34.0219,-118.2840")
    assert resp.status_code == 404 and resp.get_json()["code"] == 404
    assert client.get("/api/v1/geocode/reverse").status_code == 422


def test_openapi_lists_search_routes(client):
    paths = set(client.get("/api/openapi.json").get_json()["paths"])
    assert {"/api/v1/search", "/api/v1/search/categories", "/api/v1/places/{place_id}",
            "/api/v1/geocode/reverse"} <= paths


def test_fetch_candidates_sets_trigram_threshold_only_when_needed(monkeypatch):
    executed, fetched = [], []
    monkeypatch.setattr(svc, "db", types.SimpleNamespace(
        session=types.SimpleNamespace(execute=lambda stmt: executed.append(str(stmt)))))
    monkeypatch.setattr(svc, "fetch_all", lambda sql, **p: fetched.append(p) or [])
    svc.fetch_candidates(Q("doheney"), near=USC)
    assert executed == [f"SET LOCAL pg_trgm.word_similarity_threshold = {svc.WORD_SIM_THRESHOLD}"]
    assert "_trgm" not in fetched[0]
    executed.clear()
    svc.fetch_candidates(Q("du"), near=USC)
    assert executed == []
