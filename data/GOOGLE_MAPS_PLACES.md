# USC Google Maps places

`usc_google_maps_places.geojson` has 15,343 places (one Point each; WGS84 [lng, lat]) around USC (34.0224, -118.2851), scraped from Google Maps on 2026-11-14. About 23 MB.

Two tiers, set by the `detail` property:

- `full` (8,507 places, within 5 miles of USC): 47 business types (restaurants, cafes, bars, gyms, museums, libraries, schools, hospitals, banks, hotels, shops, salons, transit, parking and more). Properties: `name`, `categories`, `address`, `phone`, `website`, `rating`, `review_count`, `rating_breakdown` (1-5 star counts), `reviews` (up to 5 recent review texts, 400 chars max), `attributes` (Google's "about" tags), `open_hours`, `photo_url` (one Google-hosted image link), `description`, `place_id`, `plus_code`.
- `basic` (6,836 places, 5 to 10 miles out): 10 business types only (restaurants, cafes, bars, convenience stores, parks, grocery stores, gas stations, pharmacies, fast food, bakeries); name, categories, address, phone, website, rating, review count, hours. No reviews or photos.

Method: [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) v1.18.2, patched to drop Chromium's `--single-process` flag on Windows (upstream issue #322). `full`: a grid of search centres (1.2 km spacing within 1.5 miles of USC, 2.5 km spacing out to 5 miles) with 47 queries at each, de-duplicated by place id. `basic`: the scraper's fast mode on a 2 km grid.

Limits: each search returns a limited number of results, so dense areas are not exhaustive and types outside the query lists are missing. Reviews are only the few Google shows inline, not full history. `photo_url` is a link to Google's image, not a stored copy, and may expire. Google Maps data is subject to Google's terms; check they allow your use before redistributing.
