def test_openapi_spec_lists_routes(client):
    spec = client.get("/api/openapi.json").get_json()
    paths = set(spec["paths"])
    for p in ("/api/v1/health", "/api/v1/cells", "/api/v1/observations", "/api/v1/observations/latest",
              "/api/v1/incidents", "/api/v1/features", "/api/v1/buildings.pmtiles", "/api/v1/roads", "/api/v1/roads/graph", "/api/v1/pois", "/api/v1/pois/types", "/api/v1/sources",
              "/api/v1/admin/ingest/{source}"):
        assert p in paths, p


def test_bad_bbox_rejected(client):
    resp = client.get("/api/v1/cells?bbox=1,2,3")
    assert resp.status_code == 422
