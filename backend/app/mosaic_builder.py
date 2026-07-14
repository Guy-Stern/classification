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

**Tier 0 perf (windowed + parallel):** each source is warped only into the
sub-window of the target grid its own WGS-84 footprint covers — not the full
cell — so a source touching one corner costs one corner's worth of warp, not a
whole 20000² frame. The per-source reads run on a ``ThreadPoolExecutor`` (GDAL
releases the GIL during warp/read); the worker count is derived from
``_COMPOSITE_RAM_BUDGET`` and the largest window so a few full-cell layers can't
multiply into an OOM. Results are composited into the shared canvas on the main
thread in strict priority order, preserving the exact last-wins semantics.

Scale: a full-resolution 1° cell can be enormous, so ``MAX_MOSAIC_SIDE_PX``
caps the output dimensions — a mosaic that would exceed the cap has its GSD
coarsened (with a loud warning) rather than exploding to hundreds of GB. The
canvas is still allocated whole (one ``(3, H, W)`` uint8 + a ``(H, W)`` mask);
the cap keeps that bounded. The write step is isolated in
:func:`_composite_and_write` so a future per-block streaming writer (Tier 2) can
replace it without touching the catalog/grid math.

Needs ``rasterio`` + ``numpy`` (both import under the geo-only test
interpreter). Deliberately does NOT import ``core`` — see ``NEAR_BLACK_RGB_MEAN``
below — so this module stays importable without the sklearn/skimage/geopandas
chain and unit-tests without torch.
"""

from __future__ import annotations

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from rasterio import windows as _rio_windows
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

# Peak-RAM budget (bytes) for the concurrent source-window reads during the
# composite. The worker count is derived so ``workers * largest_window_bytes``
# stays under this — a handful of full-cell (low-priority) layers read in
# parallel therefore can't multiply into an OOM; they just read with fewer
# workers. Independent of the persistent canvas allocation. Tune here.
_COMPOSITE_RAM_BUDGET = 3_000_000_000


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


# A composite target window: (col0, row0, w, h, win_transform).
_Window = tuple[int, int, int, int, "rasterio.Affine"]


def _source_target_window(
    src_bounds: tuple[float, float, float, float],
    cell_bounds: tuple[float, float, float, float],
    gsd: float,
    width: int,
    height: int,
    transform: "rasterio.Affine",
    pad: int = 1,
) -> _Window | None:
    """Pixel window of the target grid a source can touch → ``(col0,row0,w,h,win_tf)``.

    Intersects the source's WGS-84 footprint with the cell, converts that to a
    padded, clipped pixel rectangle on the north-up target grid, and derives the
    sub-grid affine. Returns ``None`` when the intersection is empty (a
    touch-only or fully-outside source — normally already dropped by the catalog
    pre-filter, but cheap to guard here). The 1-px pad absorbs float rounding at
    the footprint edge so no boundary row/column is dropped.
    """
    sw, ss, se, sn = src_bounds
    cw, cs, ce, cn = cell_bounds
    iw, ie = max(sw, cw), min(se, ce)
    isth, into = max(ss, cs), min(sn, cn)
    if ie <= iw or into <= isth:
        return None

    west, north = cell_bounds[0], cell_bounds[3]
    col0 = int(math.floor((iw - west) / gsd)) - pad
    col1 = int(math.ceil((ie - west) / gsd)) + pad
    row0 = int(math.floor((north - into) / gsd)) - pad
    row1 = int(math.ceil((north - isth) / gsd)) + pad
    col0 = max(0, min(col0, width))
    col1 = max(0, min(col1, width))
    row0 = max(0, min(row0, height))
    row1 = max(0, min(row1, height))
    w, h = col1 - col0, row1 - row0
    if w <= 0 or h <= 0:
        return None

    win_transform = _rio_windows.transform(_rio_windows.Window(col0, row0, w, h), transform)
    return col0, row0, w, h, win_transform


def _pick_overview_level(overviews: list[int], downsample: float) -> int | None:
    """0-based ``OVERVIEW_LEVEL`` of the largest decimation factor ``<= downsample``.

    ``overviews`` is rasterio's ``src.overviews(band)`` list of decimation
    factors (e.g. ``[2, 4, 8, 16]``). Returns the index of the coarsest overview
    that is still at least as fine as the target grid, or ``None`` to read full
    resolution (no overviews, or the target is finer than the first level). Never
    picks an overview coarser than the target, so the subsequent ``average`` warp
    only ever downsamples slightly — never upsamples from an overview.
    """
    lvl: int | None = None
    for i, factor in enumerate(overviews):
        if factor <= downsample:
            lvl = i
    return lvl


def _read_source_window(
    src_path: Path,
    win_transform: "rasterio.Affine",
    w: int,
    h: int,
    src_gsd_deg: float = 0.0,
    target_gsd_deg: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Reproject one source onto a ``(h, w)`` sub-grid → ``((3,h,w) uint8, (h,w) bool)``.

    The bool mask is True where the source contributes a real pixel: inside the
    source's data area (WarpedVRT alpha > 0) AND not a near-black nodata/border
    pixel. Out-of-extent and border pixels come back False so a partial-coverage
    or black-edged source doesn't overwrite an earlier (lower-priority) source.

    Validity comes from the WarpedVRT alpha band (GDAL sets 255 inside data, 0
    outside, and encodes source nodata into it too). ``masked=True`` is unsafe
    here: a source with ``nodata=None`` returns no mask and out-of-extent zeros
    would read as real data. If the source already has an alpha band,
    ``add_alpha=True`` errors, so we add one only when it's missing.

    Only the target window is materialised — GDAL reads just the source blocks
    that fall under ``win_transform``/``w``/``h``, so cost scales with the
    source's own footprint, not the whole cell.

    **Overview pinning (Tier 1):** when the source is being downsampled
    (``target_gsd_deg / src_gsd_deg >= 2``) and carries internal overviews, the
    source is opened at the coarsest overview still finer than the target
    (``OVERVIEW_LEVEL``). GDAL's ``average`` warp does NOT auto-select overviews
    effectively, so this is a large win — the warp then averages a handful of
    pre-decimated pixels per output pixel instead of scanning the full-res
    source (measured ~40x less read on 1 m orthos downsampled to a capped cell).
    The level depends only on the two GSDs, so it's identical for a windowed and
    a full-grid warp — window-invariance (and the serial-equivalence guarantee)
    is preserved.
    """
    open_kwargs: dict[str, str] = {}
    if src_gsd_deg > 0.0 and target_gsd_deg > 0.0:
        downsample = target_gsd_deg / src_gsd_deg
        if downsample >= 2.0:
            try:
                with rasterio.open(src_path) as _probe:
                    _lvl = _pick_overview_level(_probe.overviews(1), downsample)
                if _lvl is not None:
                    open_kwargs["OVERVIEW_LEVEL"] = str(_lvl)
            except Exception:
                open_kwargs = {}  # fall back to full-res read on any probe hiccup
    try:
        with rasterio.open(src_path, **open_kwargs) as src:
            if src.count < 3:
                raise ValueError(
                    f"source must have at least 3 bands (RGB); {src_path} has {src.count}"
                )
            if src.crs is None:
                raise ValueError(
                    f"source has no CRS; assign one with gdal_edit before ingest: {src_path}"
                )
            src_has_alpha = ColorInterp.alpha in src.colorinterp
            # AREA-AVERAGE (not bilinear): the mosaic grid is at/below the finest
            # source GSD, so every source is downsampled — average is the correct
            # anti-aliased kernel (bilinear undersamples/aliases at high downsample
            # ratios). It is also defined by each target pixel's footprint, so a
            # windowed warp is byte-identical to the full-grid warp — which is what
            # makes this parallel windowed composite provably equal to a serial
            # one (bilinear/cubic are extent-sensitive and would drift per window).
            with WarpedVRT(
                src,
                crs=_WGS84,
                transform=win_transform,
                width=w,
                height=h,
                resampling=Resampling.average,
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
    # pixel. mean over the 3 bands, same threshold core.py uses on input. float32
    # (not the numpy-default float64) halves this transient — it's window-local
    # now, but there can be many windows in flight.
    mean_rgb = rgb.mean(axis=0, dtype=np.float32)
    valid &= mean_rgb >= NEAR_BLACK_RGB_MEAN
    return np.ascontiguousarray(rgb), valid


def _composite_region(
    order: list[SourceEntry],
    region_bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    transform: "rasterio.Affine",
    gsd: float,
    force_workers: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Windowed, parallel, last-wins composite of ``order`` over one region.

    Returns ``(result (3,H,W) uint8, overall_valid (H,W) bool)`` for the grid
    covering ``region_bounds`` — the whole cell (:func:`_composite_and_write`) or
    a single output tile (:func:`build_mosaic_tiled`). Each source is warped only
    into the sub-window its footprint covers (:func:`_source_target_window`);
    reads run on a thread pool with bounded submit-ahead sized from
    ``_COMPOSITE_RAM_BUDGET`` and the largest window, and results are applied on
    THIS thread in strict ``order`` (priority ascending → priority=1 last) — so
    the last-wins result is byte-identical to a serial composite regardless of
    read-completion order. ``force_workers`` overrides the derived pool size; the
    tiled builder passes ``1`` so per-tile compositing doesn't nest under its own
    tile-level pool.
    """
    result = np.zeros((3, height, width), dtype=np.uint8)  # black fill
    overall_valid = np.zeros((height, width), dtype=bool)

    # Precompute each source's target window in priority order; drop empties.
    tasks: list[tuple[SourceEntry, _Window]] = []
    for entry in order:
        win = _source_target_window(
            entry.bounds_wgs84, region_bounds, gsd, width, height, transform
        )
        if win is not None:
            tasks.append((entry, win))
    if not tasks:
        return result, overall_valid

    n = len(tasks)
    if force_workers is not None:
        workers = max(1, min(force_workers, n))
    else:
        # Size the pool + read-ahead so concurrent window buffers stay under the
        # RAM budget. ~12 B/px covers the WarpedVRT read + contiguous rgb copy +
        # alpha + bool mask + float32 mean transient held per in-flight source.
        largest_px = max(w * h for (_, (_, _, w, h, _)) in tasks)
        per_src_bytes = max(1, largest_px * 12)
        workers = int(_COMPOSITE_RAM_BUDGET // per_src_bytes)
        workers = max(1, min(workers, os.cpu_count() or 1, n))

    def _apply(idx: int, rgb: np.ndarray, valid: np.ndarray) -> None:
        _entry, (c0, r0, w, h, _wt) = tasks[idx]
        sub = result[:, r0:r0 + h, c0:c0 + w]
        sub[:, valid] = rgb[:, valid]
        ov = overall_valid[r0:r0 + h, c0:c0 + w]
        ov[valid] = True

    if workers == 1:
        # Serial fast path (also what the tiled builder uses per tile) — no pool.
        for i in range(n):
            entry, (_c0, _r0, w, h, wt) = tasks[i]
            rgb, valid = _read_source_window(entry.path, wt, w, h, entry.pixel_size_deg, gsd)
            _apply(i, rgb, valid)
            del rgb, valid
        return result, overall_valid

    # Read each source's window on the pool but keep only ~`workers` reads in
    # flight at once (submit-ahead + apply-in-order + free-on-consume): a HARD cap
    # on peak RAM regardless of read/apply speed, so a handful of full-cell
    # low-priority layers can't accumulate into an OOM. Applying in strict
    # submission order (== composite order, priority=1 last) on this sole-mutator
    # thread keeps the parallel composite byte-identical to a serial one. An early
    # read failure wastes only the in-flight reads, not all N.
    with ThreadPoolExecutor(max_workers=workers) as ex:
        inflight: dict[int, object] = {}
        submitted = 0

        def _submit_through(upto: int) -> None:
            nonlocal submitted
            while submitted < n and submitted <= upto:
                entry, (_c0, _r0, w, h, wt) = tasks[submitted]
                inflight[submitted] = ex.submit(
                    _read_source_window, entry.path, wt, w, h,
                    entry.pixel_size_deg, gsd,
                )
                submitted += 1

        for i in range(n):
            _submit_through(i + workers)   # keep ~workers reads ahead of the cursor
            rgb, valid = inflight.pop(i).result()   # type: ignore[union-attr]
            _apply(i, rgb, valid)
            del rgb, valid

    return result, overall_valid


# Shared GeoTIFF profile for mosaic outputs. No nodata tag: the black (0,0,0)
# fill is exactly what core.py's near-black detection treats as border/nodata, so
# the mosaic looks like a real ortho to the classifier.
_MOSAIC_GTIFF = {
    "driver": "GTiff", "count": 3, "dtype": "uint8", "crs": _WGS84,
    "tiled": True, "blockxsize": 512, "blockysize": 512,
    "compress": "deflate", "zlevel": 1, "predictor": 2, "interleave": "band",
}


def _write_rgb_geotiff(out_path: Path, rgb: np.ndarray, transform: "rasterio.Affine") -> None:
    """Atomically write a 3-band uint8 tiled-DEFLATE GeoTIFF (tmp + os.replace)."""
    _, height, width = rgb.shape
    profile = {**_MOSAIC_GTIFF, "height": height, "width": width, "transform": transform}
    # PID in the temp name so two processes building the same geocell (scheduler
    # double-trigger, or a re-run while a prior run still reads the cache) can't
    # interleave writes into one .tmp; the FINAL name stays deterministic (resume
    # keys on it), and same-grid content makes the two os.replace()es idempotent.
    tmp = out_path.with_suffix(out_path.suffix + f".{os.getpid()}.tmp")
    try:
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(rgb)
        tmp.replace(out_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _composite_and_write(
    order: list[SourceEntry],
    width: int,
    height: int,
    transform: "rasterio.Affine",
    out_path: Path,
    gsd: float,
    cell_bounds: tuple[float, float, float, float],
) -> float:
    """Whole-cell composite of ``order`` → one tiled DEFLATE GeoTIFF.

    Returns the fraction of output pixels left uncovered (black fill).
    """
    result, overall_valid = _composite_region(order, cell_bounds, width, height, transform, gsd)
    _write_rgb_geotiff(out_path, result, transform)
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
    missing = _composite_and_write(
        order, width, height, transform, out_path, gsd_used, geocell_bounds
    )
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


def write_mosaic_vrt(
    vrt_path: str | Path,
    width: int,
    height: int,
    transform: "rasterio.Affine",
    crs_wkt: str,
    tiles: list[tuple[Path, int, int, int, int]],
) -> None:
    """Write a GDAL VRT (plain XML — no ``osgeo`` dependency) mosaicking ``tiles``.

    ``tiles`` is ``[(path, col0, row0, w, h), ...]`` placing each tile at its
    pixel offset in the ``width x height`` virtual raster. rasterio/GDAL read the
    VRT as one raster, so ``classify_v6`` can tile it directly — no giant
    intermediate ``.tif``. Uncovered regions (no tile) read back as 0 (black),
    exactly like the whole-cell mosaic's black fill.
    """
    from xml.sax.saxutils import escape

    a, b, c, d, e, f = (transform.a, transform.b, transform.c,
                        transform.d, transform.e, transform.f)
    gt = f"{c}, {a}, {b}, {f}, {d}, {e}"
    ci = {1: "Red", 2: "Green", 3: "Blue"}
    lines = [
        f'<VRTDataset rasterXSize="{width}" rasterYSize="{height}">',
        f'  <SRS>{escape(crs_wkt)}</SRS>',
        f'  <GeoTransform>{gt}</GeoTransform>',
    ]
    for band in (1, 2, 3):
        lines.append(f'  <VRTRasterBand dataType="Byte" band="{band}">')
        lines.append(f'    <ColorInterp>{ci[band]}</ColorInterp>')
        for (tp, c0, r0, tw, th) in tiles:
            lines.append('    <SimpleSource>')
            lines.append(
                f'      <SourceFilename relativeToVRT="0">{escape(str(tp))}</SourceFilename>')
            lines.append(f'      <SourceBand>{band}</SourceBand>')
            lines.append(f'      <SrcRect xOff="0" yOff="0" xSize="{tw}" ySize="{th}"/>')
            lines.append(f'      <DstRect xOff="{c0}" yOff="{r0}" xSize="{tw}" ySize="{th}"/>')
            lines.append('    </SimpleSource>')
        lines.append('  </VRTRasterBand>')
    lines.append('</VRTDataset>')
    Path(vrt_path).write_text("\n".join(lines), encoding="utf-8")


def _grid_signature(
    width: int,
    height: int,
    gsd_used: float,
    transform: "rasterio.Affine",
    tile_px: int,
    order: list[SourceEntry],
) -> dict:
    """Identity of a tiled-mosaic build: grid geometry + the ordered source set.

    Two builds with the same signature produce byte-identical tiles at the same
    ``r{r}_c{c}`` names, so an existing tile is safe to reuse (resume). A DIFFERENT
    signature — a changed ``max_side``/GSD/tile grid, or an added / edited /
    removed / re-prioritised source — means the cached tiles belong to a
    different geography; reusing them by index would silently place old pixels at
    new offsets in the VRT, so the cache must be purged first.
    """
    sources = []
    for e in order:
        try:
            st = e.path.stat()
            sources.append([str(e.path), st.st_size, st.st_mtime_ns, e.priority])
        except OSError:
            sources.append([str(e.path), -1, -1, e.priority])
    return {
        "v": 1,
        "width": width,
        "height": height,
        "tile_px": tile_px,
        "gsd_used": gsd_used,
        "transform": [transform.a, transform.b, transform.c,
                      transform.d, transform.e, transform.f],
        "sources": sources,
    }


def build_mosaic_tiled(
    entries: list[SourceEntry],
    geocell_bounds: tuple[float, float, float, float],
    out_dir: str | Path,
    *,
    tile_px: int = 2048,
    max_side_px: int = MAX_MOSAIC_SIDE_PX,
    tile_name_stem: str = "mosaic",
    progress_cb=None,
    overwrite: bool = False,
    tile_workers: int | None = None,
) -> dict:
    """Composite ``entries`` into a FOLDER of georeferenced RGB tiles + a ``.vrt``.

    The tiled counterpart of :func:`build_mosaic`: instead of one big in-RAM
    canvas + temp ``.tif``, the cell is split into ``tile_px``-side output tiles,
    each composited independently (only its overlapping sources, at the pinned
    overview level) and written atomically. A ``.vrt`` over the tiles is emitted
    so ``classify_v6`` can consume the mosaic as one raster with no intermediate
    full-frame write+read. Peak RAM is O(tile), not O(cell).

    Resume/cache: an existing tile file is reused as-is unless ``overwrite`` — so
    a re-run over the same geocell (or a crashed run) skips finished tiles.
    ``progress_cb(done, total, status)`` fires per tile (status in
    ``write|skip|empty``) so a long run visibly moves.

    Returns ``{vrt_path, tile_dir, width, height, gsd_deg, n_sources, n_tiles,
    n_written, n_skipped, n_empty, missing_fraction}``.
    """
    if not entries:
        raise ValueError("build_mosaic_tiled: no source entries (nothing intersects the geocell)")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gsd = resolve_target_gsd_deg(entries)
    width, height, gsd_used, transform = compute_grid(geocell_bounds, gsd, max_side_px)
    order = composite_order(entries)

    # Guard the resume cache: if a prior run's tiles were built for a DIFFERENT
    # grid/source set (changed max_side, a new finer-GSD source, edited sources),
    # purge them now — otherwise the VRT below would map stale tiles to the new
    # grid's offsets and silently produce a geographically wrong mosaic. The
    # signature is written before building so a crashed run still invalidates
    # correctly on a later differing re-run.
    sig = _grid_signature(width, height, gsd_used, transform, tile_px, order)
    sidecar = out_dir / f".{tile_name_stem}_grid.json"
    if not overwrite and sidecar.exists():
        try:
            stale = json.loads(sidecar.read_text(encoding="utf-8")) != sig
        except Exception:
            stale = True
        if stale:
            for p in out_dir.glob(f"{tile_name_stem}_mtile_*.tif"):
                p.unlink(missing_ok=True)
            (out_dir / f"{tile_name_stem}.vrt").unlink(missing_ok=True)
    try:
        sidecar.write_text(json.dumps(sig), encoding="utf-8")
    except Exception:
        pass

    jobs: list[tuple[int, int, int, int, int, int]] = []
    n_rows = math.ceil(height / tile_px)
    n_cols = math.ceil(width / tile_px)
    for tr_i in range(n_rows):
        for tc_i in range(n_cols):
            c0, r0 = tc_i * tile_px, tr_i * tile_px
            jobs.append((tr_i, tc_i, c0, r0, min(tile_px, width - c0), min(tile_px, height - r0)))
    total = len(jobs)

    def _one(job):
        tr_i, tc_i, c0, r0, tw, th = job
        tpath = out_dir / f"{tile_name_stem}_mtile_r{tr_i}_c{tc_i}.tif"
        window = _rio_windows.Window(c0, r0, tw, th)
        if tpath.exists() and not overwrite:
            return (tpath, c0, r0, tw, th, 0, "skip")   # resume: trust the finished tile
        tile_bounds = tuple(_rio_windows.bounds(window, transform))   # (w, s, e, n)
        tile_tr = _rio_windows.transform(window, transform)
        rgb, valid = _composite_region(order, tile_bounds, tw, th, tile_tr, gsd_used, force_workers=1)
        if not valid.any():
            return (None, c0, r0, tw, th, tw * th, "empty")   # no coverage → no tile file
        _write_rgb_geotiff(tpath, rgb, tile_tr)
        return (tpath, c0, r0, tw, th, int((~valid).sum()), "write")

    workers = tile_workers or max(1, min(os.cpu_count() or 1, total))
    written: list[tuple[Path, int, int, int, int]] = []
    uncovered_px = 0
    counts = {"write": 0, "skip": 0, "empty": 0}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for done, res in enumerate(ex.map(_one, jobs), start=1):
            tpath, c0, r0, tw, th, unc, status = res
            uncovered_px += unc
            counts[status] += 1
            if tpath is not None:
                written.append((tpath, c0, r0, tw, th))
            if progress_cb is not None:
                progress_cb(done, total, status)

    crs_wkt = rasterio.crs.CRS.from_string(_WGS84).to_wkt()
    vrt_path = out_dir / f"{tile_name_stem}.vrt"
    write_mosaic_vrt(vrt_path, width, height, transform, crs_wkt, written)

    return {
        "vrt_path": str(vrt_path),
        "tile_dir": str(out_dir),
        "width": width,
        "height": height,
        "gsd_deg": gsd_used,
        "n_sources": len(order),
        "n_tiles": total,
        "n_written": counts["write"],
        "n_skipped": counts["skip"],
        "n_empty": counts["empty"],
        "missing_fraction": float(uncovered_px) / float(width * height),
    }


__all__ = [
    "NEAR_BLACK_RGB_MEAN",
    "MAX_MOSAIC_SIDE_PX",
    "resolve_target_gsd_deg",
    "compute_grid",
    "build_mosaic",
    "build_mosaic_tiled",
    "write_mosaic_vrt",
]
