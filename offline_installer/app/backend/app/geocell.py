# -*- coding: utf-8 -*-
"""CDB geocell geometry (the subset MaterialClassification needs).

A *geocell* is the OGC CDB 1.1 addressing unit: a 1°-tall geographic cell
whose longitudinal width grows toward the poles so cell area stays roughly
constant on the curved Earth. It is anchored at ``(south_lat, west_lon)`` and
named like ``N45E006``.

This module is a trimmed port of the IER project's ``cdb_build/geocell.py`` —
only the pieces a single-raster material classifier needs: the latitude-zone
width table, the ``GeoCell`` value object (name + WGS-84 bounds + snap
validation). The IER's LOD / tile / UREF-RREF pyramid math is intentionally
absent: a manifest run produces ONE classified raster for the whole cell, not
a CDB tile pyramid.

Pure math — no rasterio / numpy import — so it stays importable anywhere and
unit-tests without the geo stack.
"""

from __future__ import annotations

from dataclasses import dataclass


def cdb_cell_lon_width_deg(lat_deg: float) -> float:
    """Return the CDB geocell longitudinal width (degrees) for a latitude.

    The latitude-zone table from OGC CDB 15-113 §7 / Volume 1 Clause 7. The
    latitude extent of a geocell is always 1°; only the longitude width varies.
    """
    a = abs(lat_deg)
    if a >= 80.0:
        return 12.0
    if a >= 75.0:
        return 6.0
    if a >= 70.0:
        return 4.0
    if a >= 50.0:
        return 2.0
    return 1.0


def _lat_label(south_lat: int) -> str:
    return f"{'N' if south_lat >= 0 else 'S'}{abs(south_lat):02d}"


def _lon_label(west_lon: int) -> str:
    return f"{'E' if west_lon >= 0 else 'W'}{abs(west_lon):03d}"


@dataclass(frozen=True)
class GeoCell:
    """A single CDB geocell anchored at ``(south_lat, west_lon)``.

    ``south_lat`` and ``west_lon`` are integer degrees. ``west_lon`` MUST be a
    multiple of ``cdb_cell_lon_width_deg`` for the cell's latitude zone —
    enforced in ``__post_init__`` (this IS the "snapped to the lat-zone width"
    validation). A misaligned cell (e.g. ``west_lon=7`` in a 2°-wide zone) is a
    hard error, mirroring the IER manifest's behaviour so the output drops into
    an IER build at the right grid position.
    """

    south_lat: int
    west_lon: int

    def __post_init__(self) -> None:
        if not -90 <= self.south_lat <= 89:
            raise ValueError(f"south_lat out of range [-90, 89]: {self.south_lat}")
        if not -180 <= self.west_lon <= 179:
            raise ValueError(f"west_lon out of range [-180, 179]: {self.west_lon}")
        # Validate against the latitude-zone grid using the cell mid-latitude.
        width = cdb_cell_lon_width_deg(self.south_lat + 0.5)
        if self.west_lon % width != 0:
            raise ValueError(
                f"west_lon={self.west_lon} is not a multiple of the zone width "
                f"{width:g}° for the latitude band containing {self.south_lat}. "
                f"Valid west_lon values in this zone step by {width:g}°."
            )

    @property
    def lon_width_deg(self) -> float:
        return cdb_cell_lon_width_deg(self.south_lat + 0.5)

    @property
    def lat_extent_deg(self) -> float:
        return 1.0

    @property
    def name(self) -> str:
        """CDB-grammar cell name, e.g. ``'N45E006'``."""
        return _lat_label(self.south_lat) + _lon_label(self.west_lon)

    def bounds_wgs84(self) -> tuple[float, float, float, float]:
        """Return ``(west, south, east, north)`` in EPSG:4326 degrees."""
        west = float(self.west_lon)
        south = float(self.south_lat)
        east = west + self.lon_width_deg
        north = south + self.lat_extent_deg
        return (west, south, east, north)


__all__ = ["cdb_cell_lon_width_deg", "GeoCell"]
