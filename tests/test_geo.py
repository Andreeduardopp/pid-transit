"""Tests for geo helpers, especially the StopGrid spatial index that replaces
the O(n·m) proximity scan in stop deduplication."""

import random

from pid_transit.core.geo import haversine, StopGrid


def _brute_nearest(qlat, qlon, targets, thr):
    """Reference O(m) scan: nearest target strictly within threshold."""
    best_id, best = None, thr
    for pid, lat, lon in targets:
        d = haversine(qlat, qlon, lat, lon)
        if d < best:
            best, best_id = d, pid
    return best_id


def test_grid_matches_bruteforce_on_random_points():
    rng = random.Random(1234)
    # ~2 km-wide cluster of targets around a mid-latitude city.
    targets = [(f"T{i}", 47.37 + rng.uniform(-0.01, 0.01),
                8.54 + rng.uniform(-0.01, 0.01)) for i in range(500)]
    grid = StopGrid(iter(targets), cell_m=50)

    mismatches = 0
    for _ in range(500):
        qlat = 47.37 + rng.uniform(-0.011, 0.011)
        qlon = 8.54 + rng.uniform(-0.011, 0.011)
        expected = _brute_nearest(qlat, qlon, targets, 50)
        hit = grid.nearest(qlat, qlon, 50)
        got = hit[0] if hit else None
        if got != expected:
            # Tolerate only genuine near-threshold ambiguity: same distance.
            mismatches += 1
    assert mismatches == 0


def test_grid_finds_close_and_rejects_far():
    grid = StopGrid([("A", 40.0, -74.0), ("B", 41.0, -73.0)], cell_m=50)
    # 40.00001,-74.00001 is ~1.4 m from A.
    hit = grid.nearest(40.00001, -74.00001, 50)
    assert hit is not None and hit[0] == "A" and hit[1] < 5
    # A point 300 m away from any indexed stop matches nothing at 50 m.
    assert grid.nearest(40.003, -74.0, 50) is None


def test_grid_picks_nearest_among_several():
    grid = StopGrid([
        ("near", 40.00002, -74.0),   # ~2.2 m
        ("far", 40.0003, -74.0),     # ~33 m
    ], cell_m=50)
    hit = grid.nearest(40.0, -74.0, 50)
    assert hit[0] == "near"


def test_grid_ignores_missing_coordinates():
    # Indexed points with None coords are dropped; None queries return None.
    grid = StopGrid([("A", None, None), ("B", 40.0, -74.0)], cell_m=50)
    assert grid.nearest(None, None, 50) is None
    assert grid.nearest(40.00001, -74.00001, 50)[0] == "B"


def test_empty_grid_is_safe():
    grid = StopGrid([], cell_m=50)
    assert grid.nearest(40.0, -74.0, 50) is None
    assert grid.within(40.0, -74.0, 50) == []


def test_within_matches_bruteforce_on_random_points():
    rng = random.Random(99)
    targets = [(f"T{i}", 47.37 + rng.uniform(-0.005, 0.005),
                8.54 + rng.uniform(-0.005, 0.005)) for i in range(400)]
    grid = StopGrid(iter(targets), cell_m=50)

    for _ in range(200):
        qlat = 47.37 + rng.uniform(-0.006, 0.006)
        qlon = 8.54 + rng.uniform(-0.006, 0.006)
        expected = {pid for pid, lat, lon in targets
                    if haversine(qlat, qlon, lat, lon) <= 50}
        got = {pid for pid, _ in grid.within(qlat, qlon, 50)}
        assert got == expected


def test_within_is_inclusive_and_bounded_by_cell():
    import pytest

    grid = StopGrid([("A", 40.0, -74.0)], cell_m=50)
    # Query at A itself: distance 0 is included.
    assert [pid for pid, _ in grid.within(40.0, -74.0, 50)] == ["A"]
    # Radius above the cell size breaks the 3x3 coverage guarantee.
    with pytest.raises(ValueError):
        grid.within(40.0, -74.0, 51)
