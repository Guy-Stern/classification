# -*- coding: utf-8 -*-
"""Tests for the CDB geocell math (backend/app/geocell.py).

Plain-runner (no pytest):
    py backend/tests/test_geocell.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import geocell  # noqa: E402
from app.geocell import GeoCell  # noqa: E402


def test_zone_width_table():
    assert geocell.cdb_cell_lon_width_deg(45) == 1.0     # zone 5
    assert geocell.cdb_cell_lon_width_deg(-10) == 1.0
    assert geocell.cdb_cell_lon_width_deg(50) == 2.0     # zone 6
    assert geocell.cdb_cell_lon_width_deg(72) == 4.0     # zone 7/2
    assert geocell.cdb_cell_lon_width_deg(-72) == 4.0
    assert geocell.cdb_cell_lon_width_deg(77) == 6.0
    assert geocell.cdb_cell_lon_width_deg(85) == 12.0


def test_name_and_bounds():
    c = GeoCell(south_lat=45, west_lon=6)
    assert c.name == "N45E006"
    assert c.bounds_wgs84() == (6.0, 45.0, 7.0, 46.0)


def test_name_southern_western_hemisphere():
    c = GeoCell(south_lat=-7, west_lon=-123)
    assert c.name == "S07W123"
    assert c.bounds_wgs84() == (-123.0, -7.0, -122.0, -6.0)


def test_wide_zone_bounds():
    # 72°N is in the 4°-wide zone (70-75°); west_lon must be a multiple of 4.
    c = GeoCell(south_lat=72, west_lon=8)
    assert c.lon_width_deg == 4.0
    assert c.bounds_wgs84() == (8.0, 72.0, 12.0, 73.0)


def test_snap_validation_rejects_misaligned_west_lon():
    # zone 6 (50-70) is 2°-wide: odd west_lon must fail.
    try:
        GeoCell(south_lat=60, west_lon=7)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for west_lon=7 in a 2°-wide zone")

    # zone 7/2 (70-75) is 4°-wide: 5 is not a multiple of 4.
    try:
        GeoCell(south_lat=72, west_lon=5)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for west_lon=5 in a 4°-wide zone")


def test_snap_validation_accepts_aligned_west_lon():
    GeoCell(south_lat=60, west_lon=6)    # even -> ok in 2° zone
    GeoCell(south_lat=72, west_lon=8)    # multiple of 4 -> ok in 4° zone
    GeoCell(south_lat=85, west_lon=12)   # multiple of 12 -> ok in 12° zone


def test_range_validation():
    for bad in (dict(south_lat=90, west_lon=0), dict(south_lat=0, west_lon=180),
                dict(south_lat=-91, west_lon=0)):
        try:
            GeoCell(**bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
