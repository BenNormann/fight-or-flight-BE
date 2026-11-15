"""Place search: query understanding -> one-statement candidate retrieval -> pure-Python ranking.

Stage 0 `parse_query`   normalize q, split tokens, drop stop words, find category intent.
Stage 1 `fetch_candidates`  ONE SQL statement: a UNION ALL of small index-friendly branches
        (exact, name prefix, word prefix/substring, typo, category intent, every-token, address),
        each AND-ed with the hard filters and capped by its own ORDER BY ... LIMIT, so the cut
        keeps the nearest (and, for short queries, the most popular) strong matches.
Stage 2 `rank`          tiers by text-match quality, then a blend of proximity and popularity.

`rank` and `parse_query` touch no database, so they are unit-tested with dict fixtures.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from ..db_utils import fetch_all
from ..extensions import db
from .text_norm import normalize

LA = ZoneInfo("America/Los_Angeles")

# Words that carry no place meaning in a query ("coffee near vermont", "the lot").
STOP_WORDS = frozenset("a an and at by for in near of on or the to around".split())
# Generic words that only refine a category query ("coffee shop", "pizza place", "food near me").
# Dropped only when the query has a category intent, so "The Shop" still searches by name.
INTENT_FILLER = frozenset("shop shops place places spot spots store stores me nearby best good open now".split())

# Text-match tiers, best first.
T_EXACT, T_PREFIX, T_WORD, T_FUZZY, T_INTENT, T_WEAK = range(6)
# Tiers are compared through bands. Full-name prefix (T1) and word prefix (T2) share a band so a
# word-prefix match next door ("dulc" -> "Cafe Dulce", 300 m) is not buried under full-prefix
# matches 12 km away; T1 keeps a score bonus inside the band. Every other tier is its own band.
TIER_BAND = {T_EXACT: 0, T_PREFIX: 1, T_WORD: 1, T_FUZZY: 2, T_INTENT: 3, T_WEAK: 4}
FULL_PREFIX_BONUS = 0.15     # ~ the proximity gap between 100 m and 400 m

D0_M = 400.0                 # walking scale: proximity = 1 / (1 + d / D0)
NEUTRAL_PROXIMITY = 0.5      # when no `near` is given
BUILDING_BOOST = 0.06        # campus building codes are the most common search
WORD_SIM_THRESHOLD = 0.45    # pg_trgm.word_similarity_threshold for the typo branch


# ---------------------------------------------------------------- Stage 0: query understanding

def _category_intent(q_norm):
    try:
        from .place_taxonomy import category_intent
    except ImportError:  # data layer not installed yet
        return []
    return category_intent(q_norm) or []


@dataclass
class Query:
    raw: str = ""
    norm: str = ""
    tokens: list = field(default_factory=list)      # every token of norm
    sig: list = field(default_factory=list)         # significant tokens (stop/filler words removed)
    token_intents: dict = field(default_factory=dict)  # token -> [intent], for tokens with one
    covering: list = field(default_factory=list)    # intents that explain the whole query

    @property
    def short(self):
        return 0 < len(self.norm) <= 2

    @property
    def has_text(self):
        return bool(self.norm)


def parse_query(q, intent_fn=None) -> Query:
    intent_fn = intent_fn or _category_intent
    norm = normalize(q)
    tokens = norm.split()
    sig = [t for t in tokens if t not in STOP_WORDS] or list(tokens)
    token_intents = {t: intent_fn(t) for t in dict.fromkeys(sig) if len(t) >= 3}
    token_intents = {t: v for t, v in token_intents.items() if v}
    full = intent_fn(" ".join(sig)) if sig else []
    if (full or token_intents) and len(sig) > 1:
        trimmed = [t for t in sig if t not in INTENT_FILLER]
        if trimmed and trimmed != sig:
            sig = trimmed
            full = intent_fn(" ".join(sig)) or full
    # The whole-query intent explains the query only if every token means a category too:
    # "thai food" -> thai restaurants; "coffee bean" (bean has no intent) -> must match by name.
    covering = full if full and (len(sig) == 1 or all(t in token_intents for t in sig)) else []
    # "thai food" -> thai restaurants, not all food: a subcategory intent supersedes its parent.
    specific = {it["category"] for it in covering if it.get("subcategory")}
    covering = [it for it in covering if it.get("subcategory") or it["category"] not in specific]
    return Query(raw=q or "", norm=norm, tokens=tokens, sig=sig, token_intents=token_intents, covering=covering)


# ---------------------------------------------------------------- Stage 1: candidate retrieval

SEARCH_COLUMNS = ("p.id, p.kind, p.name, p.name_norm, p.aliases, p.category, p.subcategory, p.address, "
                  "p.lat, p.lng, p.rating, p.review_count, p.popularity, p.hours")


def _intent_sql(intents, params, prefix):
    parts = []
    for i, it in enumerate(intents):
        params[f"{prefix}c{i}"] = it["category"]
        if it.get("subcategory"):
            params[f"{prefix}s{i}"] = it["subcategory"]
            parts.append(f"(category = :{prefix}c{i} AND subcategory = :{prefix}s{i})")
        else:
            parts.append(f"category = :{prefix}c{i}")
    return "(" + " OR ".join(parts) + ")" if parts else None


def build_candidate_sql(query: Query, near=None, bbox=None, categories=None, cap=100):
    """-> (sql, params). One SELECT. params["_trgm"] says whether it uses the `<%` operator,
    which needs the transaction-local word-similarity threshold set first."""
    params = {}
    hard = []
    if bbox:
        hard.append("geom && ST_MakeEnvelope(:bb0, :bb1, :bb2, :bb3, 4326)")
        params.update({f"bb{i}": v for i, v in enumerate(bbox)})
    if categories:
        hard.append("category = ANY(:cats)")
        params["cats"] = list(categories)
    if near:
        params["lat"], params["lng"] = near
        knn = "geom <-> ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)"
        order = knn
    else:
        knn = None
        order = "popularity DESC NULLS LAST"
    params["cap"] = int(cap)

    branches = []

    def branch(score, cond, order_by=order):
        where = " AND ".join([f"({cond})"] + hard)
        branches.append(f"(SELECT id, {score}::real AS s FROM places WHERE {where} ORDER BY {order_by} LIMIT :cap)")

    uses_trgm_op = False
    q = query.norm
    if not q:
        # Category chips: nearest places in the category (pure KNN), popularity tiebreak.
        where = " AND ".join(hard) or "TRUE"
        order_by = f"{knn}, popularity DESC NULLS LAST" if knn else order
        sql = (f"SELECT {SEARCH_COLUMNS}, 1.0::real AS sql_score FROM places p WHERE {where} "
               f"ORDER BY {order_by} LIMIT :cap")
        return sql, params

    params["q"] = q
    params["q_prefix"] = q + "%"
    params["q_word"] = "% " + q + " %"
    # exact name or exact alias/word (building codes: "vkc"); the plain LIKE lets the trigram
    # index prefilter for >= 3 chars, the padded LIKE checks word boundaries
    exact_word = "(' ' || search_text || ' ') LIKE :q_word"
    if len(q) >= 3:
        params["q_contains"] = "%" + q + "%"
        exact_word = f"(search_text LIKE :q_contains AND {exact_word})"
    branch(4, f"name_norm = :q OR {exact_word}")
    # full-name prefix (btree text_pattern_ops)
    branch(3, "name_norm LIKE :q_prefix")
    if len(q) >= 3:
        # word prefix / substring anywhere in name + aliases (GIN trigram)
        branch(2, "search_text LIKE :q_contains")
        # typos ("doheney", "starbuks"): word similarity against name + aliases (GIN trigram)
        uses_trgm_op = True
        branch("1 + word_similarity(:q, search_text)", ":q <% search_text",
               order_by=f"word_similarity(:q, search_text) DESC, {order}")
    else:
        # 1-2 chars: trigrams cannot help; prefix of any word, cut by order (+ popularity below)
        params["q_wordprefix"] = "% " + q + "%"
        branch(2, "search_text LIKE :q_wordprefix")
    if query.short and near:
        # short queries: also keep the most popular prefix matches, not only the nearest
        branch(3, "name_norm LIKE :q_prefix", order_by="popularity DESC NULLS LAST")
        branch(2, "search_text LIKE :q_wordprefix", order_by="popularity DESC NULLS LAST")
    if query.covering:
        branch(1, _intent_sql(query.covering, params, "ci"))
    if len(query.sig) >= 2:
        # every significant token must match somewhere: name/alias, address, typo, or its category
        conds, driver = [], []
        for i, t in enumerate(query.sig):
            params[f"t{i}"] = "%" + t + "%"
            alts = [f"search_text LIKE :t{i}", f"lower(address) LIKE :t{i}"]
            if len(t) >= 3:
                driver.append(f"search_text LIKE :t{i}")
            if len(t) >= 4:
                params[f"tq{i}"] = t
                alts.append(f":tq{i} <% search_text")
                driver.append(f":tq{i} <% search_text")
                uses_trgm_op = True
            intent = _intent_sql(query.token_intents.get(t, []), params, f"ti{i}")
            if intent:
                alts.append(intent)
                driver.append(intent)
            conds.append("(" + " OR ".join(alts) + ")")
        # Index driver (GIN trigram / btree category, BitmapOr): at least one token must hit the
        # name, an alias or its category; the per-token conditions then filter that small set.
        # (Places matching every token only through the address are left to the address branch.)
        if driver:
            conds.insert(0, "(" + " OR ".join(driver) + ")")
        branch(1.5, " AND ".join(conds))
    if len(q) >= 4:
        params["q_addr"] = "%" + q + "%"
        branch(0.5, "lower(address) LIKE :q_addr")

    union = "\n  UNION ALL\n  ".join(branches)
    outer_order = f"b.s DESC, p.{knn}" if knn else "b.s DESC, p.popularity DESC NULLS LAST"
    sql = f"""
SELECT {SEARCH_COLUMNS}, b.s AS sql_score
FROM (SELECT id, max(s) AS s FROM (
  {union}
) u GROUP BY id) b
JOIN places p ON p.id = b.id
ORDER BY {outer_order}"""
    # No outer LIMIT: each branch is already cut to :cap, and a cut by sql_score here would drop the
    # low-scored branches (category intent, address) whenever the text branches fill up.
    params["_trgm"] = uses_trgm_op
    return sql, params


def candidate_cap(limit, open_now):
    cap = min(150, max(60, limit * 5))
    return min(300, cap * 2) if open_now else cap


def fetch_candidates(query, near=None, bbox=None, categories=None, cap=100):
    sql, params = build_candidate_sql(query, near, bbox, categories, cap)
    if params.pop("_trgm", False):
        # Transaction-local (the request's session is rolled back / removed at teardown).
        db.session.execute(text(f"SET LOCAL pg_trgm.word_similarity_threshold = {WORD_SIM_THRESHOLD}"))
    return fetch_all(sql, **params)


# ---------------------------------------------------------------- Stage 2: ranking

def osa_distance(a, b, max_d=None):
    """Damerau-Levenshtein (optimal string alignment). Stops early past max_d."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if max_d is not None and abs(la - lb) > max_d:
        return max_d + 1
    prev2, prev = None, list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if max_d is not None and min(cur) > max_d:
            return max_d + 1
        prev2, prev = prev, cur
    return prev[lb]


def edit_budget(token):
    n = len(token)
    return 0 if n <= 3 else 1 if n <= 7 else 2


def haversine_m(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def _intent_match(c, intents):
    for it in intents or ():
        if c.get("category") == it["category"] and (not it.get("subcategory") or c.get("subcategory") == it["subcategory"]):
            return 2 if it.get("subcategory") else 1
    return 0


def _token_match(t, word_lists, compacts, has_intent):
    """Best name/alias match of one token -> (kind, quality) or None.
    kind: 'prefix' (t starts a word), 'fuzzy' (within edit budget), 'sub' (inside a word)."""
    best = None
    for words in word_lists:
        for pos, w in enumerate(words):
            if w.startswith(t):
                q = len(t) / len(w) + (0.1 if pos == 0 else 0.0)
                if best is None or best[0] != "prefix" or q > best[1]:
                    best = ("prefix", q)
    if best:
        return best
    for comp in compacts:  # "vonklein" -> "vonkleinsmidcenter"
        if len(t) >= 4 and comp.startswith(t):
            return ("prefix", 0.8 * len(t) / len(comp))
    budget = edit_budget(t)
    # A category word ("cafe") fuzzy-matching an unrelated name ("care") is noise, not a typo.
    if budget and not has_intent:
        for words in word_lists:
            for w in words:
                d = osa_distance(t, w, budget)
                if len(w) > len(t):
                    d = min(d, osa_distance(t, w[:len(t)], budget))
                if d <= budget:
                    q = 0.8 * (1 - d / len(t))
                    if best is None or q > best[1]:
                        best = ("fuzzy", q)
    if best:
        return best
    if len(t) >= 3 and any(t in comp for comp in compacts):
        return ("sub", 0.4)
    return None


def _in_order_prefix(tokens, words):
    """All tokens prefix-match name words in order, the first token matching the first word."""
    if not tokens or not words or not words[0].startswith(tokens[0]):
        return False
    i = 1
    for t in tokens[1:]:
        while i < len(words) and not words[i].startswith(t):
            i += 1
        if i == len(words):
            return False
        i += 1
    return True


def classify(query: Query, c):
    """-> (tier, text_similarity in [0, 1]) or None when the candidate does not match."""
    if not query.has_text:
        return T_INTENT, 0.6
    q = query.norm
    name_n = c.get("name_norm") or normalize(c.get("name"))
    alias_ns = [a for a in (normalize(x) for x in (c.get("aliases") or [])) if a]
    if q == name_n or q in alias_ns:
        return T_EXACT, 1.0
    name_words = name_n.split()
    bare = name_n[4:] if name_n.startswith("the ") else name_n   # "The Coffee Bean" for "coffee b"
    if (name_n.startswith(q) or bare.startswith(q)
            or (len(query.tokens) > 1 and _in_order_prefix(query.sig, bare.split()))):
        return T_PREFIX, 0.5 + 0.5 * min(1.0, len(q) / max(1, len(name_n)))

    word_lists = [name_words] + [a.split() for a in alias_ns]
    compacts = [name_n.replace(" ", "")] + [a.replace(" ", "") for a in alias_ns]
    addr_words = normalize(c.get("address")).split()
    covering = _intent_match(c, query.covering)
    kinds, quals, intent_q = [], [], 0
    for t in query.sig:
        tok_intent = query.token_intents.get(t)
        m = _token_match(t, word_lists, compacts, bool(tok_intent))
        if m:
            kinds.append(m[0])
            quals.append(m[1])
            continue
        im = covering or _intent_match(c, tok_intent)
        if im:
            kinds.append("intent")
            intent_q = max(intent_q, im)
        elif any(w.startswith(t) for w in addr_words) or (len(t) >= 4 and t in " ".join(addr_words)):
            kinds.append("addr")
        else:
            return None
    named = [k for k in kinds if k in ("prefix", "fuzzy", "sub")]
    if not named:
        if "intent" in kinds:
            return T_INTENT, 1.0 if intent_q == 2 else 0.6
        return T_WEAK, 0.3
    sim = min(1.0, sum(quals) / len(query.sig))
    if all(k == "prefix" for k in named):
        return T_WORD, sim
    return T_FUZZY, sim


def rank(query, candidates, near=None, now=None, open_now_only=False, is_open_fn=None):
    """Order candidates best first. `query` is a str or a parsed Query. Each candidate is a dict
    with name/name_norm/aliases/category/subcategory/address/kind/lat/lng/popularity/hours.
    Adds `distance_m` (when near), `open_now` (when hours known), `_tier` and `_score`."""
    if not isinstance(query, Query):
        query = parse_query(query)
    if is_open_fn is None:
        is_open_fn = _is_open
    now = now or datetime.now(LA)
    if query.short:
        w_text, w_prox, w_pop = 0.15, 0.50, 0.35
    else:
        w_text, w_prox, w_pop = 0.30, 0.55, 0.15

    out = []
    for c in candidates:
        m = classify(query, c)
        if m is None:
            continue
        tier, sim = m
        r = dict(c)
        if near and r.get("lat") is not None and r.get("lng") is not None:
            d = r.get("distance_m")
            if d is None:
                d = haversine_m(near[0], near[1], r["lat"], r["lng"])
            r["distance_m"] = int(round(d))
            prox = 1.0 / (1.0 + d / D0_M)
        else:
            r.pop("distance_m", None)
            prox = NEUTRAL_PROXIMITY
        hours = r.get("hours")
        r["open_now"] = is_open_fn(hours, now) if hours else None
        if open_now_only and r["open_now"] is not True:
            continue
        score = w_text * sim + w_prox * prox + w_pop * float(r.get("popularity") or 0.0)
        if r.get("kind") == "building":
            score += BUILDING_BOOST
        if tier == T_PREFIX:
            score += FULL_PREFIX_BONUS
        r["_tier"], r["_score"] = tier, score
        out.append(r)
    out.sort(key=lambda r: (TIER_BAND[r["_tier"]], -r["_score"], r.get("distance_m") or 0, r.get("name") or ""))
    return out


def _is_open(hours, now):
    try:
        from .opening_hours import is_open
    except ImportError:
        return None
    return is_open(hours, now)


# ---------------------------------------------------------------- shaping

RESULT_FIELDS = ("id", "kind", "name", "category", "subcategory", "address", "lat", "lng",
                 "distance_m", "rating", "review_count", "open_now")


def to_result(r):
    out = {k: r.get(k) for k in RESULT_FIELDS if k in r}
    if out.get("open_now") is None:
        out.pop("open_now", None)
    return out


def search(q=None, near=None, bbox=None, categories=None, open_now=False, limit=20, now=None):
    query = parse_query(q)
    rows = fetch_candidates(query, near, bbox, categories, candidate_cap(limit, open_now))
    ranked = rank(query, rows, near, now, open_now_only=open_now)
    return [to_result(r) for r in ranked[:limit]]


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _hhmm(minutes):
    minutes = int(minutes) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def hours_by_weekday(hours):
    """{"0": [[480, 1320]], ...} (Monday=0, local minutes, close may pass 1440) ->
    {"monday": [{"open": "08:00", "close": "22:00"}], ...}; a day with no intervals is closed ([]).
    Intervals that end after midnight get "overnight": true; round-the-clock ones "all_day": true."""
    if not hours:
        return None
    out = {}
    for i, day in enumerate(WEEKDAYS):
        spans = []
        for o, c in hours.get(str(i)) or hours.get(i) or []:
            span = {"open": _hhmm(o), "close": _hhmm(c)}
            if c >= 1440:
                span["overnight"] = True
            if o <= 0 and c - o >= 1440:
                span["all_day"] = True
            spans.append(span)
        out[day] = spans
    return out


# ---------------------------------------------------------------- categories, detail, reverse geocode

def _type_label(slug):
    return (slug or "").replace("_", " ").capitalize()


def category_tree(rows=None):
    """[{"id","label","count","types":[{"id","label","count"}]}] in taxonomy order, empty ones omitted."""
    from .place_taxonomy import CATEGORIES

    if rows is None:
        rows = fetch_all("""
            SELECT category, subcategory, count(*)::int AS count,
                   mode() WITHIN GROUP (ORDER BY categories[1]) AS label
            FROM places WHERE category IS NOT NULL
            GROUP BY category, subcategory""")
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    out = []
    known = [c["id"] for c in CATEGORIES]
    labels = {c["id"]: c["label"] for c in CATEGORIES}
    for cid in known + sorted(set(by_cat) - set(known)):
        group = by_cat.get(cid)
        if not group:
            continue
        types = [{"id": r["subcategory"],
                  "label": (r.get("label") if r.get("label") and normalize(r["label"]).replace(" ", "_") == r["subcategory"]
                            else _type_label(r["subcategory"])),
                  "count": r["count"]}
                 for r in group if r.get("subcategory")]
        types.sort(key=lambda t: (-t["count"], t["label"]))
        out.append({"id": cid, "label": labels.get(cid, _type_label(cid)),
                    "count": sum(r["count"] for r in group), "types": types})
    return out


DETAIL_KEYS = ("phone", "website", "photo_url", "rating_breakdown", "attributes", "description",
               "reviews", "plus_code")


def place_detail(place_id, near=None, now=None):
    rows = fetch_all("""
        SELECT id, kind, name, aliases, category, subcategory, categories, address, lat, lng,
               rating, review_count, hours, detail, source
        FROM places WHERE id = :id""", id=place_id)
    if not rows:
        return None
    r = rows[0]
    detail = r.pop("detail") or {}
    hours = r.pop("hours")
    out = {k: r.get(k) for k in ("id", "kind", "name", "category", "subcategory", "address", "lat", "lng",
                                 "rating", "review_count")}
    if near and r.get("lat") is not None:
        out["distance_m"] = int(round(haversine_m(near[0], near[1], r["lat"], r["lng"])))
    open_now = _is_open(hours, now or datetime.now(LA)) if hours else None
    if open_now is not None:
        out["open_now"] = open_now
    out["hours"] = hours_by_weekday(hours)
    out["hours_text"] = detail.get("hours_text")
    out.update({k: detail.get(k) for k in DETAIL_KEYS})
    out["aliases"] = r.get("aliases") or []
    out["categories"] = r.get("categories") or []
    out["source"] = r.get("source")
    return out


REVERSE_RADIUS_M = 60.0


def _road_name(name):
    """osmnx stores multi-name edges as a stringified list: "['A St', 'B St']" -> "A St"."""
    if not name:
        return None
    s = str(name).strip()
    if s.startswith("["):
        s = s.strip("[]").split(",")[0].strip().strip("'\"")
    return s or None


def reverse_geocode(lat, lng):
    """Nearest named place within ~60 m (buildings preferred), else the nearest named road."""
    pt = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)"
    # ~0.0008 deg latitude is ~89 m; the box is a cheap GiST prefilter, the exact cut is in metres
    rows = fetch_all(f"""
        SELECT id, kind, name, address, ST_DistanceSphere(geom, {pt}) AS distance_m
        FROM places
        WHERE geom && ST_Expand({pt}, 0.0009) AND name IS NOT NULL
          AND ST_DistanceSphere(geom, {pt}) <= :radius
        ORDER BY (kind = 'building') DESC, geom <-> {pt}
        LIMIT 1""", lat=lat, lng=lng, radius=REVERSE_RADIUS_M)
    if rows:
        r = rows[0]
        return {"name": r["name"], "address": r["address"], "id": r["id"], "kind": r["kind"],
                "distance_m": int(round(r["distance_m"]))}
    rows = fetch_all(f"""
        SELECT name, ST_DistanceSphere(ST_ClosestPoint(geom, {pt}), {pt}) AS distance_m
        FROM road_edges
        WHERE name IS NOT NULL AND name <> '' AND geom && ST_Expand({pt}, 0.003)
        ORDER BY geom <-> {pt}
        LIMIT 1""", lat=lat, lng=lng)
    if rows and _road_name(rows[0]["name"]):
        return {"name": _road_name(rows[0]["name"]), "address": None, "id": None, "kind": "road",
                "distance_m": int(round(rows[0]["distance_m"]))}
    return None
