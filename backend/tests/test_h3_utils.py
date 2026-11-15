import h3

from shapely.geometry import LineString

from app.services.h3_utils import area_cells, cell_polygon_wkt, in_study_area, line_cells, study_cells


def test_study_area_cell_counts(app):
    cells = study_cells()
    # ~813 km^2 circle / ~0.11-0.12 km^2 per res-9 cell
    assert 6000 < len(cells) < 8500
    assert all(h3.get_resolution(c) == 9 for c in cells)
    parents = area_cells()
    assert 15 < len(parents) < 60
    assert {h3.cell_to_parent(c, 6) for c in cells} == set(parents)


def test_in_study_area(app):
    assert in_study_area(34.0224, -118.2851)       # USC
    assert in_study_area(34.0522, -118.2437)       # downtown LA (~5 km)
    assert not in_study_area(34.1478, -118.1445)   # Pasadena (~20 km)


def test_polygon_wkt_closed(app):
    wkt = cell_polygon_wkt(study_cells()[0])
    coords = wkt[len("POLYGON(("):-2].split(", ")
    assert coords[0] == coords[-1]


def test_line_cells(app):
    # ~1.1 km east-west along Exposition Blvd: crosses several res-9 cells (~370 m across)
    line = LineString([(-118.2900, 34.0185), (-118.2780, 34.0185)])
    cells = line_cells(line, 1100)
    assert 3 <= len(cells) <= 6
    assert len(cells) == len(set(cells))
    assert cells[0] == h3.latlng_to_cell(34.0185, -118.2900, 9)
    assert cells[-1] == h3.latlng_to_cell(34.0185, -118.2780, 9)
