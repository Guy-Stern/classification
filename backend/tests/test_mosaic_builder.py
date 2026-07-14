# -*- coding: utf-8 -*-
"""Tests for the priority mosaic builder (backend/app/mosaic_builder.py).

Uses tiny synthetic EPSG:4326 GeoTIFFs — no torch, needs only rasterio/numpy.

Plain-runner (no pytest):
    py backend/tests/test_mosaic_builder.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import mosaic_builder as mb  # noqa: E402
from app.mosaic_catalog import SourceEntry, composite_order  # noqa: E402

CELL = (6.0, 45.0, 7.0, 46.0)   # geocell N45E006 (west, south, east, north)


def _solid(path, bounds, color, size=100, nodata=None):
    west, south, east, north = bounds
    arr = np.zeros((3, size, size), np.uint8)
    for b in range(3):
        arr[b, :, :] = color[b]
    _write(path, bounds, arr, size, nodata)
    return path


def _bordered(path, bounds, center, border, size=100, border_px=20, nodata=None):
    """A tile with a solid border colour framing a solid centre colour."""
    west, south, east, north = bounds
    arr = np.zeros((3, size, size), np.uint8)
    for b in range(3):
        arr[b, :, :] = border[b]
    for b in range(3):
        arr[b, border_px:size - border_px, border_px:size - border_px] = center[b]
    _write(path, bounds, arr, size, nodata)
    return path


def _gradient(path, bounds, size=400):
    """A tile whose per-pixel colour varies (row/col ramps) — exercises the
    downsample resampling kernel (a solid tile can't tell nearest from average)."""
    rr, cc = np.meshgrid(np.arange(size), np.arange(size), indexing="ij")
    arr = np.zeros((3, size, size), np.uint8)
    arr[0] = (rr * 255 // max(1, size - 1)).astype(np.uint8)          # red ramps down rows
    arr[1] = (cc * 255 // max(1, size - 1)).astype(np.uint8)          # green ramps across cols
    arr[2] = (((rr + cc) * 255) // max(1, 2 * (size - 1))).astype(np.uint8)
    arr = np.clip(arr, 8, 255)   # keep every pixel above the near-black cutoff
    _write(path, bounds, arr, size, None)
    return path


def _write(path, bounds, arr, size, nodata):
    west, south, east, north = bounds
    prof = dict(driver="GTiff", height=size, width=size, count=3, dtype="uint8",
                crs="EPSG:4326", transform=from_bounds(west, south, east, north, size, size))
    if nodata is not None:
        prof["nodata"] = nodata
    with rasterio.open(path, "w", **prof) as d:
        d.write(arr)


def _fullgrid_average_reference(entries, cell):
    """Serial, full-grid, area-average last-wins composite — the algorithm the
    windowed+parallel :func:`mosaic_builder._composite_and_write` must reproduce
    exactly. Independent of mb's windowing so the test can't pass a shared bug."""
    from rasterio.enums import ColorInterp, Resampling
    from rasterio.vrt import WarpedVRT

    gsd = mb.resolve_target_gsd_deg(entries)
    w, h, gsd_used, tr = mb.compute_grid(cell, gsd)
    result = np.zeros((3, h, w), np.uint8)
    for e in composite_order(entries):        # low priority -> high (last wins)
        with rasterio.open(e.path) as s:
            has_a = ColorInterp.alpha in s.colorinterp
            with WarpedVRT(s, crs="EPSG:4326", transform=tr, width=w, height=h,
                           resampling=Resampling.average, add_alpha=not has_a) as v:
                ab = v.read([1, 2, 3, v.count])
        rgb = ab[:3]
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8)
        valid = ab[3] > 0
        valid &= rgb.mean(axis=0, dtype=np.float32) >= mb.NEAR_BLACK_RGB_MEAN
        result[:, valid] = rgb[:, valid]
    return result


def _entry(path, priority, size, bounds):
    west, south, east, north = bounds
    px = max((east - west) / size, (north - south) / size)
    return SourceEntry(Path(path), priority, tuple(map(float, bounds)), px)


def _read(path):
    with rasterio.open(path) as d:
        return d.read(), d.transform, d.crs


def _pixel(arr, transform, lon, lat):
    col = int((lon - transform.c) / transform.a)
    row = int((lat - transform.f) / transform.e)   # transform.e is negative
    return tuple(int(arr[b, row, col]) for b in range(3))


# ── compute_grid (pure) ──────────────────────────────────────────────────────

def test_compute_grid_basic():
    w, h, gsd, tr = mb.compute_grid(CELL, 0.01)
    assert (w, h) == (100, 100)
    assert abs(gsd - 0.01) < 1e-12


def test_compute_grid_cap_coarsens_and_warns():
    # 1°/1e-5 = 100000 px/side -> must be coarsened under the cap.
    w, h, gsd, tr = mb.compute_grid((0.0, 0.0, 1.0, 1.0), 1e-5, max_side_px=20000)
    assert max(w, h) <= 20000
    assert gsd > 1e-5           # GSD was coarsened
    assert w >= 1 and h >= 1


def test_resolve_target_gsd_picks_finest():
    entries = [
        _entry("a.tif", 2, 100, CELL),      # px 0.01
        _entry("b.tif", 1, 200, CELL),      # px 0.005
    ]
    assert abs(mb.resolve_target_gsd_deg(entries) - 0.005) < 1e-12


# ── build_mosaic (composite) ─────────────────────────────────────────────────

def test_priority_one_wins_in_overlap():
    with tempfile.TemporaryDirectory() as td:
        base = _solid(Path(td) / "base.tif", CELL, (255, 0, 0), size=100)       # red, prio 2
        patch_bounds = (6.4, 45.4, 6.6, 45.6)
        top = _solid(Path(td) / "top.tif", patch_bounds, (0, 0, 255), size=40)  # blue, prio 1
        entries = [_entry(base, 2, 100, CELL), _entry(top, 1, 40, patch_bounds)]
        out = Path(td) / "mosaic.tif"
        info = mb.build_mosaic(entries, CELL, out)
        arr, tr, crs = _read(out)
    assert crs.to_epsg() == 4326
    assert (info["width"], info["height"]) == (200, 200)     # finest gsd 0.005
    assert _pixel(arr, tr, 6.5, 45.5) == (0, 0, 255)          # centre: priority 1 (blue) wins
    assert _pixel(arr, tr, 6.05, 45.95) == (255, 0, 0)        # corner: only base (red)


def test_near_black_border_does_not_stomp_lower_priority():
    with tempfile.TemporaryDirectory() as td:
        base = _solid(Path(td) / "base.tif", CELL, (255, 0, 0), size=100)       # red, prio 2
        # priority-1 tile: blue centre, BLACK (0,0,0) border, no nodata tag.
        top = _bordered(Path(td) / "top.tif", CELL, center=(0, 0, 255),
                        border=(0, 0, 0), size=100, border_px=20)
        entries = [_entry(base, 2, 100, CELL), _entry(top, 1, 100, CELL)]
        out = Path(td) / "mosaic.tif"
        mb.build_mosaic(entries, CELL, out)
        arr, tr, crs = _read(out)
    assert _pixel(arr, tr, 6.5, 45.5) == (0, 0, 255)          # centre: priority 1 wins
    assert _pixel(arr, tr, 6.05, 45.95) == (255, 0, 0)        # border: black excluded -> base shows


def test_nodata_source_masks_via_alpha_not_near_black():
    # priority-1 border is GREY (128,128,128) with nodata=128 — NOT near-black,
    # so this isolates the WarpedVRT alpha/nodata masking path.
    with tempfile.TemporaryDirectory() as td:
        base = _solid(Path(td) / "base.tif", CELL, (255, 0, 0), size=100)       # red, prio 2
        top = _bordered(Path(td) / "top.tif", CELL, center=(0, 0, 255),
                        border=(128, 128, 128), size=100, border_px=20, nodata=128)
        entries = [_entry(base, 2, 100, CELL), _entry(top, 1, 100, CELL)]
        out = Path(td) / "mosaic.tif"
        mb.build_mosaic(entries, CELL, out)
        arr, tr, crs = _read(out)
    assert _pixel(arr, tr, 6.5, 45.5) == (0, 0, 255)          # centre: priority 1 wins
    assert _pixel(arr, tr, 6.05, 45.95) == (255, 0, 0)        # grey nodata border masked -> base


def test_uncovered_cell_area_is_black_fill():
    with tempfile.TemporaryDirectory() as td:
        patch_bounds = (6.4, 45.4, 6.6, 45.6)
        only = _solid(Path(td) / "p.tif", patch_bounds, (0, 200, 0), size=40)
        entries = [_entry(only, 1, 40, patch_bounds)]
        out = Path(td) / "mosaic.tif"
        info = mb.build_mosaic(entries, CELL, out)
        arr, tr, crs = _read(out)
    assert info["missing_fraction"] > 0.5                     # patch covers a fraction of the cell
    assert _pixel(arr, tr, 6.5, 45.5) == (0, 200, 0)          # inside the patch
    assert _pixel(arr, tr, 6.02, 45.98) == (0, 0, 0)          # outside -> black fill


def test_windowed_parallel_matches_serial_fullgrid():
    # Multi-source scene with partial coverage, overlap, mixed priorities, and a
    # DOWNSAMPLED gradient tile — the windowed+parallel composite must be
    # byte-identical to the serial full-grid average reference.
    with tempfile.TemporaryDirectory() as td:
        base = _solid(Path(td) / "base.tif", CELL, (40, 60, 200), size=120)        # whole cell, prio 3
        left = _solid(Path(td) / "left.tif", (6.0, 45.0, 6.5, 46.0), (200, 30, 30), size=120)  # W half, prio 2
        grad = _gradient(Path(td) / "grad.tif", (6.3, 45.3, 6.7, 45.7), size=400)   # centre patch, 4x downsample, prio 1
        entries = [
            _entry(base, 3, 120, CELL),
            _entry(left, 2, 120, (6.0, 45.0, 6.5, 46.0)),
            _entry(grad, 1, 400, (6.3, 45.3, 6.7, 45.7)),
        ]
        out = Path(td) / "mosaic.tif"
        mb.build_mosaic(entries, CELL, out)
        got, _, _ = _read(out)
        ref = _fullgrid_average_reference(entries, CELL)
    assert got.shape == ref.shape, (got.shape, ref.shape)
    assert np.array_equal(got, ref), (
        f"windowed/parallel composite diverged from serial reference: "
        f"{int(np.count_nonzero(np.any(got != ref, axis=0)))} px differ, "
        f"max |Δ|={int(np.abs(got.astype(int) - ref.astype(int)).max())}"
    )


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
