# -*- coding: utf-8 -*-
"""Priority-aware source catalog for the geocell mosaic.

Given the manifest's priority layers (and optional flat ``sources``), walk the
folders, read each source's WGS-84 footprint from headers only (no pixel I/O),
drop the files that don't touch the target geocell, and expose the survivors in
priority-composite order.

Adapted from the IER project's ``cdb_build/io/source_catalog.py`` +
``footprint_reader.py``, deliberately trimmed for MaterialClassification's
scale (a geocell run touches a handful to a few hundred files, not the IER's
tens of thousands):

  * **rasterio-only footprint read** — no ``tifffile`` fast path. That path
    exists in the IER purely to beat GDAL's per-open overhead at ~15 000 files;
    it isn't worth a second code path here.
  * **no JSON sidecar cache, no thread pool, no pickling** — a straight
    sequential scan. Header reads for a few hundred files finish in well under
    a second.

Halt-on-first-error (per CLAUDE.md): a source with no CRS, an unreadable
header, or a layer whose glob matches zero files aborts the run with the
offending path attached — not a silent smaller mosaic.

JPEG 2000 (``.jp2``) sources are read through the same rasterio/GDAL path as
GeoTIFFs — footprint here, pixels in ``mosaic_builder`` — so no format-specific
code path exists. They require a GDAL JP2 driver (JP2OpenJPEG on the standard
build); a ``.jp2`` GDAL cannot open fails loudly here with its path attached.

No numpy import; needs only ``rasterio`` (for the header read + reprojection),
so it imports under the geo-only interpreter used for unit tests.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import rasterio
from rasterio.warp import transform_bounds

from .manifest import LayerConfig

_WGS84 = "EPSG:4326"

# Per-folder footprint cache (Tier 1): a JSON sidecar co-located with the source
# imagery, mapping each file's folder-relative path to its stat signature +
# footprint. A footprint is a pure function of the immutable ortho, so a re-run
# over an unchanged folder skips every GDAL open and does a bare stat-scan. Keyed
# on (size, mtime_ns) so editing/replacing a source auto-invalidates its row.
# Co-locating (IER-style) means the cache moves with the data and is shared
# across manifests/machines; if the folder is read-only the save is skipped
# silently (caching is an optimisation, never a hard dependency).
_CACHE_NAME = ".mc_footprint_cache.json"
_CACHE_VERSION = 1

# Pipeline OUTPUT directory suffixes. Their tiles are EPSG:4326 RGB re-composites
# / material-index rasters written next to the manifest output; if a layer folder
# is an ancestor of the output, a recursive glob would otherwise re-ingest them as
# "sources" on the next run. Discovery skips any file living under such a dir.
_OUTPUT_DIR_SUFFIXES = ("_mosaic_tiles", "_classified_tiles", "_with_vectors_tiles")


@dataclass(frozen=True)
class SourceEntry:
    """One source file with its WGS-84 footprint and priority.

    ``bounds_wgs84`` is ``(west, south, east, north)`` — the rasterio
    convention. ``priority`` is the layer's integer (1 = highest / wins).
    ``pixel_size_deg`` is the coarsest of the x/y axes in WGS-84 degrees per
    pixel; the mosaic's target grid uses the *finest* (smallest) such value
    across all surviving sources.
    """

    path: Path
    priority: int
    bounds_wgs84: tuple[float, float, float, float]
    pixel_size_deg: float


def read_footprint(path: Path) -> tuple[tuple[float, float, float, float], float]:
    """Return ``((west, south, east, north), pixel_size_deg)`` for one source.

    Reads GDAL metadata only (no pixel data). Source-data errors (no CRS,
    unreadable header) propagate wrapped in ``RuntimeError`` with the offending
    path attached so the catalog build halts loudly. ``Exception`` (not
    ``BaseException``) lets ``KeyboardInterrupt`` / ``SystemExit`` propagate.
    """
    try:
        with rasterio.open(path) as src:
            if src.crs is None:
                raise ValueError(
                    f"source has no CRS; assign one with gdal_edit before ingest: {path}"
                )
            west, south, east, north = transform_bounds(
                src.crs, _WGS84, *src.bounds, densify_pts=21
            )
            width = max(int(src.width), 1)
            height = max(int(src.height), 1)
    except Exception as exc:
        raise RuntimeError(f"failed to read footprint for {path}: {exc}") from exc

    bounds = (float(west), float(south), float(east), float(north))
    # Conservative (max, not min) so we never overstate a source's resolution.
    pixel_size_deg = max((bounds[2] - bounds[0]) / width, (bounds[3] - bounds[1]) / height)
    return bounds, pixel_size_deg


def _bbox_intersects(
    a: tuple[float, float, float, float],
    b_west: float,
    b_south: float,
    b_east: float,
    b_north: float,
) -> bool:
    """AABB overlap on ``(west, south, east, north)`` tuples.

    Touch-only (shared edge, zero overlap area) counts as non-intersecting: the
    resampler would read no pixels from a source that only shares a boundary.
    """
    a_west, a_south, a_east, a_north = a
    return (
        a_west < b_east and a_east > b_west and a_south < b_north and a_north > b_south
    )


def _discover(layer: LayerConfig) -> list[Path]:
    """Files under ``layer.folder`` matching any of the layer's glob pattern(s).

    A layer's default glob covers TIFF + JPEG 2000, so one call can return a mix
    of ``.tif`` / ``.tiff`` / ``.jp2``. Matches from all patterns are unioned
    (a file that matches two patterns appears once) and sorted for a
    deterministic, path-tie-break composite order.
    """
    patterns = layer.glob_patterns()
    # Path.glob (unlike the glob module) matches dotfiles, so exclude our own
    # footprint-cache sidecar: a layer configured with glob="*" / "**/*" would
    # otherwise pick it up on a later run and feed it to read_footprint (crash).
    # Also skip anything under a pipeline output dir (see _OUTPUT_DIR_SUFFIXES) so
    # a run's own mosaic/classified tiles are never re-ingested as sources.
    _skip = {_CACHE_NAME, _CACHE_NAME + ".tmp"}

    def _keep(p: Path) -> bool:
        if p.name in _skip:
            return False
        return not any(part.endswith(_OUTPUT_DIR_SUFFIXES) for part in p.parts)

    matches = sorted({
        p for pat in patterns for p in layer.folder.glob(pat) if _keep(p)
    })
    if not matches:
        shown = patterns[0] if len(patterns) == 1 else patterns
        raise ValueError(
            f"layer priority={layer.priority} folder={layer.folder} "
            f"glob={shown!r} matched no files"
        )
    return matches


def _load_cache(folder: Path) -> dict:
    """Read a folder's footprint cache sidecar → ``{relpath: {...}}`` (``{}`` on miss)."""
    try:
        data = json.loads((folder / _CACHE_NAME).read_text(encoding="utf-8"))
        if data.get("version") == _CACHE_VERSION and isinstance(data.get("entries"), dict):
            return data["entries"]
    except Exception:
        pass  # missing / corrupt / unreadable cache → treat as empty
    return {}


def _save_cache(folder: Path, entries: dict) -> None:
    """Atomically write the folder's cache. A read-only folder just disables caching."""
    dst = folder / _CACHE_NAME
    tmp = dst.with_name(_CACHE_NAME + ".tmp")
    try:
        tmp.write_text(
            json.dumps({"version": _CACHE_VERSION, "entries": entries}),
            encoding="utf-8",
        )
        os.replace(tmp, dst)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _read_footprints(
    folder: Path,
    paths: list[Path],
    use_cache: bool,
    max_workers: int | None,
) -> tuple[dict[Path, tuple[tuple[float, float, float, float], float]], int, int]:
    """Footprints for ``paths`` → ``({path: (bounds, px)}, n_cache_hits, n_read)``.

    Cache-hit files (matching ``size`` + ``mtime_ns``) skip the GDAL open; the
    misses are opened in parallel on a thread pool (footprint reads are
    metadata-only and I/O-bound; ``rasterio.open`` releases the GIL). Halts on
    the first unreadable/no-CRS source (``read_footprint`` raises), same contract
    as before. Updated rows are written back to the sidecar once at the end.
    """
    cache = _load_cache(folder) if use_cache else {}
    new_cache = dict(cache)
    results: dict[Path, tuple[tuple[float, float, float, float], float]] = {}
    misses: list[tuple[Path, str]] = []  # (path, relkey)

    for p in paths:
        try:
            # as_posix so a cache written on Windows is a hit on POSIX and vice
            # versa (forward-slash keys are portable; str() would embed '\\').
            relkey = p.relative_to(folder).as_posix()
        except ValueError:
            relkey = p.name
        row = cache.get(relkey)
        if use_cache and row is not None:
            try:
                st = p.stat()
                b = row["bounds"]
                if (row["size"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns
                        and isinstance(b, list) and len(b) == 4):
                    results[p] = (tuple(b), row["px"])  # type: ignore[assignment]
                    continue
            except (OSError, KeyError, TypeError):
                pass  # stat failure / malformed row → fall through to a real read
        misses.append((p, relkey))

    if misses:
        workers = max_workers or min(32, (os.cpu_count() or 4) * 4, len(misses))

        def _work(item: tuple[Path, str]):
            p, relkey = item
            bounds, px = read_footprint(p)
            return p, relkey, bounds, px

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for p, relkey, bounds, px in ex.map(_work, misses):
                results[p] = (bounds, px)
                if use_cache:
                    try:
                        st = p.stat()
                        new_cache[relkey] = {
                            "size": st.st_size,
                            "mtime_ns": st.st_mtime_ns,
                            "bounds": list(bounds),
                            "px": px,
                        }
                    except OSError:
                        pass

    if use_cache and new_cache != cache:
        _save_cache(folder, new_cache)

    return results, len(paths) - len(misses), len(misses)


def build_catalog(
    layers: list[LayerConfig],
    flat_sources: Iterable[Path] | None,
    geocell_bounds: tuple[float, float, float, float],
    use_cache: bool = True,
    max_workers: int | None = None,
) -> list[SourceEntry]:
    """Discover every source, read footprints, keep only those touching the cell.

    ``geocell_bounds`` is ``(west, south, east, north)`` in WGS-84. Flat
    ``sources`` are assigned a priority one greater than the largest layer
    priority, so they composite *below* every ``[[layers]]`` entry.

    Footprints are read in parallel and cached per-folder (Tier 1): a re-run over
    an unchanged folder skips every GDAL open (bare stat-scan). Pass
    ``use_cache=False`` to force fresh reads, or ``max_workers`` to cap the pool.

    Returns the surviving ``SourceEntry`` list (unordered — call
    :func:`composite_order` for the last-wins compositing sequence). Raises if a
    layer glob matches nothing or any footprint read fails.
    """
    if not layers:
        raise ValueError("build_catalog: at least one layer is required")

    entries: list[SourceEntry] = []
    for layer in layers:
        matches = _discover(layer)
        fps, n_hit, n_read = _read_footprints(layer.folder, matches, use_cache, max_workers)
        print(f"[catalog] layer priority={layer.priority} folder={layer.folder} "
              f"matched {len(matches)} file(s) (cache: {n_hit} hit, {n_read} read)")
        for p in matches:
            bounds, px = fps[p]
            entries.append(SourceEntry(p.resolve(), layer.priority, bounds, px))

    flat_list = [Path(p) for p in flat_sources] if flat_sources else []
    if flat_list:
        flat_priority = max(layer.priority for layer in layers) + 1
        for p in flat_list:
            if not p.exists():
                raise ValueError(f"sources entry does not exist: {p}")
        # Group flat sources by parent folder so each folder's cache is reused.
        by_folder: dict[Path, list[Path]] = {}
        for p in flat_list:
            by_folder.setdefault(p.parent, []).append(p)
        flat_fps: dict[Path, tuple[tuple[float, float, float, float], float]] = {}
        for folder, fpaths in by_folder.items():
            sub, _h, _r = _read_footprints(folder, fpaths, use_cache, max_workers)
            flat_fps.update(sub)
        for p in flat_list:
            bounds, px = flat_fps[p]
            entries.append(SourceEntry(p.resolve(), flat_priority, bounds, px))
        print(f"[catalog] {len(flat_list)} flat source(s) at priority={flat_priority}")

    gw, gs, ge, gn = geocell_bounds
    kept = [e for e in entries if _bbox_intersects(e.bounds_wgs84, gw, gs, ge, gn)]
    dropped = len(entries) - len(kept)
    if dropped:
        print(f"[catalog] pre-filtered {dropped} source(s) entirely outside the geocell "
              f"(W={gw:.4f} S={gs:.4f} E={ge:.4f} N={gn:.4f}); {len(kept)} retained")
    return kept


def composite_order(entries: list[SourceEntry]) -> list[SourceEntry]:
    """Return entries ordered so the highest-priority (priority=1) source is LAST.

    The mosaic builder iterates in this order and applies ``result[valid] =
    layer[valid]`` per source (last-wins), so priority=1 becomes the final
    writer per pixel. Ties inside a priority group break lexicographically by
    path — deterministic across runs and machines.
    """
    return sorted(entries, key=lambda e: (-e.priority, str(e.path)))


def summary_by_priority(
    entries: list[SourceEntry],
) -> list[tuple[int, int, tuple[float, float, float, float]]]:
    """One ``(priority, file_count, union_bbox)`` row per priority group.

    For a human-readable log of what the catalog discovered for the geocell.
    """
    by_priority: dict[int, list[SourceEntry]] = {}
    for e in entries:
        by_priority.setdefault(e.priority, []).append(e)
    rows = []
    for priority in sorted(by_priority):
        group = by_priority[priority]
        w = min(e.bounds_wgs84[0] for e in group)
        s = min(e.bounds_wgs84[1] for e in group)
        e_ = max(e.bounds_wgs84[2] for e in group)
        n = max(e.bounds_wgs84[3] for e in group)
        rows.append((priority, len(group), (w, s, e_, n)))
    return rows


__all__ = [
    "SourceEntry",
    "read_footprint",
    "build_catalog",
    "composite_order",
    "summary_by_priority",
]
