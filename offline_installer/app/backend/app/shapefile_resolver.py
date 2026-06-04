"""Smart-trim resolver for the 6-material MEA pipeline (buildings & roads).

Buildings and roads are pulled per-raster from an Esri enterprise
geodatabase (the ``sde`` block of ``shapefile_config.json``) via
``sde_extractor``. At classification time this resolver:

  1. Reads the ortho raster's bounds + CRS.
  2. Hands the buffered bounds to ``sde_extractor``, which returns one
     trimmed ``.shp`` per tile (already restricted to the ortho footprint).
  3. Reads each tile, reprojects features to the raster's CRS, and
     concatenates them (per-feature attributes are preserved).
  4. For roads, buffers the LINE geometries into road-width polygons (see
     ``_buffer_road_lines``) using the configured width attribute.
  5. Writes the result to a fresh temp ``.shp`` and returns its path so the
     existing ``_rasterise_user_shapefile_to_mask`` consumes it unchanged.

Returns ``None`` when nothing resolves, so the caller can fall back to
SAM3 / no-mask. (Water is handled separately as a raster mask — see
``shapefile_config.get_water_mask``.)
"""
from __future__ import annotations

import atexit
import math
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from . import sde_extractor, shapefile_config

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
) -> Optional[str]:
    """Return a single ``.shp`` path ready for the existing rasterizer.

    Unions the SDE-extracted features for ``feature_type`` whose envelopes
    intersect ``raster_path``'s buffered bounds, writes the union to a temp
    ``.shp`` (buffering road lines to polygons for ``feature_type == "roads"``),
    and returns that path.

    Returns ``None`` when:
      - no SDE features resolve, OR
      - none of them intersect the ortho, OR
      - the ortho can't be opened (rasterio failure).
    """
    print(f"[debug-resolver] >>> resolve_shapefile(raster={Path(raster_path).name!r}, "
          f"feature_type={feature_type!r})")

    try:
        import rasterio
        from rasterio.warp import transform_bounds
        import geopandas as gpd
        import pandas as pd
        import pyogrio
    except Exception as exc:
        print(f"[shapefile_resolver] missing geo deps ({exc})")
        print(f"[debug-resolver] EXIT: missing geo deps -> returning None")
        return None

    try:
        with rasterio.open(raster_path) as src:
            raster_crs = src.crs
            raster_bounds = src.bounds  # (left, bottom, right, top)
    except Exception as exc:
        print(f"[shapefile_resolver] cannot open raster {raster_path}: {exc}")
        print(f"[debug-resolver] EXIT: rasterio.open failed -> returning None")
        return None

    print(f"[debug-resolver] raster CRS={raster_crs}  bounds={raster_bounds}")

    if raster_crs is None:
        print(f"[shapefile_resolver] raster {raster_path} has no CRS — cannot trim")
        print(f"[debug-resolver] EXIT: raster has no CRS -> returning None")
        return None

    buf = _bounds_buffer_in_raster_units(raster_crs)
    raster_bounds_buf = (
        raster_bounds.left   - buf,
        raster_bounds.bottom - buf,
        raster_bounds.right  + buf,
        raster_bounds.top    + buf,
    )
    print(f"[debug-resolver] buffer in raster units = {buf}  buffered_bounds={raster_bounds_buf}")

    # SDE extraction (Path B stopgap): if shapefile_config.json has an
    # enabled ``sde`` block, pull features for this feature_type from the
    # configured layer via an arcpy subprocess. The worker tiles the
    # bounds (default 5 km) so we don't trip the 2 GB / ~1M-row
    # shapefile ceiling, and returns one .shp per tile. We treat those
    # tiles as additional inputs to the union loop below — same code
    # path that handles file-based shapefiles.
    sde_wkid = raster_crs.to_epsg() if raster_crs else None
    print(f"[debug-resolver] handing off to sde_extractor with wkid={sde_wkid}")
    sde_shps = sde_extractor.extract_to_shapefiles(
        feature_type, raster_bounds_buf, sde_wkid, Path(raster_path).stem,
    )
    print(f"[debug-resolver] sde_extractor returned {len(sde_shps)} shapefile(s)")

    paths = list(sde_shps)
    print(f"[debug-resolver] combined paths ({len(paths)}): {paths}")
    if not paths:
        print(f"[debug-resolver] EXIT: no SDE tiles AND no file shapefiles -> returning None "
              f"(this is why _acquire_mask saw user_shapefile=None for {feature_type!r})")
        return None

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

    # Roads arrive from SDE as LINE geometries — buffer each to a polygon the
    # width of the road before rasterising (a bare line burns a 1-px
    # centreline). Width per feature comes from the configured attribute, else
    # the fallback; both are metres, converted to raster units.
    if feature_type == "roads":
        sde_cfg = shapefile_config.get_sde()
        width_attr = str(sde_cfg.get("road_width_attr") or "")
        fallback_m = float(sde_cfg.get("road_width_fallback_m") or 2.0)
        merged = _buffer_road_lines(
            merged, raster_crs, width_attr, fallback_m,
            type_attr=str(sde_cfg.get("Road_Type_Attr") or ""),
            main_key=str(sde_cfg.get("Road_Type_Key_MainRoad") or ""),
            main_w=float(sde_cfg.get("Road_Type_Width_MainRoad_m") or 0.0),
            side_key=str(sde_cfg.get("Road_Type_Key_SideRoad") or ""),
            side_w=float(sde_cfg.get("Road_Type_Width_SideRoad_m") or 0.0),
        )
        if merged.empty:
            print(f"[shapefile_resolver] roads: nothing left after buffering -> None")
            return None

    return _write_temp_shapefile(merged, feature_type, Path(raster_path).stem)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _metres_to_crs_units(metres: float, raster_crs) -> float:
    """Convert a distance in metres to the raster CRS's horizontal units.

    Geographic CRS (degrees) → approximate 1 degree as 111_320 m at the
    equator. Projected CRS → divide by the CRS's linear-unit factor
    (metre = 1, US-survey-foot ≈ 0.3048). Falls back to the raw metres
    value on any error.
    """
    try:
        if raster_crs.is_geographic:
            return metres / 111_320.0
        unit_factor = raster_crs.linear_units_factor[1] if hasattr(raster_crs, "linear_units_factor") else 1.0
        return metres / unit_factor if unit_factor else metres
    except Exception:
        return metres


def _bounds_buffer_in_raster_units(raster_crs) -> float:
    """Return the per-axis bounds buffer (50 m) in the raster CRS's units."""
    return _metres_to_crs_units(_BOUNDS_BUFFER_METRES, raster_crs)


def _as_positive_float(value) -> Optional[float]:
    """Return ``float(value)`` when it parses to a number > 0, else ``None``.

    NaN (geopandas fills missing numeric cells with NaN) is treated as
    absent — ``nan > 0`` is False.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _type_matches(type_val, key) -> bool:
    """Case-insensitive, whitespace-trimmed match of a feature's road-type
    value against a configured key.

    A blank/None key or a blank/None feature value never matches, so an
    unconfigured road-type tier is simply skipped.
    """
    if key is None:
        return False
    key_s = str(key).strip()
    if not key_s:
        return False
    if type_val is None:
        return False
    val_s = str(type_val).strip()
    if not val_s:
        return False
    return val_s.lower() == key_s.lower()


def _road_width_m(
    width_val,
    type_val,
    *,
    main_key,
    main_w,
    side_key,
    side_w,
    fallback_m,
) -> float:
    """Return the full road width (metres) for one road feature.

    3-tier fallback:
      1. ``width_val`` (the ``road_width_attr`` value) when it is a number > 0.
      2. else ``type_val`` matched against the configured main/side road-type
         keys -> ``main_w`` / ``side_w`` (each used only when > 0). Main wins
         when both keys are equal.
      3. else ``fallback_m``.
    """
    explicit = _as_positive_float(width_val)
    if explicit is not None:
        return explicit

    if _type_matches(type_val, main_key):
        mw = _as_positive_float(main_w)
        if mw is not None:
            return mw
    if _type_matches(type_val, side_key):
        sw = _as_positive_float(side_w)
        if sw is not None:
            return sw

    return float(fallback_m)


def _buffer_road_lines(
    gdf,
    raster_crs,
    width_attr: str,
    fallback_m: float,
    *,
    type_attr: str = "",
    main_key: str = "",
    main_w: float = 0.0,
    side_key: str = "",
    side_w: float = 0.0,
):
    """Buffer road LINE geometries into road-width polygons.

    Each feature is buffered by HALF of the full width returned by
    ``_road_width_m`` — the explicit ``width_attr`` value, else a road-type
    (``type_attr`` -> main/side) width, else ``fallback_m`` (all metres).
    Half-widths are converted from metres to the raster CRS's units so a 2 m
    road stays 2 m wide instead of 2 degrees on a geographic CRS. Flat
    end-caps (cap_style="flat") avoid overshoot past endpoints; round joins
    keep bends connected.

    With no road-type config (``type_attr``/keys blank) this reduces to the
    original two-tier behaviour: ``width_attr`` else ``fallback_m``.
    """
    n = len(gdf)
    width_vals = list(gdf[width_attr]) if (width_attr and width_attr in gdf.columns) else [None] * n
    type_vals = list(gdf[type_attr]) if (type_attr and type_attr in gdf.columns) else [None] * n

    if width_attr and width_attr not in gdf.columns:
        print(f"[shapefile_resolver] roads: width attr {width_attr!r} not found in "
              f"columns {list(gdf.columns)} — falling back to road-type / {fallback_m} m")
    if type_attr and type_attr not in gdf.columns:
        print(f"[shapefile_resolver] roads: type attr {type_attr!r} not found in "
              f"columns {list(gdf.columns)} — road-type tier disabled")

    distances = [
        _metres_to_crs_units(
            _road_width_m(w, t, main_key=main_key, main_w=main_w,
                          side_key=side_key, side_w=side_w, fallback_m=fallback_m) / 2.0,
            raster_crs,
        )
        for w, t in zip(width_vals, type_vals)
    ]

    out = gdf.copy()
    out["geometry"] = gdf.geometry.buffer(distances, cap_style="flat")
    out = out[~(out.geometry.is_empty | out.geometry.isna())]
    print(f"[shapefile_resolver] roads: buffered {len(out)} feature(s) to polygons "
          f"(attr={width_attr or '—'}, type={type_attr or '—'}, fallback={fallback_m} m)")
    return out


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
