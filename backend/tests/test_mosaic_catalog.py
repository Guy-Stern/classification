# -*- coding: utf-8 -*-
"""Tests for the geocell source catalog (backend/app/mosaic_catalog.py).

Uses tiny synthetic EPSG:4326 GeoTIFFs — no torch, needs only rasterio/numpy.

Plain-runner (no pytest):
    py backend/tests/test_mosaic_catalog.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import mosaic_catalog as mc  # noqa: E402
from app.manifest import LayerConfig  # noqa: E402

# Geocell N45E006 -> WGS84 bounds (west, south, east, north)
CELL = (6.0, 45.0, 7.0, 46.0)


def _tif(path, bounds, size=100, color=(255, 0, 0), crs="EPSG:4326", nodata=None):
    west, south, east, north = bounds
    arr = np.zeros((3, size, size), np.uint8)
    for b in range(3):
        arr[b, :, :] = color[b]
    prof = dict(driver="GTiff", height=size, width=size, count=3, dtype="uint8",
                crs=crs, transform=from_bounds(west, south, east, north, size, size))
    if nodata is not None:
        prof["nodata"] = nodata
    with rasterio.open(path, "w", **prof) as d:
        d.write(arr)
    return path


def test_read_footprint_bounds_and_pixel_size():
    with tempfile.TemporaryDirectory() as td:
        p = _tif(Path(td) / "a.tif", (6.0, 45.0, 7.0, 46.0), size=100)
        bounds, px = mc.read_footprint(Path(p))
    assert bounds == (6.0, 45.0, 7.0, 46.0)
    assert abs(px - 0.01) < 1e-9        # 1° / 100 px


def test_no_crs_source_raises():
    with tempfile.TemporaryDirectory() as td:
        p = _tif(Path(td) / "a.tif", (6.0, 45.0, 7.0, 46.0), crs=None)
        try:
            mc.read_footprint(Path(p))
        except RuntimeError:
            return
    raise AssertionError("expected RuntimeError for a source with no CRS")


def test_empty_glob_layer_raises():
    with tempfile.TemporaryDirectory() as td:
        layer = LayerConfig(folder=Path(td), priority=1, glob="*.tif")
        try:
            mc.build_catalog([layer], None, CELL)
        except ValueError:
            return
    raise AssertionError("expected ValueError for a layer whose glob matched no files")


def test_geocell_prefilter_drops_nonintersecting():
    with tempfile.TemporaryDirectory() as td:
        hi = Path(td) / "hi"; hi.mkdir()
        _tif(hi / "in.tif", (6.2, 45.2, 6.4, 45.4))          # inside the cell
        _tif(hi / "out.tif", (10.0, 10.0, 10.5, 10.5))       # far away
        layer = LayerConfig(folder=hi, priority=1, glob="*.tif")
        kept = mc.build_catalog([layer], None, CELL)
    names = sorted(e.path.name for e in kept)
    assert names == ["in.tif"], names


def test_discover_default_glob_picks_up_tiff_and_jp2():
    # Discovery only (mc._discover) so it runs without a GDAL JP2 driver — no
    # pixels are read. The default glob must union .tif/.tiff/.jp2 and skip
    # non-raster files; each match appears once even on a case-insensitive FS.
    with tempfile.TemporaryDirectory() as td:
        lay = Path(td) / "mixed"; lay.mkdir()
        for name in ("a.tif", "b.tiff", "c.jp2", "skip.png", "notes.txt"):
            (lay / name).write_bytes(b"")
        layer = LayerConfig(folder=lay, priority=1)   # default glob
        found = sorted(p.name for p in mc._discover(layer))
    assert found == ["a.tif", "b.tiff", "c.jp2"], found


def test_discover_string_glob_restricts_to_one_format():
    with tempfile.TemporaryDirectory() as td:
        lay = Path(td) / "jp2only"; lay.mkdir()
        for name in ("a.tif", "c.jp2", "d.jp2"):
            (lay / name).write_bytes(b"")
        layer = LayerConfig(folder=lay, priority=1, glob="**/*.jp2")
        found = sorted(p.name for p in mc._discover(layer))
    assert found == ["c.jp2", "d.jp2"], found


def test_priority_one_is_last_in_composite_order():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "base"; base.mkdir()
        top = Path(td) / "top"; top.mkdir()
        _tif(base / "b.tif", CELL)
        _tif(top / "t.tif", CELL)
        layers = [
            LayerConfig(folder=base, priority=2, glob="*.tif"),
            LayerConfig(folder=top, priority=1, glob="*.tif"),
        ]
        entries = mc.build_catalog(layers, None, CELL)
        order = mc.composite_order(entries)
    # Last-wins: priority=1 (top/t.tif) must be applied LAST so it wins.
    assert order[-1].priority == 1 and order[-1].path.name == "t.tif"
    assert order[0].priority == 2 and order[0].path.name == "b.tif"


def test_flat_sources_get_lowest_priority():
    with tempfile.TemporaryDirectory() as td:
        lay = Path(td) / "lay"; lay.mkdir()
        _tif(lay / "l.tif", CELL)
        flat = _tif(Path(td) / "flat.tif", CELL)
        layers = [LayerConfig(folder=lay, priority=3, glob="*.tif")]
        entries = mc.build_catalog(layers, [flat], CELL)
        by_name = {e.path.name: e.priority for e in entries}
    # Flat source priority must be greater (= lower priority) than every layer.
    assert by_name["flat.tif"] == 4
    assert by_name["l.tif"] == 3
    # ...and composite-first (loses to the layer on overlap).
    assert mc.composite_order(entries)[0].path.name == "flat.tif"


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
