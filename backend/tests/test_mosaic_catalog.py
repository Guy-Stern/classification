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


def test_footprint_cache_hit_avoids_reopen():
    # A warm run over an unchanged folder must serve every footprint from the
    # sidecar cache — proven by making a real read_footprint EXPLODE and asserting
    # the second build still succeeds with identical entries.
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td) / "lay"; folder.mkdir()
        _tif(folder / "a.tif", (6.2, 45.2, 6.4, 45.4))
        _tif(folder / "b.tif", (6.5, 45.5, 6.7, 45.7))
        layer = LayerConfig(folder=folder, priority=1, glob="*.tif")
        first = mc.build_catalog([layer], None, CELL)          # cold: writes cache
        assert (folder / mc._CACHE_NAME).exists(), "cold run should write the cache sidecar"

        def _boom(p):
            raise AssertionError(f"cache miss re-opened {p}")

        orig = mc.read_footprint
        mc.read_footprint = _boom
        try:
            warm = mc.build_catalog([layer], None, CELL)       # warm: must not read
        finally:
            mc.read_footprint = orig
    assert sorted(e.path.name for e in warm) == sorted(e.path.name for e in first)


def test_footprint_cache_invalidates_on_change():
    # A file whose bytes change (size/mtime differ from the cached signature) must
    # be re-read, and the fresh footprint must replace the stale cached one.
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td) / "lay"; folder.mkdir()
        _tif(folder / "a.tif", (6.2, 45.2, 6.4, 45.4), size=100)
        layer = LayerConfig(folder=folder, priority=1, glob="*.tif")
        mc.build_catalog([layer], None, CELL)                  # cold: caches size=100² row
        # Rewrite with different bounds AND a different size -> stat signature changes.
        _tif(folder / "a.tif", (6.1, 45.1, 6.6, 45.6), size=120)
        kept = mc.build_catalog([layer], None, CELL)           # warm: signature mismatch -> re-read
    entry = next(e for e in kept if e.path.name == "a.tif")
    assert abs(entry.bounds_wgs84[0] - 6.1) < 1e-6, entry.bounds_wgs84


def test_cache_sidecar_not_rediscovered_as_source():
    # A permissive glob ("*") must NOT pick up the cache sidecar written on a
    # prior run (Path.glob matches dotfiles); otherwise run 2 crashes feeding the
    # JSON to read_footprint.
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td) / "lay"; folder.mkdir()
        _tif(folder / "a.tif", (6.2, 45.2, 6.4, 45.4))
        layer = LayerConfig(folder=folder, priority=1, glob="*")
        mc.build_catalog([layer], None, CELL)          # run 1: writes sidecar
        assert (folder / mc._CACHE_NAME).exists()
        kept = mc.build_catalog([layer], None, CELL)    # run 2: sidecar must be ignored
    assert sorted(e.path.name for e in kept) == ["a.tif"]


def test_discovery_skips_pipeline_output_dirs():
    # A recursive layer glob whose folder is an ancestor of the output must NOT
    # re-ingest a prior run's own mosaic/classified tiles as sources.
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td) / "orthos"; folder.mkdir()
        _tif(folder / "real.tif", (6.2, 45.2, 6.4, 45.4))
        for sub, name in [("N45E006_mosaic_tiles", "N45E006_mtile_r0_c0.tif"),
                          ("N45E006_classified_tiles", "N45E006_tile_r0_c0.tif"),
                          ("N45E006_with_vectors_tiles", "N45E006_wv_r0_c0.tif")]:
            d = folder / sub; d.mkdir()
            _tif(d / name, (6.2, 45.2, 6.4, 45.4))
        layer = LayerConfig(folder=folder, priority=1, glob="**/*.tif")
        kept = mc.build_catalog([layer], None, CELL)
    assert sorted(e.path.name for e in kept) == ["real.tif"], [e.path.name for e in kept]


def test_use_cache_false_skips_sidecar():
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td) / "lay"; folder.mkdir()
        _tif(folder / "a.tif", (6.2, 45.2, 6.4, 45.4))
        layer = LayerConfig(folder=folder, priority=1, glob="*.tif")
        mc.build_catalog([layer], None, CELL, use_cache=False)
    assert not (folder / mc._CACHE_NAME).exists(), "use_cache=False must not write a sidecar"


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
