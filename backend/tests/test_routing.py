import pytest

from app.services.routing import explode_edges


def test_explode_merges_shared_vertices_and_dedupes():
    a = [[-118.2850, 34.0200], [-118.2840, 34.0200], [-118.2830, 34.0200]]   # 3 vertices, 2 segments
    b = [[-118.2830, 34.0200], [-118.2830, 34.0210]]                         # shares a's last vertex
    dup = [[-118.2840, 34.0200], [-118.2850, 34.0200]]                       # a's first segment, reversed
    zero = [[-118.2800, 34.0300], [-118.2800, 34.0300]]                      # zero-length
    lat, lon, u, v = explode_edges([a, b, dup, zero])
    assert len(lat) == 5          # 3 + 1 new + zero-length point
    assert len(u) == 3            # a: 2 segments, b: 1, dup and zero add nothing
    assert all(x < y for x, y in zip(u, v))


def test_route_rejects_bad_points(client):
    assert client.get("/api/v1/route?from=34.02&to=34.03,-118.28").status_code == 422
    resp = client.get("/api/v1/route?from=34.0224,-118.2851&to=34.30,-118.60")   # ~40 km
    assert resp.status_code == 400 and "limit" in resp.get_json()["message"]


def _ladder_graph():
    """Two parallel east-west streets joined at both ends: south street (lat 34.0200) and
    north street (34.0206). Walking from west to east, both are about the same length."""
    from pathfinding.graph import WalkGraph

    west, east, n = -118.2900, -118.2800, 20
    xs = [west + (east - west) * i / n for i in range(n + 1)]
    south = [[x, 34.0200] for x in xs]
    north = [[x, 34.0206] for x in xs]
    lat, lon, u, v = explode_edges([south, north, [[west, 34.0200], [west, 34.0206]],
                                    [[east, 34.0200], [east, 34.0206]]])
    return WalkGraph(lat, lon, u, v)


class FakeRisk:
    """South street risky (0.9), north street safe (0.1)."""
    date, res, no_data_risk, step_m = "2026-11-14", 9, 0.5, 60.0

    def edge_risk(self, lat1, lon1, lat2, lon2, length_m, hour, step_m=None):
        return (0.9 if (lat1 + lat2) / 2 < 34.0203 else 0.1), 0.0


def test_planner_avoids_risky_street_on_backend_graph():
    pytest.importorskip("algorithm.common.geo")   # risk-model package (mounted as `algorithm` in the image)
    from pathfinding.planner import plan_route

    g = _ladder_graph()
    start, end = (34.0200, -118.2900), (34.0200, -118.2800)   # both on the risky south street
    res = plan_route(start, end, hour=22, max_detour=0.3, graph=g, risk=FakeRisk())
    safe, shortest = res["safe"], res["shortest"]
    assert shortest.mean_risk > 0.8                       # straight along the south street
    assert safe.mean_risk < shortest.mean_risk            # safe route uses the north street
    assert safe.length_m <= shortest.length_m * 1.3 + 1   # within the detour budget
