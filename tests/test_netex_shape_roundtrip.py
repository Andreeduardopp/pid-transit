"""Each trip keeps its own shape through GTFS -> NeTEx -> GTFS.

Found on the Euskotren feed, where 352 of 5,867 trips came back with another
shape id: 346 because one shape served patterns with different stops (a short
working), and 6 because trips with the same stops ran over different shapes.
"""

import pytest

from pid_transit.core.dataset import TransitDataset
from tests.conftest import make_gtfs_zip
from tests.test_roundtrip import _read_csv

STOPS = [("S1", 43.000), ("S2", 43.010), ("S3", 43.020), ("S4", 43.030)]


def _shape(shape_id, lon):
    # Eight points per stop gap, offset in longitude so two shapes over the
    # same stops are distinct geometry.
    pts, seq = [], 0
    for i in range(len(STOPS) - 1):
        lat0, lat1 = STOPS[i][1], STOPS[i + 1][1]
        for k in range(8):
            seq += 1
            pts.append({"shape_id": shape_id, "shape_pt_sequence": str(seq),
                        "shape_pt_lat": f"{lat0 + (lat1 - lat0) * k / 8:.6f}",
                        "shape_pt_lon": f"{lon:.6f}"})
    seq += 1
    pts.append({"shape_id": shape_id, "shape_pt_sequence": str(seq),
                "shape_pt_lat": f"{STOPS[-1][1]:.6f}", "shape_pt_lon": f"{lon:.6f}"})
    return pts


def _trip(trip_id, shape_id, stops):
    return ({"trip_id": trip_id, "route_id": "R1", "service_id": "WK",
             "direction_id": "0", "shape_id": shape_id},
            [{"trip_id": trip_id, "stop_id": s, "stop_sequence": str(i + 1),
              "arrival_time": f"08:{i:02d}:00", "departure_time": f"08:{i:02d}:00"}
             for i, s in enumerate(stops)])


def _records():
    trips, stop_times = [], []
    for t in (
        _trip("FULL", "A", ["S1", "S2", "S3", "S4"]),
        # Same shape A, fewer stops: a different cut of the same polyline.
        _trip("SKIP", "A", ["S1", "S3", "S4"]),
        # Same stops as FULL, another shape: patterns must not be merged.
        _trip("OTHER", "B", ["S1", "S2", "S3", "S4"]),
        _trip("FULL2", "A", ["S1", "S2", "S3", "S4"]),
    ):
        trips.append(t[0])
        stop_times.extend(t[1])
    return {
        "agency": [{"agency_id": "A1", "agency_name": "Rail",
                    "agency_url": "https://example.com",
                    "agency_timezone": "Europe/Madrid"}],
        "stops": [{"stop_id": s, "stop_name": s, "stop_lat": f"{lat}",
                   "stop_lon": "-2.0"} for s, lat in STOPS],
        "routes": [{"route_id": "R1", "agency_id": "A1", "route_short_name": "1",
                    "route_type": "2"}],
        "calendar": [{"service_id": "WK", "monday": "1", "tuesday": "1",
                      "wednesday": "1", "thursday": "1", "friday": "1",
                      "saturday": "0", "sunday": "0",
                      "start_date": "20260101", "end_date": "20261231"}],
        "trips": trips,
        "stop_times": stop_times,
        "shapes": _shape("A", -2.0) + _shape("B", -2.001),
    }


@pytest.fixture
def round_tripped(tmp_path):
    feed = tmp_path / "feed.zip"
    feed.write_bytes(make_gtfs_zip(_records()))
    ds = TransitDataset.from_gtfs(feed, tmp_path / "a.db")
    ds.to_netex(tmp_path / "feed.xml")
    ds.close()
    back = TransitDataset.from_netex(tmp_path / "feed.xml", tmp_path / "b.db")
    back.to_gtfs(tmp_path / "back.zip")
    back.close()
    return tmp_path / "back.zip"


def test_every_trip_keeps_its_shape_id(round_tripped):
    shapes = {r["trip_id"]: r["shape_id"] for r in _read_csv(round_tripped, "trips.txt")}
    assert shapes == {"FULL": "A", "SKIP": "A", "OTHER": "B", "FULL2": "A"}


def test_each_shape_comes_back_once_with_its_geometry(round_tripped, tmp_path):
    def geometry(path):
        out = {}
        for r in _read_csv(path, "shapes.txt"):
            out.setdefault(r["shape_id"], []).append(
                (int(r["shape_pt_sequence"]), round(float(r["shape_pt_lat"]), 6),
                 round(float(r["shape_pt_lon"]), 6)))
        return {k: [p[1:] for p in sorted(v)] for k, v in out.items()}
    assert geometry(round_tripped) == geometry(tmp_path / "feed.zip")
