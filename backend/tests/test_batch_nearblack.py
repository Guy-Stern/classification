# -*- coding: utf-8 -*-
"""Quality tests for the near-black (nodata/border) handling fix.

Verifies that:
  1. _sample_raster_for_training excludes near-black mosaic-edge pixels so the
     shared KMeans never forms a black centroid (the batch "everything sand,
     no soil" root cause).
  2. _paint_near_black_nodata zeroes the painted output where the source is
     near-black, so borders are written as nodata instead of SOIL.

Plain-runner (no pytest): run with the project python:
    py backend/tests/test_batch_nearblack.py
"""
import os
import sys
import tempfile

import numpy as np
import rasterio
from rasterio.transform import from_bounds

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app import core  # noqa: E402

FLAGS = {"spectral": True, "texture": True, "indices": False}


def _make_split_raster(path, h=600, w=600):
    """Left half pure black (RGB 0), right half sand-ish (200,180,150)."""
    a = np.zeros((3, h, w), np.uint8)
    a[0, :, w // 2:] = 200
    a[1, :, w // 2:] = 180
    a[2, :, w // 2:] = 150
    prof = dict(driver="GTiff", height=h, width=w, count=3, dtype="uint8",
                crs="EPSG:4326", transform=from_bounds(34, 32, 35, 33, w, h))
    with rasterio.open(path, "w", **prof) as d:
        d.write(a)


def test_sampling_excludes_near_black():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "split.tif")
        _make_split_raster(p)
        feats = core._sample_raster_for_training(p, FLAGS, n_samples=30_000)
    assert feats.shape[0] > 0, "no training pixels returned"
    assert feats.shape[1] >= 3, "feature matrix missing RGB columns"
    rgb_mean = feats[:, :3].mean(axis=1)
    n_black = int((rgb_mean < core.NEAR_BLACK_RGB_MEAN).sum())
    assert n_black == 0, f"{n_black} near-black pixels leaked into training"
    # The surviving pixels should be the sand half (bright).
    assert rgb_mean.mean() > 100, f"surviving sample too dark: mean={rgb_mean.mean():.1f}"


def test_paint_near_black_nodata():
    # rgb painted entirely "soil" (101,67,33); source left 2 cols black, rest bright.
    rgb = np.zeros((3, 4, 4), np.uint8)
    rgb[0], rgb[1], rgb[2] = 101, 67, 33
    src = np.zeros((3, 4, 4), np.uint8)
    src[:, :, 2:] = 200  # right two columns bright; left two black
    n = core._paint_near_black_nodata(rgb, src)
    assert n == 4 * 2, f"expected 8 masked px, got {n}"
    assert (rgb[:, :, :2] == 0).all(), "near-black border not zeroed"
    assert (rgb[0, :, 2:] == 101).all(), "valid pixels were wrongly zeroed"


def test_mask_helper_handles_no_black():
    src = np.full((3, 5, 5), 150, np.uint8)
    assert core._near_black_validity_mask(src) is None
    rgb = np.full((3, 5, 5), 101, np.uint8)
    assert core._paint_near_black_nodata(rgb, src) == 0
    assert (rgb == 101).all()


def test_no_palette_material_has_zero_channel():
    # nodata=0 must not collide with any MEA palette colour.
    for c in core.MEA_CLASSES:
        hexv = c.get("color", "#ffffff").lstrip("#")
        rgb = tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))
        assert 0 not in rgb, f"{c['name']} has a zero channel {rgb}; nodata=0 would collide"


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
