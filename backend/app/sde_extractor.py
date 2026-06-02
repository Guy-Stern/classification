"""SDE feature extraction orchestrator (Path B stopgap).

Pulls building / road / water features from an Esri enterprise geodatabase
by shelling out to an arcpy worker (``sde_arcpy_worker.py``) under ArcGIS
Pro's bundled Python. Returns a list of per-tile shapefile paths; the
caller (``shapefile_resolver.resolve_shapefile``) then unions them with
any configured file-based shapefiles via its existing concat loop.

Why tile?
    Shapefiles cap at 2 GB per ``.shp`` / ``.dbf`` (signed 32-bit offset)
    and ``arcpy.CopyFeatures_management`` silently truncates at the wall.
    Large urban orthos cover enough area to risk this, so we always
    split the raster's buffered bounds into N×M tiles of at most
    ``tile_size_metres`` per side (default 5 km) and let arcpy emit one
    shp per tile. The resolver's existing list-of-paths handling unions
    them downstream.

This module runs in the main venv — it does **not** import arcpy.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from . import shapefile_config

_DEFAULT_TILE_SIZE_M = 5000.0

# Default worker (arcpy-based). Override with the MC_SDE_WORKER_SCRIPT env
# var for testing without ArcGIS Pro (see tools/_sde_mock/mock_worker.py).
_WORKER_SCRIPT = Path(
    os.environ.get("MC_SDE_WORKER_SCRIPT")
    or (Path(__file__).resolve().parent / "sde_arcpy_worker.py")
)


def extract_to_shapefiles(
    feature_type: str,
    raster_bounds: Tuple[float, float, float, float],
    raster_crs_wkid: Optional[int],
    raster_stem: str,
) -> List[str]:
    """Return per-tile shapefile paths for ``feature_type`` within ``raster_bounds``.

    ``raster_bounds`` is ``(minx, miny, maxx, maxy)`` in the raster's CRS,
    already buffered by the caller. ``raster_crs_wkid`` is the raster's
    EPSG code (or None if the raster has no EPSG-defined CRS — the
    worker will fall back to letting arcpy infer the SR from the layer).

    Returns ``[]`` when SDE is not configured for this feature type, when
    the configured connection / arcpy-python paths are invalid, or when
    the subprocess fails. The caller falls through to the file-based
    resolver path in those cases.
    """
    print(f"[debug-sde] >>> extract_to_shapefiles(feature_type={feature_type!r}, "
          f"bounds={raster_bounds}, wkid={raster_crs_wkid}, stem={raster_stem!r})")
    print(f"[debug-sde] worker script = {_WORKER_SCRIPT}")
    print(f"[debug-sde] worker script exists? {Path(_WORKER_SCRIPT).exists()}")

    sde_cfg = shapefile_config.get_sde()
    print(f"[debug-sde] sde_cfg.enabled         = {sde_cfg.get('enabled')!r}")
    print(f"[debug-sde] sde_cfg.connection_file = {sde_cfg.get('connection_file')!r}")
    print(f"[debug-sde] sde_cfg.arcpy_python    = {sde_cfg.get('arcpy_python')!r}")
    print(f"[debug-sde] sde_cfg.layers          = {sde_cfg.get('layers')!r}")

    if not sde_cfg.get("enabled"):
        print(f"[debug-sde] GATE 1 TRIPPED: sde.enabled is falsy ({sde_cfg.get('enabled')!r}) "
              f"-> returning [] (will fall back to file-shapefiles / SAM3)")
        return []

    layer_name = (sde_cfg.get("layers") or {}).get(feature_type, "")
    print(f"[debug-sde] resolved layer for {feature_type!r} = {layer_name!r}")
    if not layer_name:
        print(f"[debug-sde] GATE 2 TRIPPED: layers[{feature_type!r}] is empty "
              f"-> returning [] (check the 'layers' object in shapefile_config.json)")
        return []

    connection = sde_cfg.get("connection_file") or ""
    print(f"[debug-sde] connection_file = {connection!r}  exists={Path(connection).exists() if connection else False}")
    if not connection or not Path(connection).exists():
        print(f"[sde_extractor] {feature_type}: connection_file missing or unset ({connection!r})")
        print(f"[debug-sde] GATE 3 TRIPPED: connection_file missing -> returning []")
        return []

    arcpy_python = sde_cfg.get("arcpy_python") or ""
    print(f"[debug-sde] arcpy_python = {arcpy_python!r}  exists={Path(arcpy_python).exists() if arcpy_python else False}")
    if not arcpy_python or not Path(arcpy_python).exists():
        print(f"[sde_extractor] {feature_type}: arcpy_python missing or unset ({arcpy_python!r})")
        print(f"[debug-sde] GATE 4 TRIPPED: arcpy_python missing -> returning []")
        return []

    tile_size = _coerce_tile_size(sde_cfg.get("tile_size_metres"))
    timeout = _coerce_timeout(sde_cfg.get("timeout_seconds"))
    print(f"[debug-sde] tile_size_metres (configured)  = {tile_size}")
    print(f"[debug-sde] timeout_seconds                = {timeout}")

    tile_size_native = _metres_to_raster_units(tile_size, raster_crs_wkid)
    print(f"[debug-sde] tile_size in raster CRS units  = {tile_size_native}  (wkid={raster_crs_wkid})")
    tiles_wkt = _tile_bounds(raster_bounds, tile_size_native)
    print(f"[debug-sde] generated {len(tiles_wkt)} tile WKT(s) from bounds {raster_bounds}")
    if not tiles_wkt:
        print(f"[debug-sde] GATE 5 TRIPPED: zero tiles generated "
              f"(degenerate bounds or zero tile_size?) -> returning []")
        return []

    out_dir = tempfile.mkdtemp(prefix=f"sde_extract_{feature_type}_{raster_stem}_")
    print(f"[debug-sde] tile shapefile out_dir = {out_dir}")
    spec = {
        "sde_connection": connection,
        "layer": layer_name,
        "out_dir": out_dir,
        "sr_wkid": int(raster_crs_wkid) if raster_crs_wkid else None,
        "tiles": [{"id": idx, "wkt": wkt} for idx, wkt in enumerate(tiles_wkt)],
    }
    print(f"[debug-sde] spec.sde_connection = {spec['sde_connection']!r}")
    print(f"[debug-sde] spec.layer          = {spec['layer']!r}")
    print(f"[debug-sde] spec.sr_wkid        = {spec['sr_wkid']!r}")
    print(f"[debug-sde] spec.tiles          = {len(spec['tiles'])} entry/entries")

    print(
        f"[sde_extractor] {feature_type}: extracting {layer_name!r} into "
        f"{len(tiles_wkt)} tile(s) via {Path(arcpy_python).name}"
    )
    print(f"[debug-sde] launching subprocess: {arcpy_python} {_WORKER_SCRIPT}")

    try:
        proc = subprocess.run(
            [arcpy_python, str(_WORKER_SCRIPT)],
            input=json.dumps(spec),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        print(f"[debug-sde] subprocess returned exit={proc.returncode}  "
              f"stdout_len={len(proc.stdout)}  stderr_len={len(proc.stderr)}")
    except subprocess.TimeoutExpired:
        print(f"[sde_extractor] {feature_type}: worker timed out after {timeout}s")
        print(f"[debug-sde] EXCEPTION: subprocess.TimeoutExpired after {timeout}s -> returning []")
        return []
    except Exception as exc:
        print(f"[sde_extractor] {feature_type}: subprocess failed: {exc}")
        print(f"[debug-sde] EXCEPTION launching subprocess: {type(exc).__name__}: {exc}")
        import traceback as _tb
        print(f"[debug-sde] traceback:\n{_tb.format_exc()}")
        return []

    if proc.returncode != 0:
        # The worker still writes a JSON {fatal: ...} on most failure
        # paths — try to surface it before bailing.
        print(f"[sde_extractor] {feature_type}: worker exit {proc.returncode}")
        if proc.stderr:
            print(f"[sde_extractor] stderr: {proc.stderr[:500]}")
        print(f"[debug-sde] full stderr ({len(proc.stderr)} chars):")
        print(proc.stderr)
        print(f"[debug-sde] full stdout ({len(proc.stdout)} chars):")
        print(proc.stdout)

    try:
        result = json.loads(proc.stdout)
        print(f"[debug-sde] parsed worker JSON: fatal={result.get('fatal')!r}  "
              f"results_count={len(result.get('results', []))}")
    except Exception as exc:
        print(
            f"[sde_extractor] {feature_type}: cannot parse worker output "
            f"(stdout head: {proc.stdout[:200]!r})"
        )
        print(f"[debug-sde] JSON parse failed: {type(exc).__name__}: {exc}")
        print(f"[debug-sde] full stdout:\n{proc.stdout}")
        print(f"[debug-sde] full stderr:\n{proc.stderr}")
        return []

    fatal = result.get("fatal")
    if fatal:
        print(f"[sde_extractor] {feature_type}: fatal — {fatal.get('error')}")
        print(f"[debug-sde] fatal trace from worker:\n{fatal.get('trace', '(no trace)')}")
        return []

    shps: List[str] = []
    for entry in result.get("results", []):
        if entry.get("ok") and entry.get("shp") and Path(entry["shp"]).exists():
            shps.append(entry["shp"])
            print(
                f"[sde_extractor] {feature_type}: tile {entry['id']} kept "
                f"{entry.get('count', '?')} feature(s) -> {Path(entry['shp']).name}"
            )
        elif entry.get("ok"):
            # ok=True but no shp -> tile had 0 features (worker chose not to write)
            print(f"[debug-sde] {feature_type}: tile {entry.get('id')} ok with 0 features (no shapefile written)")
        elif not entry.get("ok"):
            print(
                f"[sde_extractor] {feature_type}: tile {entry.get('id')} failed — "
                f"{entry.get('error', 'unknown error')}"
            )
            if entry.get("trace"):
                print(f"[debug-sde] tile {entry.get('id')} trace:\n{entry['trace']}")

    if not shps:
        print(f"[sde_extractor] {feature_type}: no tiles produced features")

    print(f"[debug-sde] <<< extract_to_shapefiles({feature_type!r}) returning {len(shps)} shapefile(s)")
    return shps


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _tile_bounds(
    bounds: Tuple[float, float, float, float], tile_size: float,
) -> List[str]:
    """Split a bbox into a grid of at-most ``tile_size``-side rectangles.

    Returns a list of WKT POLYGON strings, one per tile. Edge tiles can
    be smaller than ``tile_size`` (we clip to ``bounds`` so we don't
    over-query).
    """
    minx, miny, maxx, maxy = bounds
    width = max(0.0, maxx - minx)
    height = max(0.0, maxy - miny)
    if width == 0.0 or height == 0.0 or tile_size <= 0.0:
        return []

    nx = max(1, math.ceil(width / tile_size))
    ny = max(1, math.ceil(height / tile_size))

    tiles: List[str] = []
    for iy in range(ny):
        y0 = miny + iy * tile_size
        y1 = min(maxy, y0 + tile_size)
        for ix in range(nx):
            x0 = minx + ix * tile_size
            x1 = min(maxx, x0 + tile_size)
            tiles.append(
                f"POLYGON(({x0} {y0}, {x1} {y0}, {x1} {y1}, {x0} {y1}, {x0} {y0}))"
            )
    return tiles


def _metres_to_raster_units(tile_size_metres: float, raster_crs_wkid: Optional[int]) -> float:
    """Convert a tile size in metres to the raster CRS's horizontal units.

    Geographic CRS (degrees) → approximate 1 degree as 111_320 m. This is
    a few % off at high latitudes but is fine for a 5 km tile budget; the
    bbox query downstream uses real geometry intersection.

    Projected CRS → assume metres (true for almost all projected CRS in
    practice; we don't try to detect feet etc.).

    Unknown / no WKID → fall back to the raw value (matches pre-fix
    behaviour for projected SDE workspaces).
    """
    if not raster_crs_wkid:
        return tile_size_metres
    try:
        from pyproj import CRS as _CRS
        crs = _CRS.from_epsg(int(raster_crs_wkid))
        if crs.is_geographic:
            return tile_size_metres / 111_320.0
    except Exception:
        pass
    return tile_size_metres


def _coerce_tile_size(value) -> float:
    try:
        size = float(value)
        return size if size > 0 else _DEFAULT_TILE_SIZE_M
    except (TypeError, ValueError):
        return _DEFAULT_TILE_SIZE_M


def _coerce_timeout(value) -> int:
    try:
        secs = int(value)
        return secs if secs > 0 else 1800
    except (TypeError, ValueError):
        return 1800
