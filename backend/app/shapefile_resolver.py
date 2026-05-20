"""Smart-trim shapefile resolver for the 6-material MEA pipeline.

For each feature type (buildings / roads / water) the user can configure
multiple source shapefiles (one per state, region, etc.) in
``shapefile_config.json``.  At classification time, this resolver:

  1. Reads the ortho raster's bounds + CRS.
  2. For each configured shapefile, checks whether its envelope intersects
     the ortho bounds (in the shapefile's own CRS, with a small buffer in
     metres so features straddling the ortho edge are kept whole).
  3. For shapefiles that intersect, reads only the features whose geometry
     intersects the buffered bounds — geopandas/fiona use the ``.shx``
     spatial index for this, so the read is fast even on multi-GB inputs.
  4. Reprojects each batch of features to the raster's CRS.
  5. Concatenates everything into a single GeoDataFrame and writes it to
     a fresh temp ``.shp`` (with sidecar ``.shx`` / ``.dbf`` / ``.prj``).
  6. Returns the temp path so the existing ``_rasterise_user_shapefile_to_mask``
     code path can consume it unchanged.

If the user passes an explicit override path (CLI flag), that path wins
unconditionally — no trim, no union — because the user has signalled
they know what they're doing.

If no shapefiles are configured (or none intersect the ortho), returns
``None`` so the caller can fall back to SAM3 / no-mask.
"""
from __future__ import annotations

import atexit
import math
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from . import shapefile_config

# Buffer applied to the ortho bounds when filtering features. Keeps any
# feature that just barely touches the edge whole, instead of dropping it.
# 50 m is a safe default for aerial work (typical building / road width).
_BOUNDS_BUFFER_METRES = 50.0

# Temp dirs created by _write_temp_shapefile. Registered with atexit so
# they don't accumulate across long batch runs.
_TEMP_DIRS: List[str] = []


def _cleanup_temp_dirs() -> None:
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_temp_dirs)


def resolve_shapefile(
    raster_path: str,
    feature_type: str,
    override: Optional[str] = None,
) -> Optional[str]:
    """Return a single ``.shp`` path ready for the existing rasterizer.

    ``override`` (if provided and existing) wins outright. Otherwise the
    resolver unions all configured shapefiles for ``feature_type`` whose
    envelopes intersect ``raster_path``'s buffered bounds, writes the
    union to a temp ``.shp``, and returns that path.

    Returns ``None`` when:
      - no override and no configured shapefiles, OR
      - none of the configured shapefiles intersect the ortho, OR
      - the ortho can't be opened (rasterio failure).
    """
    if override:
        if Path(override).exists():
            return override
        # An explicit override that doesn't exist is a hard signal — the
        # user said "use this file". Falling back to config would silently
        # use a different shapefile, so return None and let _acquire_mask
        # decide (SAM3 fallback or no-mask).
        print(f"[shapefile_resolver] override missing — no fallback: {override}")
        return None

    paths = shapefile_config.get(feature_type)
    if not paths:
        return None

    try:
        import rasterio
        from rasterio.warp import transform_bounds
        import geopandas as gpd
        import pandas as pd
        import pyogrio
    except Exception as exc:
        print(f"[shapefile_resolver] missing geo deps ({exc})")
        return None

    try:
        with rasterio.open(raster_path) as src:
            raster_crs = src.crs
            raster_bounds = src.bounds  # (left, bottom, right, top)
    except Exception as exc:
        print(f"[shapefile_resolver] cannot open raster {raster_path}: {exc}")
        return None

    if raster_crs is None:
        print(f"[shapefile_resolver] raster {raster_path} has no CRS — cannot trim")
        return None

    buf = _bounds_buffer_in_raster_units(raster_crs)
    raster_bounds_buf = (
        raster_bounds.left   - buf,
        raster_bounds.bottom - buf,
        raster_bounds.right  + buf,
        raster_bounds.top    + buf,
    )

    matched_gdfs: List["gpd.GeoDataFrame"] = []
    for sp in paths:
        sp_path = Path(sp)
        if not sp_path.exists():
            print(f"[shapefile_resolver] {feature_type}: missing {sp_path}")
            continue

        try:
            info = pyogrio.read_info(str(sp_path))
            shp_crs = info.get("crs")
            shp_bounds = info.get("total_bounds")  # (minx, miny, maxx, maxy)
            if shp_bounds is None or any(math.isnan(v) for v in shp_bounds):
                print(f"[shapefile_resolver] {feature_type}: {sp_path.name} has no usable bounds — skipping")
                continue
        except Exception as exc:
            print(f"[shapefile_resolver] {feature_type}: cannot read header of {sp_path}: {exc}")
            continue

        # If the shapefile has no .prj we can't reproject the ortho's bounds
        # into its coordinate space, so the bbox filter would silently drop
        # features (or load wrong ones). Better to skip with a clear warning.
        if shp_crs is None:
            print(f"[shapefile_resolver] {feature_type}: {sp_path.name} has no CRS (.prj missing) — skipping")
            continue

        bbox_in_shp_crs = transform_bounds(raster_crs, shp_crs, *raster_bounds_buf, densify_pts=21) \
            if shp_crs != raster_crs else tuple(raster_bounds_buf)
        if not _bbox_intersects(bbox_in_shp_crs, shp_bounds):
            print(f"[shapefile_resolver] {feature_type}: {sp_path.name} does not intersect ortho bounds")
            continue

        try:
            gdf = gpd.read_file(str(sp_path), bbox=bbox_in_shp_crs)
        except Exception as exc:
            print(f"[shapefile_resolver] {feature_type}: gpd.read_file failed for {sp_path}: {exc}")
            continue

        if gdf.empty:
            continue

        if gdf.crs is not None and gdf.crs != raster_crs:
            gdf = gdf.to_crs(raster_crs)

        matched_gdfs.append(gdf)
        print(f"[shapefile_resolver] {feature_type}: kept {len(gdf)} feature(s) from {sp_path.name}")

    if not matched_gdfs:
        print(f"[shapefile_resolver] {feature_type}: no features intersected ortho bounds")
        return None

    if len(matched_gdfs) == 1:
        merged = matched_gdfs[0]
    else:
        merged = gpd.GeoDataFrame(
            pd.concat(matched_gdfs, ignore_index=True, sort=False),
            crs=raster_crs,
        )

    return _write_temp_shapefile(merged, feature_type, Path(raster_path).stem)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _bounds_buffer_in_raster_units(raster_crs) -> float:
    """Return the per-axis bounds buffer expressed in the raster CRS's
    horizontal units.

    Projected CRS in metres or feet → convert _BOUNDS_BUFFER_METRES.
    Geographic CRS (degrees) → convert by approximating 1 degree latitude
    as 111_320 m at the equator. This is fine for a 50 m buffer; the
    actual filter still uses real geometry intersection downstream.
    """
    try:
        if raster_crs.is_geographic:
            return _BOUNDS_BUFFER_METRES / 111_320.0
        # projected — find unit (default to metre)
        unit_factor = raster_crs.linear_units_factor[1] if hasattr(raster_crs, "linear_units_factor") else 1.0
        return _BOUNDS_BUFFER_METRES / unit_factor if unit_factor else _BOUNDS_BUFFER_METRES
    except Exception:
        return _BOUNDS_BUFFER_METRES


def _bbox_intersects(a, b) -> bool:
    """Return True iff two (minx, miny, maxx, maxy) bboxes overlap."""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _write_temp_shapefile(gdf, feature_type: str, raster_stem: str) -> str:
    """Write ``gdf`` to a fresh temp .shp and return its path.

    Each invocation gets its own dir so concurrent runs don't collide.
    The dir is registered for atexit cleanup so we don't leak temp dirs
    across long batch runs.
    """
    tmp_dir = tempfile.mkdtemp(prefix=f"shp_resolver_{feature_type}_")
    _TEMP_DIRS.append(tmp_dir)
    out = Path(tmp_dir) / f"{raster_stem}_{feature_type}_trimmed.shp"
    gdf.to_file(str(out), driver="ESRI Shapefile")
    print(f"[shapefile_resolver] {feature_type}: wrote trimmed shapefile -> {out}")
    return str(out)
