# -*- coding: utf-8 -*-
"""Priority mosaic builder: composite geocell layers into one EPSG:4326 GeoTIFF.

Takes the geocell's surviving catalog entries and writes a single RGB GeoTIFF
covering the whole cell in WGS-84, at the finest covering layer's ground-sample
distance. The classifier (``classify_v6``) then treats that mosaic exactly like
any other input ortho — no changes to the classification pipeline.

Compositing is **priority last-wins** (adapted from the IER's
``imagery_reader.read_imagery_grid``): each source is reprojected to the target
grid via ``WarpedVRT``; a pixel is *valid* where the source has real data
(alpha band > 0) **and** is not a near-black nodata/border pixel. Sources are
applied highest-priority-last, so priority=1 is the final writer per pixel and
its black borders never stomp a real pixel from a lower-priority layer.

Scale: a full-resolution 1° cell can be enormous, so ``MAX_MOSAIC_SIDE_PX``
caps the output dimensions — a mosaic that would exceed the cap has its GSD
coarsened (with a loud warning) rather than exploding to hundreds of GB. The
composite is done full-frame in RAM (the IER-proven mechanism); the cap keeps
that bounded. The write step is isolated in :func:`_composite_and_write` so a
future native-resolution streaming writer can replace it without touching the
catalog/grid math.

Needs ``rasterio`` + ``numpy`` (both import under the geo-only test
interpreter). Deliberately does NOT import ``core`` — see ``NEAR_BLACK_RGB_MEAN``
below — so this module stays importable without the sklearn/skimage/geopandas
chain and unit-tests without torch.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT

from .mosaic_catalog import SourceEntry, composite_order

_WGS84 = "EPSG:4326"

# Pixels whose mean RGB is below this are treated as nodata / black border and
# excluded from a source's valid mask, so a source's black edge can't win over a
# real pixel from a lower-priority layer. Kept as a local constant (NOT imported
# from core.py) so this module doesn't drag in the sklearn/skimage/geopandas
# import chain; mirror of core.NEAR_BLACK_RGB_MEAN (backend/app/core.py:445).
NEAR_BLACK_RGB_MEAN = 8.0

# Hard cap on either output side length. A mosaic that would exceed this has its
# GSD coarsened (with a warning) instead of allocating a multi-hundred-GB array.
# 20000 x 20000 x 3 uint8 ~= 1.15 GB for the composite buffer. Tune here.
MAX_MOSAIC_SIDE_PX = 20_000


def resolve_target_gsd_deg(entries: list[SourceEntry]) -> float:
    """Finest (smallest) pixel size in deg/px across the entries.

    Sources with a non-positive pixel size (degenerate bounds) are ignored.
    Raises if no entry has a usable pixel size.
    """
    sizes = [e.pixel_size_deg for e in entries if e.pixel_size_deg > 0.0]
    if not sizes:
        raise ValueError("no source has a usable (positive) pixel size")
    return min(sizes)


def compute_grid(
    geocell_bounds: tuple[float, float, float, float],
    gsd_deg: float,
    max_side_px: int = MAX_MOSAIC_SIDE_PX,
) -> tuple[int, int, float, "rasterio.Affine"]:
    """Return ``(width, height, gsd_used_deg, transform)`` for the target grid.

    Covers the whole geocell at ``gsd_deg``. If either side would exceed
    ``max_side_px`` the GSD is coarsened just enough to fit, and a WARNING is
    logged — the resolution drop is never silent.
    """
    west, south, east, north = geocell_bounds
    lon_extent = east - west
    lat_extent = north - south

    width = max(1, round(lon_extent / gsd_deg))
    height = max(1, round(lat_extent / gsd_deg))

    gsd_used = gsd_deg
    longest = max(width, height)
    if longest > max_side_px:
        factor = longest / max_side_px
        gsd_used = gsd_deg * factor
        width = max(1, math.ceil(lon_extent / gsd_used))
        height = max(1, math.ceil(lat_extent / gsd_used))
        print(f"[mosaic] WARNING: cell would be {longest} px on its longest side at "
              f"GSD {gsd_deg:.3e} deg/px (cap {max_side_px}); coarsening GSD to "
              f"{gsd_used:.3e} deg/px -> {width} x {height} px. Set a coarser finest "
              f"layer or raise MAX_MOSAIC_SIDE_PX to keep native resolution.")

    transform = from_bounds(west, south, east, north, width, height)
    return width, height, gsd_used, transform


def _read_source_into_grid(
    src_path: Path,
    target_transform: "rasterio.Affine",
    width: int,
    height: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Reproject one source onto the target grid → ``((3, H, W) uint8, (H, W) bool)``.

    The bool mask is True where the source contributes a real pixel: inside the
    source's data area (WarpedVRT alpha > 0) AND not a near-black nodata/border
    pixel. Out-of-extent and border pixels come back False so a partial-coverage
    or black-edged source doesn't overwrite an earlier (lower-priority) source.

    Validity comes from the WarpedVRT alpha band (GDAL sets 255 inside data, 0
    outside, and encodes source nodata into it too). ``masked=True`` is unsafe
    here: a source with ``nodata=None`` returns no mask and out-of-extent zeros
    would read as real data. If the source already has an alpha band,
    ``add_alpha=True`` errors, so we add one only when it's missing.
    """
    try:
        with rasterio.open(src_path) as src:
            if src.count < 3:
                raise ValueError(
                    f"source must have at least 3 bands (RGB); {src_path} has {src.count}"
                )
            if src.crs is None:
                raise ValueError(
                    f"source has no CRS; assign one with gdal_edit before ingest: {src_path}"
                )
            src_has_alpha = ColorInterp.alpha in src.colorinterp
            with WarpedVRT(
                src,
                crs=_WGS84,
                transform=target_transform,
                width=width,
                height=height,
                resampling=Resampling.bilinear,
                add_alpha=not src_has_alpha,
            ) as vrt:
                if vrt.colorinterp[vrt.count - 1] != ColorInterp.alpha:
                    raise RuntimeError(
                        f"WarpedVRT yielded no alpha band at vrt.count "
                        f"(count={vrt.count}, colorinterp={vrt.colorinterp})"
                    )
                all_bands = vrt.read([1, 2, 3, vrt.count])
    except Exception as exc:
        raise RuntimeError(f"failed to read source {src_path}: {exc}") from exc

    rgb = all_bands[:3]
    alpha = all_bands[3]
    if rgb.dtype != np.uint8:
        rgb = np.clip(rgb, 0, 255).astype(np.uint8)

    valid = alpha > 0
    # Drop near-black pixels (nodata / mosaic border filled with RGB~0 that is
    # NOT flagged as raster nodata) so they can't win over a real lower-priority
    # pixel. mean over the 3 bands, same threshold core.py uses on input.
    mean_rgb = rgb.mean(axis=0)
    valid &= mean_rgb >= NEAR_BLACK_RGB_MEAN
    return np.ascontiguousarray(rgb), valid


def _composite_and_write(
    order: list[SourceEntry],
    width: int,
    height: int,
    transform: "rasterio.Affine",
    out_path: Path,
) -> float:
    """Full-frame last-wins composite of ``order`` → tiled DEFLATE GeoTIFF.

    Returns the fraction of output pixels left uncovered (black fill). Isolated
    from the grid math so a streaming (per-block) writer can replace it later
    without changing callers.
    """
    result = np.zeros((3, height, width), dtype=np.uint8)  # black fill
    overall_valid = np.zeros((height, width), dtype=bool)

    for entry in order:
        rgb, valid = _read_source_into_grid(entry.path, transform, width, height)
        result[:, valid] = rgb[:, valid]
        overall_valid |= valid

    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 3,
        "dtype": "uint8",
        "crs": _WGS84,
        "transform": transform,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "deflate",
        "zlevel": 1,
        "predictor": 2,
        "interleave": "band",
    }
    # No nodata tag: the black (0,0,0) fill is what core.py's near-black
    # detection already treats as border/nodata, so the mosaic looks exactly
    # like a real ortho mosaic to the classifier.
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(result)
        tmp.replace(out_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    return float((~overall_valid).mean())


def build_mosaic(
    entries: list[SourceEntry],
    geocell_bounds: tuple[float, float, float, float],
    out_path: str | Path,
    max_side_px: int = MAX_MOSAIC_SIDE_PX,
) -> dict:
    """Composite ``entries`` into a single EPSG:4326 RGB GeoTIFF at ``out_path``.

    ``entries`` must be non-empty and already filtered to the geocell (as
    returned by :func:`mosaic_catalog.build_catalog`). Returns a summary dict:
    ``{path, width, height, gsd_deg, n_sources, missing_fraction}``.
    """
    if not entries:
        raise ValueError("build_mosaic: no source entries (nothing intersects the geocell)")

    out_path = Path(out_path)
    gsd = resolve_target_gsd_deg(entries)
    width, height, gsd_used, transform = compute_grid(geocell_bounds, gsd, max_side_px)
    order = composite_order(entries)

    print(f"[mosaic] {len(order)} source(s) -> {width} x {height} px @ "
          f"{gsd_used:.3e} deg/px (EPSG:4326)")
    missing = _composite_and_write(order, width, height, transform, out_path)
    if missing > 0:
        print(f"[mosaic] {missing * 100.0:.1f}% of the cell uncovered by any source (black fill)")

    return {
        "path": str(out_path),
        "width": width,
        "height": height,
        "gsd_deg": gsd_used,
        "n_sources": len(order),
        "missing_fraction": missing,
    }


__all__ = [
    "NEAR_BLACK_RGB_MEAN",
    "MAX_MOSAIC_SIDE_PX",
    "resolve_target_gsd_deg",
    "compute_grid",
    "build_mosaic",
]
