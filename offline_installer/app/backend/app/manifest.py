# -*- coding: utf-8 -*-
"""TOML manifest schema for a layered-priority geocell classification run.

One TOML file = one CDB geocell = one ``classify_v6`` run. The manifest
describes *where the input imagery comes from* (priority layers of source
orthos — GeoTIFF and/or JPEG 2000) and *where the classified output goes* —
nothing else. SAM3 / SDE /
water-mask configuration deliberately stays in ``shapefile_config.json``; a
stray ``[sde]`` or ``[water]`` table here is rejected (``extra="forbid"``).

Schema (see ``docs`` / ``cli.py --examples`` for the annotated version)::

    [geocell]
    south_lat = 45
    west_lon  = 6

    [[layers]]
    folder   = "D:/orthos/2024_campaign"
    priority = 1            # 1 = highest, wins on overlap
    glob     = "**/*.tif"   # optional; default matches *.tif/*.tiff/*.jp2.
                            #   May be a list, e.g. ["**/*.tif", "**/*.jp2"].
    name     = "2024"       # optional, logging only

    [[layers]]
    folder   = "D:/orthos/archive_2019"
    priority = 2

    # sources = ["D:/orthos/one_off.tif"]   # optional flat files, below all layers

    [output]
    path      = "D:/cdb_out/N45E006_material.tif"
    overwrite = false

Priority direction matches QGIS / Photoshop layer ordering: **lower number =
on top**. ``sources`` (flat files) always composite *below* every ``[[layers]]``
entry — they occupy the lowest priority band.

Each layer discovers **GeoTIFF and JPEG 2000** by default (``*.tif``, ``*.tiff``,
``*.jp2``); different layers may mix formats freely (e.g. a ``.jp2`` layer on top
of a ``.tif`` layer, both prioritized in the same manifest). Narrow a layer to
one format by setting its ``glob`` to a single pattern or an explicit list.

Pure stdlib ``tomllib`` + ``pydantic`` — no rasterio import, so it validates
without the geo stack.
"""

from __future__ import annotations

import glob as _glob
import tomllib
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .geocell import GeoCell

_GLOB_CHARS = frozenset("*?[")

# Default per-layer discovery patterns: GeoTIFF (both spellings) + JP2-boxed
# JPEG 2000. A layer picks up all three unless it overrides ``glob``. On the
# Windows target these match case-insensitively (``.TIF`` / ``.JP2`` too);
# raw ``.j2k`` / ``.jpc`` codestreams are intentionally excluded — they can't
# carry a CRS, so the catalog would reject them anyway.
DEFAULT_LAYER_GLOBS = ("**/*.tif", "**/*.tiff", "**/*.jp2")


def _expand_source_paths(raw_paths: list[Path]) -> list[Path]:
    """Resolve flat ``sources`` entries, expanding shell-style globs.

    Each entry is either a literal path (passed through unchanged; existence
    is checked at catalog-build time, not here, so a manifest on a removable
    drive still validates) or a glob pattern expanded via
    ``glob.glob(..., recursive=True)``. A pattern that matches no files is a
    hard error — silently dropping it would give a smaller mosaic with no
    explanation. Matches within a pattern are sorted for determinism.
    """
    expanded: list[Path] = []
    for entry in raw_paths:
        text = str(entry)
        if any(ch in text for ch in _GLOB_CHARS):
            matches = sorted(Path(p) for p in _glob.glob(text, recursive=True))
            if not matches:
                raise ValueError(
                    f"sources glob {text!r} matched no files. Double-check the "
                    f"path, or quote the pattern so your shell doesn't expand it "
                    f"before it reaches the manifest."
                )
            expanded.extend(matches)
        else:
            expanded.append(entry)
    return expanded


class GeoCellConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    south_lat: Annotated[int, Field(ge=-90, le=89)]
    west_lon: Annotated[int, Field(ge=-180, le=179)]

    def to_geocell(self) -> GeoCell:
        """Build the validated ``GeoCell`` (raises on a non-snapped ``west_lon``)."""
        return GeoCell(south_lat=self.south_lat, west_lon=self.west_lon)


class LayerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    folder: Path
    priority: Annotated[int, Field(ge=1)]
    # One pattern or a list of patterns. Default discovers TIFF + JPEG 2000 so a
    # layer folder of either format (or a mix) is picked up without configuration.
    glob: str | list[str] = Field(default_factory=lambda: list(DEFAULT_LAYER_GLOBS))
    name: str | None = None

    @field_validator("glob")
    @classmethod
    def _reject_empty_glob(cls, v: str | list[str]) -> str | list[str]:
        patterns = [v] if isinstance(v, str) else list(v)
        if not patterns:
            raise ValueError("glob must not be an empty list; give at least one pattern")
        for pat in patterns:
            if not isinstance(pat, str) or not pat.strip():
                raise ValueError(f"glob pattern must be a non-empty string, got {pat!r}")
        return v

    def glob_patterns(self) -> list[str]:
        """Normalize ``glob`` to a list of patterns (a lone string → one-item list)."""
        return [self.glob] if isinstance(self.glob, str) else list(self.glob)


class OutputConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    overwrite: bool = False


class ParallelismConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Reserved: the v1 mosaic build is single-threaded; classify_v6 does its own
    # RAM-aware tiling. Present so a manifest can carry the knob without
    # re-plumbing the schema when a parallel mosaic build lands.
    workers: int | str = "auto"


class GeocellManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    geocell: GeoCellConfig
    layers: Annotated[list[LayerConfig], Field(min_length=1)]
    sources: list[Path] | None = None
    output: OutputConfig
    parallelism: ParallelismConfig = Field(default_factory=ParallelismConfig)

    @model_validator(mode="after")
    def _reject_duplicate_priorities(self) -> Self:
        # Each layer is a distinct z-order position (1 = highest). Two layers
        # sharing a priority would silently merge into one path-tie-break group,
        # so fail fast (halt-on-first-error) — almost always an authoring typo.
        priorities = [layer.priority for layer in self.layers]
        if len(set(priorities)) != len(priorities):
            dups = sorted({p for p in priorities if priorities.count(p) > 1})
            raise ValueError(
                f"duplicate [[layers]] priority value(s) {dups}; give each layer a "
                f"distinct priority (1 = highest, wins on overlap)."
            )
        return self

    @model_validator(mode="after")
    def _expand_flat_sources(self) -> Self:
        if self.sources:
            # frozen model: replace the field via object.__setattr__ (same
            # pattern the IER manifest uses for its glob expansion).
            object.__setattr__(self, "sources", _expand_source_paths(list(self.sources)))
        return self


def _anchor(base: Path, value: str) -> str:
    """Make a relative manifest path absolute against ``base``.

    Relative ``folder`` / ``sources`` / ``output.path`` entries resolve against
    the manifest file's own directory (docker-compose style), not the process
    CWD — so ``cli.py --manifest D:/cfg/geocell.toml`` behaves the same from any
    working directory. Absolute paths pass through unchanged.
    """
    return value if Path(value).is_absolute() else str(base / value)


def _anchor_relative_paths(data: dict, base: Path) -> dict:
    """Rewrite the manifest's path fields to be absolute against ``base``."""
    for layer in data.get("layers") or []:
        if isinstance(layer, dict) and layer.get("folder") is not None:
            layer["folder"] = _anchor(base, str(layer["folder"]))
    if isinstance(data.get("sources"), list):
        data["sources"] = [_anchor(base, str(s)) for s in data["sources"]]
    out = data.get("output")
    if isinstance(out, dict) and out.get("path") is not None:
        out["path"] = _anchor(base, str(out["path"]))
    return data


def load_manifest(path: str | Path) -> GeocellManifest:
    """Parse and validate a geocell manifest TOML file.

    Relative paths in the manifest resolve against the manifest file's own
    directory (see :func:`_anchor`). Raises ``FileNotFoundError`` if the file is
    missing and ``pydantic.ValidationError`` / ``ValueError`` on any schema
    problem (unknown table, non-snapped ``west_lon``, duplicate layer priority,
    empty ``sources`` glob, ...).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"manifest not found: {p}")
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    data = _anchor_relative_paths(data, p.resolve().parent)
    return GeocellManifest.model_validate(data)


__all__ = [
    "DEFAULT_LAYER_GLOBS",
    "GeoCellConfig",
    "LayerConfig",
    "OutputConfig",
    "ParallelismConfig",
    "GeocellManifest",
    "load_manifest",
]
