# USC Google Maps places (10 mile radius)

`usc_google_maps_places.geojson` — 9,724 places (one Point each) within 10 miles (16,093 m) of USC (34.0224, -118.2851), scraped from Google Maps on 2026-11-14. Coordinates are WGS84 [lng, lat].

Properties (empty values omitted): `id`, `name`, `categories`, `address`, `phone`, `website`, `rating`, `review_count`, `place_id`, `open_hours`, `status`.

Method: [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) v1.18.2 in `-fast-mode`, run on a 2 km grid of 233 centres (radius 2 km each) covering the circle, with these queries at every centre: restaurants, cafes, bars, convenience stores, parks, grocery stores, gas stations, pharmacies, fast food, bakeries. Results were de-duplicated by name + coordinates and clipped to the 10 mile circle.

Limits: fast mode returns about 20 results per query per centre, so dense areas are not exhaustive and only the categories above are covered. Reviews, photos and owner data were dropped. Google Maps data is subject to Google's terms; check they permit your use before redistributing.
