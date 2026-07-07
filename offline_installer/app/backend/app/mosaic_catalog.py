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

No numpy import; needs only ``rasterio`` (for the header read + reprojection),
so it imports under the geo-only interpreter used for unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import rasterio
from rasterio.warp import transform_bounds

from .manifest import LayerConfig

_WGS84 = "EPSG:4326"


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
    matches = sorted(layer.folder.glob(layer.glob))
    if not matches:
        raise ValueError(
            f"layer priority={layer.priority} folder={layer.folder} "
            f"glob={layer.glob!r} matched no files"
        )
    return matches


def build_catalog(
    layers: list[LayerConfig],
    flat_sources: Iterable[Path] | None,
    geocell_bounds: tuple[float, float, float, float],
) -> list[SourceEntry]:
    """Discover every source, read footprints, keep only those touching the cell.

    ``geocell_bounds`` is ``(west, south, east, north)`` in WGS-84. Flat
    ``sources`` are assigned a priority one greater than the largest layer
    priority, so they composite *below* every ``[[layers]]`` entry.

    Returns the surviving ``SourceEntry`` list (unordered — call
    :func:`composite_order` for the last-wins compositing sequence). Raises if a
    layer glob matches nothing or any footprint read fails.
    """
    if not layers:
        raise ValueError("build_catalog: at least one layer is required")

    entries: list[SourceEntry] = []
    for layer in layers:
        matches = _discover(layer)
        print(f"[catalog] layer priority={layer.priority} folder={layer.folder} "
              f"matched {len(matches)} file(s)")
        for p in matches:
            bounds, px = read_footprint(p)
            entries.append(SourceEntry(p.resolve(), layer.priority, bounds, px))

    flat_list = [Path(p) for p in flat_sources] if flat_sources else []
    if flat_list:
        flat_priority = max(layer.priority for layer in layers) + 1
        for p in flat_list:
            if not p.exists():
                raise ValueError(f"sources entry does not exist: {p}")
            bounds, px = read_footprint(p)
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
