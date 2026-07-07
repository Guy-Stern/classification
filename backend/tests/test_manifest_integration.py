# -*- coding: utf-8 -*-
"""Integration test for the manifest -> geocell -> catalog -> mosaic wiring.

Exercises exactly what cli.run_manifest does UP TO (but not including) the
classify_v6 call — which needs torch and can't run here. Confirms a real TOML
manifest with real layer folders composites the right mosaic for its geocell.

Plain-runner (no pytest):
    py backend/tests/test_manifest_integration.py
"""
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.manifest import load_manifest          # noqa: E402
from app.mosaic_catalog import build_catalog     # noqa: E402
from app.mosaic_builder import build_mosaic       # noqa: E402


def _solid(path, bounds, color, size=100):
    west, south, east, north = bounds
    arr = np.zeros((3, size, size), np.uint8)
    for b in range(3):
        arr[b, :, :] = color[b]
    prof = dict(driver="GTiff", height=size, width=size, count=3, dtype="uint8",
                crs="EPSG:4326", transform=from_bounds(west, south, east, north, size, size))
    with rasterio.open(path, "w", **prof) as d:
        d.write(arr)


def _pixel(arr, tr, lon, lat):
    col = int((lon - tr.c) / tr.a)
    row = int((lat - tr.f) / tr.e)
    return tuple(int(arr[b, row, col]) for b in range(3))


def test_manifest_to_mosaic_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        base = td / "base"; base.mkdir()
        top = td / "top"; top.mkdir()
        # Geocell N45E006 -> (6,45,7,46). base = full cell red; top = centre blue.
        _solid(base / "b.tif", (6.0, 45.0, 7.0, 46.0), (255, 0, 0), size=100)
        _solid(top / "t.tif", (6.4, 45.4, 6.6, 45.6), (0, 0, 255), size=40)

        manifest_text = f"""
[geocell]
south_lat = 45
west_lon  = 6

[[layers]]
folder   = "{base.as_posix()}"
priority = 2

[[layers]]
folder   = "{top.as_posix()}"
priority = 1

[output]
path = "{(td / 'N45E006_material.tif').as_posix()}"
"""
        mpath = td / "geocell.toml"
        mpath.write_text(manifest_text, encoding="utf-8")

        # Mirror run_manifest's wiring exactly (sans classify_v6).
        manifest = load_manifest(mpath)
        geocell = manifest.geocell.to_geocell()
        assert geocell.name == "N45E006"
        bounds = geocell.bounds_wgs84()
        assert bounds == (6.0, 45.0, 7.0, 46.0)

        entries = build_catalog(manifest.layers, manifest.sources, bounds)
        assert len(entries) == 2

        out = td / "mosaic.tif"
        info = build_mosaic(entries, bounds, out)
        with rasterio.open(out) as d:
            arr, tr, crs = d.read(), d.transform, d.crs

        assert crs.to_epsg() == 4326
        assert _pixel(arr, tr, 6.5, 45.5) == (0, 0, 255)     # centre: priority 1 (blue) wins
        assert _pixel(arr, tr, 6.05, 45.95) == (255, 0, 0)   # corner: base (red)
        assert info["n_sources"] == 2


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
