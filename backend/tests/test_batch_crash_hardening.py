# -*- coding: utf-8 -*-
"""Quality test for the batch crash-hardening fix.

A per-tile failure inside the SDE/shapefile mask-resolve path must NOT lose a
tile whose KMeans classification already succeeded. apply_v6_masks_to_classification
should swallow a resolve_shapefile exception and degrade to no-mask, returning
status "ok" instead of propagating (which earlier produced the silent "17 tiles
with no output" on mosaic-edge tiles).

Plain-runner:  py backend/tests/test_batch_crash_hardening.py
"""
import os
import sys
import tempfile

import numpy as np
import rasterio
from rasterio.transform import from_bounds

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import pipeline, core, shapefile_resolver, shapefile_config  # noqa: E402


def _make_classification(path, h=64, w=64):
    a = np.zeros((3, h, w), np.uint8)
    a[0], a[1], a[2] = 237, 201, 175  # SAND-colored valid classification
    prof = dict(driver="GTiff", height=h, width=w, count=3, dtype="uint8",
                crs="EPSG:4326", transform=from_bounds(34, 32, 34.01, 32.01, w, h))
    with rasterio.open(path, "w", **prof) as d:
        d.write(a)


def test_resolve_shapefile_raise_degrades_to_no_mask():
    with tempfile.TemporaryDirectory() as td:
        clsp = os.path.join(td, "cls.tif")
        _make_classification(clsp)

        orig_resolve = shapefile_resolver.resolve_shapefile
        orig_water = shapefile_config.get_water_mask

        def _boom(*a, **k):
            raise RuntimeError("simulated SDE subprocess crash at mosaic edge")

        shapefile_resolver.resolve_shapefile = _boom
        shapefile_config.get_water_mask = lambda *a, **k: ""
        try:
            res = pipeline.apply_v6_masks_to_classification(
                classification_path=clsp,
                raster_path=clsp,
                classes=core.MEA_CLASSES,
                classify_result={"status": "ok"},
                sam3_enabled=False,      # no torch/SAM3 needed
                water_mask=None,
            )
        finally:
            shapefile_resolver.resolve_shapefile = orig_resolve
            shapefile_config.get_water_mask = orig_water

        # The exception must have been swallowed and the tile preserved.
        assert isinstance(res, dict), "no result returned"
        assert res.get("status") == "ok", f"expected ok after degrade, got {res.get('status')}: {res}"
        assert res["maskSources"]["roads"] == "disabled", res["maskSources"]
        assert res["maskSources"]["buildings"] == "disabled", res["maskSources"]
        # Output still points at the (KMeans) classification — not lost.
        assert os.path.exists(res["outputPath"]), "classification output went missing"


def test_get_water_mask_raise_degrades():
    with tempfile.TemporaryDirectory() as td:
        clsp = os.path.join(td, "cls.tif")
        _make_classification(clsp)
        orig_resolve = shapefile_resolver.resolve_shapefile
        orig_water = shapefile_config.get_water_mask
        shapefile_resolver.resolve_shapefile = lambda *a, **k: None
        def _boom_water(*a, **k):
            raise RuntimeError("config read blew up")
        shapefile_config.get_water_mask = _boom_water
        try:
            res = pipeline.apply_v6_masks_to_classification(
                classification_path=clsp, raster_path=clsp,
                classes=core.MEA_CLASSES, classify_result={"status": "ok"},
                sam3_enabled=False, water_mask=None,
            )
        finally:
            shapefile_resolver.resolve_shapefile = orig_resolve
            shapefile_config.get_water_mask = orig_water
        assert res.get("status") == "ok", f"water-config crash should degrade, got {res}"
        assert res["maskSources"]["water"] == "disabled"


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
