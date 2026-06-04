"""Unit tests for the 3-tier road-width resolution (SDE road buffering).

The resolver buffers SDE road LINES to polygons; the per-feature full width
(metres) is chosen by ``_road_width_m`` in this order:

    Tier 1  explicit per-feature width attribute (``road_width_attr``), if > 0
    Tier 2  road type -> main/side width (``Road_Type_*`` config), if its width > 0
    Tier 3  flat fallback (``road_width_fallback_m``)

Run with pytest (preferred):
    python -m pytest backend/tests/test_road_type_width.py -v
or as a plain script when pytest isn't installed:
    python backend/tests/test_road_type_width.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow `from app import ...` when running outside the backend/ working dir.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))

from app.shapefile_resolver import _road_width_m, _buffer_road_lines

# Geopandas/shapely are optional here: the pure-logic tests always run; the
# end-to-end buffering test below is skipped when the geo stack is absent.
try:
    import geopandas as gpd
    from shapely.geometry import LineString
    from pyproj import CRS as _CRS
    _GEO_OK = True
except Exception:  # noqa: BLE001 — environment-specific dep absence
    _GEO_OK = False

# Common road-type config shared across tests (full widths in metres).
_TYPE_CFG = dict(
    main_key="MAIN", main_w=8.0,
    side_key="SIDE", side_w=4.0,
    fallback_m=2.0,
)


# ─── Tier 1: explicit width attribute ────────────────────────────────────────

def test_tier1_width_attr_wins_when_positive():
    # Explicit width present and > 0 is used verbatim, even if the type matches.
    assert _road_width_m(10.0, "MAIN", **_TYPE_CFG) == 10.0


def test_tier1_skipped_when_width_zero_or_negative():
    assert _road_width_m(0.0, "MAIN", **_TYPE_CFG) == 8.0    # -> type=main
    assert _road_width_m(-5.0, "SIDE", **_TYPE_CFG) == 4.0   # -> type=side


def test_tier1_skipped_when_width_missing_or_nonnumeric():
    assert _road_width_m(None, "MAIN", **_TYPE_CFG) == 8.0
    assert _road_width_m("", "SIDE", **_TYPE_CFG) == 4.0
    assert _road_width_m("abc", "MAIN", **_TYPE_CFG) == 8.0


# ─── Tier 2: road type -> main/side width ────────────────────────────────────

def test_tier2_main_road_width():
    assert _road_width_m(None, "MAIN", **_TYPE_CFG) == 8.0


def test_tier2_side_road_width():
    assert _road_width_m(None, "SIDE", **_TYPE_CFG) == 4.0


def test_tier2_matching_is_case_insensitive_and_trimmed():
    assert _road_width_m(None, "main", **_TYPE_CFG) == 8.0
    assert _road_width_m(None, " Side ", **_TYPE_CFG) == 4.0


def test_tier2_matching_coerces_numeric_codes():
    cfg = dict(main_key="1", main_w=8.0, side_key="2", side_w=4.0, fallback_m=2.0)
    assert _road_width_m(None, 1, **cfg) == 8.0
    assert _road_width_m(None, 2, **cfg) == 4.0


def test_main_takes_precedence_when_both_keys_equal():
    cfg = dict(main_key="X", main_w=8.0, side_key="X", side_w=4.0, fallback_m=2.0)
    assert _road_width_m(None, "X", **cfg) == 8.0


# ─── Tier 3: fallback ────────────────────────────────────────────────────────

def test_tier3_fallback_when_type_matches_neither():
    assert _road_width_m(None, "TRACK", **_TYPE_CFG) == 2.0


def test_tier3_fallback_when_type_missing():
    assert _road_width_m(None, None, **_TYPE_CFG) == 2.0
    assert _road_width_m(None, float("nan"), **_TYPE_CFG) == 2.0


def test_type_tier_disabled_when_keys_blank():
    # No type keys configured -> behaves like the old 2-tier (attr -> fallback).
    cfg = dict(main_key="", main_w=0.0, side_key="", side_w=0.0, fallback_m=2.0)
    assert _road_width_m(None, "MAIN", **cfg) == 2.0


def test_matched_type_with_nonpositive_width_falls_to_fallback():
    cfg = dict(main_key="MAIN", main_w=0.0, side_key="SIDE", side_w=4.0, fallback_m=2.0)
    assert _road_width_m(None, "MAIN", **cfg) == 2.0   # main_w <= 0 -> fallback
    assert _road_width_m(None, "SIDE", **cfg) == 4.0


# ─── End-to-end: real LINE -> polygon buffering through all 3 tiers ──────────

def test_buffer_road_lines_end_to_end():
    """Buffer real road LINES and confirm each polygon's width matches its tier.

    Uses a projected metre CRS (UTM 36N) so 1 buffer unit == 1 m. A straight
    LineString of length L buffered with flat caps by half-width h yields a
    rectangle of area L*(2h) = L*full_width, so area/L recovers the full width.
    """
    if not _GEO_OK:
        print("SKIP test_buffer_road_lines_end_to_end (geopandas/shapely absent)")
        return

    utm36n = _CRS.from_epsg(32636)  # metres
    line_len = 100.0
    # (RD_WIDTH, RD_TYPE, expected full width m, tier exercised)
    rows = [
        (10.0, "MAIN",  10.0),   # tier 1: explicit width wins over type
        (0.0,  "MAIN",  8.0),    # tier 2: width <= 0 -> main-road width
        (None, "side",  4.0),    # tier 2: missing width, case-insensitive side
        (None, "TRACK", 2.0),    # tier 3: unknown type -> fallback
    ]
    geoms = [LineString([(0.0, i * 1000.0), (line_len, i * 1000.0)])
             for i in range(len(rows))]
    gdf = gpd.GeoDataFrame(
        {"RD_WIDTH": [r[0] for r in rows], "RD_TYPE": [r[1] for r in rows]},
        geometry=geoms, crs=utm36n,
    )

    out = _buffer_road_lines(
        gdf, utm36n, "RD_WIDTH", 2.0,
        type_attr="RD_TYPE", main_key="MAIN", main_w=8.0,
        side_key="SIDE", side_w=4.0,
    )

    assert len(out) == len(rows), f"expected {len(rows)} polygons, got {len(out)}"
    for (_, _, expected_w), area in zip(rows, out.geometry.area):
        got_w = area / line_len
        assert abs(got_w - expected_w) < 0.01, \
            f"expected ~{expected_w} m wide, got {got_w:.4f} m"


# Plain-script runner so the suite is verifiable without pytest installed.
if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception:
            failures += 1
            print(f"FAIL {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
