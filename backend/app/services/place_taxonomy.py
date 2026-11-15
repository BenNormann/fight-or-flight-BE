"""Place categories for search: map raw Google Maps type labels ("Chicken restaurant",
"Parking garage", ...) to a small set of top-level categories, and map what a user types
("coffee", "pizza", "atm") to the categories / subcategories it implies.

Pure Python, no DB. Used by scripts/load_places.py (writes places.category/subcategory) and
by the search query path (CATEGORIES for the facet list, category_intent for ranking).
"""
import re

from .text_norm import normalize

# Ordered for display. "other" is the fallback and is always last.
CATEGORIES = [
    {"id": "food", "label": "Food"},
    {"id": "cafe", "label": "Coffee & Tea"},
    {"id": "bar", "label": "Bars & Nightlife"},
    {"id": "grocery", "label": "Grocery & Convenience"},
    {"id": "shopping", "label": "Shopping"},
    {"id": "study", "label": "Study Spots & Libraries"},
    {"id": "campus", "label": "Campus Buildings"},
    {"id": "education", "label": "Schools"},
    {"id": "health", "label": "Health & Pharmacy"},
    {"id": "fitness", "label": "Fitness & Sports"},
    {"id": "outdoors", "label": "Parks & Outdoors"},
    {"id": "entertainment", "label": "Arts & Entertainment"},
    {"id": "services", "label": "Services, ATMs & Banks"},
    {"id": "parking", "label": "Parking"},
    {"id": "transit", "label": "Transit"},
    {"id": "lodging", "label": "Housing & Hotels"},
    {"id": "safety", "label": "Police & Safety"},
    {"id": "community", "label": "Worship & Community"},
    {"id": "other", "label": "Other"},
]
CATEGORY_IDS = [c["id"] for c in CATEGORIES]

# Exact label -> category. Checked before the keyword rules; for labels a keyword would get wrong.
OVERRIDES = {
    "bar & grill": "food", "snack bar": "food", "poke bar": "food", "oyster bar restaurant": "food",
    "sushi takeaway": "food", "cafeteria": "food", "cha chaan teng (hong kong-style cafe)": "food",
    "food court": "food", "dance restaurant": "food", "box lunch supplier": "food",
    "espresso bar": "cafe", "eyebrow bar": "services", "internet cafe": "cafe", "cat cafe": "cafe",
    "coffee vending machine": "cafe",
    "food bank": "community", "homeless service": "community",
    "health food store": "grocery", "organic food store": "grocery", "natural goods store": "grocery",
    "market": "grocery", "night market": "grocery", "farmers' market": "grocery", "store": "shopping",
    "stores and shopping": "shopping", "general store": "grocery", "variety store": "shopping",
    "student housing center": "lodging", "student dormitory": "lodging",
    "coworking space": "study", "business center": "study", "archive": "study",
    "recreation center": "fitness", "athletic park": "fitness", "sports complex": "fitness",
    "gas station": "services", "alternative fuel station": "services",
    "compressed natural gas station": "services", "truck stop": "services",
    "criminal court": "community", "public bathroom": "services", "bicycle rack": "parking",
    "park & ride": "transit", "transportation service": "transit",
    "amusement park": "entertainment", "water park": "entertainment", "amusement park ride": "entertainment",
    "business park": "other", "mobile home park": "lodging", "rv park": "lodging",
    "memorial park": "community", "cemetery": "community", "funeral home": "community",
    "pool hall": "entertainment", "beer garden": "bar", "community garden": "outdoors",
    "plaza": "outdoors", "fountain": "outdoors", "bridge": "outdoors", "sculpture": "entertainment",
    "painting": "entertainment", "castle": "entertainment", "memorial": "entertainment",
    "security guard service": "services", "veterinarian": "health", "animal hospital": "health",
    "vitamin & supplements store": "health", "sports nutrition store": "health",
    "herbal medicine store": "health", "medical supply store": "health", "medical book store": "shopping",
    "law book store": "shopping", "law library": "study", "dental school": "education",
    "beauty school": "education", "barber school": "education", "police academy": "education",
    "barber supply store": "shopping", "beauty supply store": "shopping",
    "coin operated laundry equipment supplier": "services", "portable toilet supplier": "services",
    "valet parking service": "parking", "automobile storage facility": "parking",
    "ice cream shop": "food", "frozen yogurt shop": "food", "juice shop": "cafe", "smoothie shop": "cafe",
    "health": "health", "building": "other", "house": "lodging", "club": "bar", "social club": "community",
    "country club": "fitness", "golf club": "fitness", "soccer club": "fitness", "sports club": "fitness",
    "martial arts club": "fitness", "adult entertainment club": "bar", "adult entertainment store": "shopping",
    "fraternal organization": "community", "makerspace": "study", "photo booth": "services",
    "event venue": "entertainment", "banquet hall": "entertainment", "convention center": "entertainment",
    "visitor center": "entertainment", "festival": "entertainment", "karaoke": "bar",
    "cannabis store": "shopping", "tobacco shop": "shopping", "smoke shop": "shopping",
    "dumpster rental service": "services", "food manufacturer": "other", "food producer": "other",
    "food processing company": "other", "frozen food manufacturer": "other",
    "food manufacturing supply": "other", "catering food and drink supplier": "other",
    "coffee wholesaler": "other", "books wholesaler": "other", "alcoholic beverage wholesaler": "other",
    "meeting planning service": "services", "personal chef service": "services",
    "art school": "education", "art supply store": "shopping", "baseball goods store": "shopping",
    "educational supply store": "shopping", "office supply store": "shopping",
}

# (category, keywords) in priority order; a keyword matches whole words of the lowercased label.
RULES = [
    ("parking", ["parking", "valet", "garage"]),
    ("transit", ["bus stop", "bus station", "bus depot", "light rail", "subway", "train", "transit",
                 "metro", "transport hub", "railroad"]),
    ("safety", ["police", "sheriff", "fire station", "highway patrol"]),
    ("cafe", ["coffee", "cafe", "café", "espresso", "bubble tea", "boba", "tea house", "tea store",
              "teahouse", "açaí", "acai"]),
    ("grocery", ["grocery", "supermarket", "convenience store", "liquor store", "butcher", "seafood market",
                 "produce market", "fruit and vegetable", "beer store", "wine store", "dairy store",
                 "meat products", "cheese shop", "dry fruit", "food store", "kosher grocery", "deli market"]),
    ("food", ["restaurant", "bakery", "fast food", "pizza", "donut", "dessert", "deli", "diner", "buffet",
              "bistro", "creperie", "cookie", "cake shop", "pastry", "patisserie", "cupcake", "bagel",
              "sandwich", "taco", "burrito", "sushi", "ramen", "noodle", "dumpling", "barbecue", "steak house",
              "grill", "gastropub", "caterer", "mobile caterer", "takeout", "takeaway", "chicken shop",
              "hot dog", "churreria", "pie shop", "chocolate shop", "confectionery", "sweets", "tamale",
              "salad shop", "kebab", "shawarma", "falafel", "pasta shop", "pancake", "brunch", "breakfast",
              "lunch", "wok", "sundae", "izakaya", "cheesesteak", "fish & chips", "food", "pizza delivery"]),
    ("bar", ["bar", "pub", "brewery", "brewpub", "night club", "lounge", "winery", "jazz club", "hookah",
             "tiki", "karaoke"]),
    ("study", ["library", "coworking", "study"]),
    ("health", ["pharmacy", "drug store", "dentist", "dental", "clinic", "hospital", "doctor", "physician",
                "medical", "urgent care", "health center", "orthodontist", "periodontist", "endodontist",
                "prosthodontist", "oral surgeon", "ophthalmologist", "cardiologist", "oncologist", "internist",
                "anesthesiologist", "practitioner", "acupuncturist", "mental health", "physical therapy",
                "wellness", "addiction", "family planning", "denture", "emergency room", "health service",
                "laboratory"]),
    ("fitness", ["gym", "fitness", "yoga", "pilates", "personal trainer", "climbing", "boxing", "martial arts",
                 "swimming", "pool", "tennis", "basketball", "soccer", "baseball", "futsal", "pickleball",
                 "athletic", "gymnastics", "golf", "archery", "field", "court", "stadium", "arena"]),
    ("outdoors", ["park", "playground", "hiking", "garden", "lake", "nature preserve", "scenic spot",
                  "wetland", "ravine", "reserve", "skatepark", "state park"]),
    ("entertainment", ["museum", "gallery", "theater", "theatre", "cinema", "movie", "imax", "tourist attraction",
                       "landmark", "historical", "performing arts", "bowling", "amusement", "escape room",
                       "haunted house", "zoo", "live music", "music venue", "concert", "comedy", "arts organization",
                       "art center", "cultural center", "dance hall", "studio", "art", "artist"]),
    ("lodging", ["hotel", "motel", "hostel", "inn", "bed & breakfast", "lodging", "apartment", "housing",
                 "dormitory", "condominium", "cottage", "gated community", "assisted living", "resort"]),
    ("education", ["school", "university", "college", "academic", "educational", "preschool", "kindergarten",
                   "education", "learning center", "research institute", "day care", "child care", "montessori",
                   "after school", "tutoring"]),
    ("community", ["church", "temple", "mosque", "synagogue", "chapel", "cathedral", "worship", "religious",
                   "mission", "non-profit", "organization", "charity", "community center", "senior citizen",
                   "government", "city hall", "social services", "volunteer", "housing authority"]),
    ("services", ["atm", "bank", "credit union", "laundromat", "laundry", "cleaners", "dry cleaner", "barber",
                  "salon", "hair", "hairdresser", "beauty", "nail", "spa", "massage", "post office", "shipping",
                  "mailing", "check cashing", "loan", "mortgage", "notary", "money transfer", "car wash",
                  "auto repair", "oil change", "copy shop", "cleaning", "repair", "alteration", "photo",
                  "eyelash", "accountant", "insurance", "investment", "property management", "agency",
                  "service", "consultant", "company", "office", "contractor", "dressmaker", "private investigator",
                  "publisher", "dealer", "rental", "leasing"]),
    ("shopping", ["store", "shop", "boutique", "mall", "outlet", "wholesaler", "market place", "florist",
                  "wholesale", "market"]),
]

_RULES = [(cat, re.compile(r"(?<![\w])(?:" + "|".join(re.escape(k) for k in sorted(kws, key=len, reverse=True))
                           + r")(?![\w])")) for cat, kws in RULES]


def slug(label) -> str | None:
    """'Chicken restaurant' -> 'chicken_restaurant'; "Women's clothing store" -> 'womens_clothing_store'."""
    s = normalize(label).replace(" ", "_")
    return s or None


def categorize_label(label: str) -> str:
    """Top-level category of one raw Google label ('other' if nothing matches)."""
    low = (label or "").strip().lower()
    if not low:
        return "other"
    if low in OVERRIDES:
        return OVERRIDES[low]
    for cat, rx in _RULES:
        if rx.search(low):
            return cat
    return "other"


def categorize(labels) -> tuple[str, str | None]:
    """(category id, subcategory slug) for a place's raw Google labels.

    The category comes from the first label that maps to something other than 'other';
    the subcategory is always the slug of the primary (first) label."""
    labels = [x for x in (labels or []) if x and str(x).strip()]
    if not labels:
        return "other", None
    cat = "other"
    for lab in labels:
        cat = categorize_label(lab)
        if cat != "other":
            break
    return cat, slug(labels[0])


# ---- query intent -----------------------------------------------------------------------------

def _c(category, subcategory=None):
    return {"category": category, "subcategory": subcategory}


# Normalized query term (one or more words, as text_norm.normalize would produce) -> intents.
QUERY_SYNONYMS: dict[str, list[dict]] = {
    # food
    "food": [_c("food")], "eat": [_c("food")], "eats": [_c("food")], "restaurant": [_c("food")],
    "restaurants": [_c("food")], "lunch": [_c("food")], "dinner": [_c("food")], "breakfast": [_c("food")],
    "brunch": [_c("food")], "meal": [_c("food")], "hungry": [_c("food")], "takeout": [_c("food")],
    "fast food": [_c("food", "fast_food_restaurant")],
    "pizza": [_c("food", "pizza_restaurant")], "burger": [_c("food", "hamburger_restaurant")],
    "burgers": [_c("food", "hamburger_restaurant")], "hamburger": [_c("food", "hamburger_restaurant")],
    "taco": [_c("food", "taco_restaurant")], "tacos": [_c("food", "taco_restaurant")],
    "mexican": [_c("food", "mexican_restaurant")], "chinese": [_c("food", "chinese_restaurant")],
    "thai": [_c("food", "thai_restaurant")], "japanese": [_c("food", "japanese_restaurant")],
    "korean": [_c("food", "korean_restaurant")], "italian": [_c("food", "italian_restaurant")],
    "indian": [_c("food", "indian_restaurant")], "vietnamese": [_c("food", "vietnamese_restaurant")],
    "pho": [_c("food", "vietnamese_restaurant")], "sushi": [_c("food", "sushi_restaurant")],
    "ramen": [_c("food", "ramen_restaurant")], "chicken": [_c("food", "chicken_restaurant")],
    "wings": [_c("food", "chicken_wings_restaurant")], "seafood": [_c("food", "seafood_restaurant")],
    "bbq": [_c("food", "barbecue_restaurant")], "barbecue": [_c("food", "barbecue_restaurant")],
    "vegan": [_c("food", "vegan_restaurant")], "vegetarian": [_c("food", "vegetarian_restaurant")],
    "sandwich": [_c("food", "sandwich_shop")], "sandwiches": [_c("food", "sandwich_shop")],
    "bakery": [_c("food", "bakery")], "donut": [_c("food", "donut_shop")], "donuts": [_c("food", "donut_shop")],
    "dessert": [_c("food", "dessert_shop")], "ice cream": [_c("food", "ice_cream_shop")],
    "froyo": [_c("food", "frozen_yogurt_shop")], "deli": [_c("food", "deli")],
    "dining hall": [_c("food")],
    # cafe
    "coffee": [_c("cafe")], "cafe": [_c("cafe")], "espresso": [_c("cafe")], "latte": [_c("cafe")],
    "tea": [_c("cafe")], "boba": [_c("cafe", "bubble_tea_store")], "bubble tea": [_c("cafe", "bubble_tea_store")],
    "milk tea": [_c("cafe", "bubble_tea_store")], "juice": [_c("cafe", "juice_shop")],
    "smoothie": [_c("cafe", "juice_shop")], "starbucks": [_c("cafe")],
    # bar
    "bar": [_c("bar")], "bars": [_c("bar")], "pub": [_c("bar")], "drinks": [_c("bar")], "beer": [_c("bar")],
    "cocktail": [_c("bar", "cocktail_bar")], "cocktails": [_c("bar", "cocktail_bar")],
    "nightlife": [_c("bar")], "club": [_c("bar", "night_club")], "nightclub": [_c("bar", "night_club")],
    "brewery": [_c("bar", "brewery")],
    # grocery
    "grocery": [_c("grocery")], "groceries": [_c("grocery")], "supermarket": [_c("grocery")],
    "market": [_c("grocery")], "convenience store": [_c("grocery", "convenience_store")],
    "liquor": [_c("grocery", "liquor_store")], "liquor store": [_c("grocery", "liquor_store")],
    "snacks": [_c("grocery", "convenience_store")], "trader joes": [_c("grocery")],
    "7 eleven": [_c("grocery", "convenience_store")],
    # shopping
    "shopping": [_c("shopping")], "shop": [_c("shopping")], "store": [_c("shopping")], "mall": [_c("shopping")],
    "clothes": [_c("shopping")], "clothing": [_c("shopping")], "bookstore": [_c("shopping", "book_store")],
    "books": [_c("shopping", "book_store")], "target": [_c("shopping")],
    # study
    "study": [_c("study")], "studying": [_c("study")], "study spot": [_c("study")], "library": [_c("study")],
    "libraries": [_c("study")], "coworking": [_c("study")], "quiet": [_c("study")],
    # campus
    "building": [_c("campus")], "hall": [_c("campus")], "classroom": [_c("campus")],
    "lecture hall": [_c("campus")], "dorm": [_c("lodging")], "dorms": [_c("lodging")],
    "residence hall": [_c("lodging")], "housing": [_c("lodging")], "apartment": [_c("lodging")],
    "apartments": [_c("lodging")], "hotel": [_c("lodging")], "motel": [_c("lodging")],
    # education
    "school": [_c("education")], "university": [_c("education")], "college": [_c("education")],
    # health
    "pharmacy": [_c("health", "pharmacy")], "drugstore": [_c("health", "pharmacy")],
    "cvs": [_c("health", "pharmacy")], "walgreens": [_c("health", "pharmacy")],
    "rite aid": [_c("health", "pharmacy")], "medicine": [_c("health")], "doctor": [_c("health")],
    "clinic": [_c("health")], "hospital": [_c("health")], "urgent care": [_c("health", "urgent_care_center")],
    "emergency room": [_c("health")], "dentist": [_c("health", "dentist")],
    "health": [_c("health")], "student health": [_c("health")],
    # fitness
    "gym": [_c("fitness")], "workout": [_c("fitness")], "fitness": [_c("fitness")], "yoga": [_c("fitness")],
    "pilates": [_c("fitness")], "pool": [_c("fitness")], "swim": [_c("fitness")], "tennis": [_c("fitness")],
    "basketball": [_c("fitness")], "rec center": [_c("fitness")], "lyon center": [_c("fitness")],
    # outdoors
    "park": [_c("outdoors")], "parks": [_c("outdoors")], "playground": [_c("outdoors")],
    "hike": [_c("outdoors")], "hiking": [_c("outdoors")], "garden": [_c("outdoors")],
    "dog park": [_c("outdoors", "dog_park")], "outdoor seating": [_c("outdoors")], "seating": [_c("outdoors")],
    "tables": [_c("outdoors")],
    # entertainment
    "museum": [_c("entertainment")], "movie": [_c("entertainment", "movie_theater")],
    "movies": [_c("entertainment", "movie_theater")], "cinema": [_c("entertainment", "movie_theater")],
    "theater": [_c("entertainment")], "theatre": [_c("entertainment")], "art": [_c("entertainment")],
    "gallery": [_c("entertainment")], "bowling": [_c("entertainment")], "concert": [_c("entertainment")],
    "stadium": [_c("entertainment")],
    # services
    "atm": [_c("services", "atm")], "bank": [_c("services")], "cash": [_c("services", "atm")],
    "laundry": [_c("services")], "laundromat": [_c("services", "laundromat")],
    "haircut": [_c("services")], "barber": [_c("services", "barber_shop")], "salon": [_c("services")],
    "nails": [_c("services")], "post office": [_c("services", "post_office")], "mail": [_c("services")],
    "gas": [_c("services", "gas_station")], "gas station": [_c("services", "gas_station")],
    "bathroom": [_c("services", "public_bathroom")], "bathrooms": [_c("services", "public_bathroom")],
    "restroom": [_c("services", "public_bathroom")], "restrooms": [_c("services", "public_bathroom")],
    "toilet": [_c("services", "public_bathroom")],
    # parking / transit
    "parking": [_c("parking")], "parking lot": [_c("parking")], "garage": [_c("parking")],
    "parking structure": [_c("parking")], "bike rack": [_c("parking", "bicycle_rack")],
    "bus": [_c("transit")], "bus stop": [_c("transit", "bus_stop")], "metro": [_c("transit")],
    "train": [_c("transit")], "light rail": [_c("transit")], "transit": [_c("transit")],
    "station": [_c("transit")],
    # safety / community
    "police": [_c("safety")], "dps": [_c("safety")], "safety": [_c("safety")], "fire station": [_c("safety")],
    "church": [_c("community")], "worship": [_c("community")], "temple": [_c("community")],
}

# Words too generic to trigger an intent by prefix while being typed.
_NO_PREFIX = {"the", "and", "for", "near", "with", "from", "out", "open", "best", "new", "los", "las",
              "san", "via", "usc", "now"}
_MIN_PREFIX = 3


def category_intent(q_norm: str) -> list[dict]:
    """Categories/subcategories a normalized query implies, e.g. 'coffee near vkc' -> [{'category':
    'cafe', 'subcategory': None}]. Whole-word synonym phrases anywhere in the query count; the last
    1-3 words also count as a prefix of a synonym ('coff' -> cafe, 'ice cr' -> ice cream) when that
    tail is at least 3 characters long.
    Empty list when the query has no category intent."""
    toks = (q_norm or "").split()
    if not toks:
        return []
    out: list[dict] = []

    def add(intents):
        for it in intents:
            if it not in out:
                out.append(it)

    # Exact phrases (up to 3 words) anywhere in the query.
    for n in (3, 2, 1):
        for i in range(len(toks) - n + 1):
            phrase = " ".join(toks[i:i + n])
            if phrase in QUERY_SYNONYMS:
                add(QUERY_SYNONYMS[phrase])
    # Prefix of a synonym at the end of the query (the word still being typed).
    last = toks[-1]
    if last not in _NO_PREFIX and last not in QUERY_SYNONYMS:
        for n in (1, 2, 3):
            if n > len(toks):
                break
            tail = " ".join(toks[-n:])
            if len(tail) < _MIN_PREFIX:
                continue
            for key, intents in QUERY_SYNONYMS.items():
                if key != tail and key.startswith(tail):
                    add(intents)
    return out
