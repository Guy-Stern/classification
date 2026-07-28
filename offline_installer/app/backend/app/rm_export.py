"""CDB Raster Material (RM) export — CMIX index raster + cell-wide CMT XML.

The MEA pipeline's deliverable is a folder of RGB-painted classification tiles.
The downstream OGC CDB builder (``cdb-build``, the IER repo) instead wants the
pair its ``[raster_material]`` manifest section describes:

* ``source_tiff``    — one **single-band** raster of CMIX indices covering the
  whole geocell.
* ``source_cmt_xml`` — **one** Composite Material Table for that raster, with a
  stable index space shared by every pixel of every tile.

This module produces that pair from an already-classified output.

Why an RGB post-pass rather than plumbing label arrays through the pipeline:
the painted colour *is* an exact function of the label. ``core._apply_color_table``
builds a LUT whose entry 0 is black and whose entry *N* is class *N*'s exact
colour, mask fusion in ``pipeline._fuse_with_priors_and_veto`` hard-assigns those
same palette colours, and every reprojection on the path uses nearest-neighbour.
So the colour→index map here is the exact inverse of the paint step, and reading
it back costs one extra pass over finished tiles instead of a pipeline-wide
refactor. ``rgb_to_cmix`` fails loudly (see ``strict``) if that ever stops
holding, so the invariant cannot rot silently.

Index space (fixed, positional over the class list — never renumber):

===== =========================================================
CMIX  Meaning
===== =========================================================
0     ``DEFAULT`` — uncovered / near-black nodata. Composed of
      100 % ``BM_SOIL`` so a gap renders as bare ground.
      cdb-build fills out-of-source pixels with CMIX 0 and
      *requires* the index to exist in the CMT.
1..N  ``classes[i - 1]`` — the MEA class at 1-based position i.
===== =========================================================
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from rasterio.errors import WindowError
from rasterio.windows import Window
from rasterio.windows import from_bounds as window_from_bounds
from rasterio.windows import transform as window_transform

log = logging.getLogger(__name__)

# CMIX reserved for "no classification here" (mosaic gap / near-black nodata).
DEFAULT_CMIX = 0
DEFAULT_COMPOSITE_NAME = "DEFAULT"
DEFAULT_BASE_MATERIAL = "BM_SOIL"
# Black: what those pixels actually look like in the RGB tiles. The <Color>
# element is an MC extension the OGC CMT schema does not define and cdb-build's
# reader ignores it, so this is purely for human/GIS inspection.
DEFAULT_COLOR_ARGB = "#ff000000"

# Written to the CMT for every substrate, matching core._write_composite_material_xml.
_SUBSTRATE_THICKNESS = "1"

_RM_PROFILE_BASE = {
    "driver": "GTiff",
    "count": 1,
    "dtype": "uint8",
    "tiled": True,
    "blockxsize": 512,
    "blockysize": 512,
    "compress": "deflate",
    "zlevel": 1,
    "predictor": 2,
}


class PaletteError(ValueError):
    """A classified raster held a colour outside the class palette."""


# --------------------------------------------------------------------------- #
# Palette                                                                      #
# --------------------------------------------------------------------------- #


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    h = str(hex_color).strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected a #RRGGBB colour, got {hex_color!r}")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def build_palette(classes: Sequence[Dict[str, str]]) -> Dict[Tuple[int, int, int], int]:
    """Map each class colour to its 1-based CMIX index.

    Black is deliberately *not* in the returned map: it is handled as
    ``DEFAULT_CMIX`` by ``rgb_to_cmix``. A class whose colour is literally black
    would therefore be unreachable, so that is rejected here rather than
    silently swallowing pixels.
    """
    palette: Dict[Tuple[int, int, int], int] = {}
    for idx, cls in enumerate(classes, start=1):
        rgb = _hex_to_rgb(cls.get("color", ""))
        if rgb == (0, 0, 0):
            raise ValueError(
                f"class {cls.get('name')!r} is coloured pure black, which is "
                f"reserved for CMIX {DEFAULT_CMIX} (uncovered)"
            )
        if rgb in palette:
            prev = classes[palette[rgb] - 1].get("name")
            raise ValueError(
                f"classes {prev!r} and {cls.get('name')!r} share colour "
                f"{cls.get('color')!r}; the colour->index map must be injective"
            )
        palette[rgb] = idx
    return palette


def rgb_to_cmix(
    rgb: np.ndarray,
    palette: Dict[Tuple[int, int, int], int],
    strict: bool = False,
    context: str = "",
) -> np.ndarray:
    """Invert the paint step: an ``(3, H, W)`` RGB array to ``(H, W)`` uint8 CMIX.

    Pure black maps to ``DEFAULT_CMIX``. Any other colour absent from *palette*
    is a broken invariant: it means something on the pipeline blended or
    interpolated a classified raster. Those pixels fall back to ``DEFAULT_CMIX``
    with a ``WARNING`` naming the offending colours, or raise ``PaletteError``
    when *strict*.
    """
    if rgb.ndim != 3 or rgb.shape[0] < 3:
        raise ValueError(f"expected a (3, H, W) RGB array, got shape {rgb.shape}")

    r, g, b = (rgb[i].astype(np.uint32) for i in range(3))
    packed = (r << 16) | (g << 8) | b

    out = np.zeros(packed.shape, dtype=np.uint8)
    matched = packed == 0  # pure black -> DEFAULT_CMIX, already 0 in `out`
    for (pr, pg, pb), idx in palette.items():
        key = (pr << 16) | (pg << 8) | pb
        hit = packed == key
        out[hit] = idx
        matched |= hit

    if not matched.all():
        unknown = packed[~matched]
        keys, counts = np.unique(unknown, return_counts=True)
        detail = ", ".join(
            f"#{int(k):06x} ({int(n)} px)"
            for k, n in zip(keys[:8], counts[:8])
        )
        if len(keys) > 8:
            detail += f", +{len(keys) - 8} more"
        where = f" in {context}" if context else ""
        msg = (
            f"{int((~matched).sum())} pixel(s){where} carry colours outside the "
            f"class palette: {detail}. The classified raster must only ever hold "
            f"exact palette colours or black — a non-palette colour means it was "
            f"resampled with interpolation or blended somewhere upstream."
        )
        if strict:
            raise PaletteError(msg)
        log.warning("%s Falling back to CMIX %d for those pixels.", msg, DEFAULT_CMIX)

    return out


# --------------------------------------------------------------------------- #
# CMT XML                                                                      #
# --------------------------------------------------------------------------- #


def _composite_material_block(
    index: int, name: str, argb: str, base_material: str
) -> List[str]:
    return [
        f'  <Composite_Material index="{index}">',
        f"    <Name>{name}</Name>",
        f"    <Color>{argb}</Color>",
        "    <Primary_Substrate>",
        f"      <Thickness>{_SUBSTRATE_THICKNESS}</Thickness>",
        "      <Material>",
        f"        <Name>{base_material}</Name>",
        "        <Weight>100</Weight>",
        "      </Material>",
        "    </Primary_Substrate>",
        "  </Composite_Material>",
    ]


def write_cell_cmt_xml(xml_path, classes: Sequence[Dict[str, str]]) -> str:
    """Write the cell-wide Composite Material Table covering CMIX 0..N.

    Same grammar as ``core._write_composite_material_xml`` (2-space indent, no
    XML declaration, ARGB ``<Color>``), with the ``DEFAULT`` entry at index 0
    prepended so cdb-build's out-of-source fill has a defined material.
    """
    xml_path = Path(xml_path)
    lines: List[str] = ["<Composite_Material_Table>"]
    lines += _composite_material_block(
        DEFAULT_CMIX, DEFAULT_COMPOSITE_NAME, DEFAULT_COLOR_ARGB, DEFAULT_BASE_MATERIAL
    )

    for idx, cls in enumerate(classes, start=1):
        bm_name = cls.get("name", "")
        composite_name = cls.get("composite_name") or bm_name.replace("BM_", "")
        color_hex = cls.get("color", "#000000")
        if color_hex.startswith("#") and len(color_hex) == 7:
            argb = f"#ff{color_hex[1:].lower()}"
        else:
            argb = "#ff000000"
        lines += _composite_material_block(idx, composite_name, argb, bm_name)

    lines.append("</Composite_Material_Table>")

    xml_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = xml_path.with_suffix(xml_path.suffix + ".tmp")
    try:
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(xml_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return str(xml_path)


# --------------------------------------------------------------------------- #
# Raster assembly                                                              #
# --------------------------------------------------------------------------- #


def _classified_tifs(tiles_dir: Path) -> List[Path]:
    """Every classified tile in *tiles_dir*, sorted, skipping dot-prefixed temps."""
    return sorted(
        p for p in tiles_dir.glob("*.tif") if not p.name.startswith(".")
    )


def _target_grid(tile_paths: Sequence[Path]):
    """Union bounds + finest resolution across *tile_paths*.

    Returns ``(transform, width, height, crs)``. Resolution is taken as the
    finest seen on each axis: in manifest mode every tile is a window of one
    mosaic grid so they agree exactly, but a non-4326 source classified in
    normal mode reprojects each tile independently and they can differ slightly.
    Placement below reprojects rather than block-copies, so a finest-wins grid
    is safe either way.
    """
    west = south = float("inf")
    east = north = float("-inf")
    xres = yres = float("inf")
    crs = None

    for path in tile_paths:
        with rasterio.open(path) as src:
            if crs is None:
                crs = src.crs
            elif src.crs != crs:
                raise ValueError(
                    f"tiles disagree on CRS: {crs} vs {src.crs} ({path.name}). "
                    f"Every classified tile should have been reprojected to EPSG:4326."
                )
            b = src.bounds
            west, south = min(west, b.left), min(south, b.bottom)
            east, north = max(east, b.right), max(north, b.top)
            xres = min(xres, abs(src.transform.a))
            yres = min(yres, abs(src.transform.e))

    if not (xres > 0 and yres > 0) or east <= west or north <= south:
        raise ValueError(
            f"degenerate RM grid from {len(tile_paths)} tile(s): "
            f"bounds=({west}, {south}, {east}, {north}) res=({xres}, {yres})"
        )

    width = max(1, int(round((east - west) / xres)))
    height = max(1, int(round((north - south) / yres)))
    return from_origin(west, north, xres, yres), width, height, crs


def assemble_rm_raster(
    tile_paths: Sequence[Path],
    out_path: Path,
    palette: Dict[Tuple[int, int, int], int],
    strict: bool = False,
) -> Dict[str, object]:
    """Write the single-band CMIX raster covering every tile in *tile_paths*.

    One tile is held in memory at a time — the output is written windowed, so a
    full geocell never materialises as one array.

    No ``nodata`` tag is set: CMIX 0 is a *defined* material (``DEFAULT``), not
    absent data, and tagging it nodata would make cdb-build treat real pixels as
    holes.
    """
    tile_paths = list(tile_paths)
    if not tile_paths:
        raise ValueError("no classified tiles to assemble an RM raster from")

    transform, width, height, crs = _target_grid(tile_paths)
    profile = dict(_RM_PROFILE_BASE)
    profile.update(width=width, height=height, transform=transform, crs=crs)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    full = Window(0, 0, width, height)
    histogram: Dict[int, int] = {}

    try:
        # 'w+' so overlapping tiles can be merged against what is already down.
        with rasterio.open(tmp_path, "w+", **profile) as dst:
            for path in tile_paths:
                with rasterio.open(path) as src:
                    rgb = src.read()
                    src_transform, src_crs, sb = src.transform, src.crs, src.bounds

                cmix = rgb_to_cmix(rgb, palette, strict=strict, context=path.name)
                del rgb

                win = window_from_bounds(
                    sb.left, sb.bottom, sb.right, sb.top, transform
                ).round_offsets().round_lengths()
                try:
                    win = win.intersection(full)
                except WindowError:
                    # Unreachable while the grid is the union of these very
                    # tiles, but a rounding slip must not cost the whole export.
                    log.warning("tile %s falls outside the RM grid; skipped", path.name)
                    continue
                if win.width <= 0 or win.height <= 0:
                    log.warning("tile %s is empty on the RM grid; skipped", path.name)
                    continue

                placed = np.zeros((int(win.height), int(win.width)), dtype=np.uint8)
                reproject(
                    source=cmix,
                    destination=placed,
                    src_transform=src_transform,
                    src_crs=src_crs,
                    dst_transform=window_transform(win, transform),
                    dst_crs=crs,
                    resampling=Resampling.nearest,  # categorical — NEVER interpolate
                )

                # Keep whatever a previous tile already classified here: a tile's
                # own zero-fill (rounding slack at the window edge, or its
                # near-black nodata) must not erase a neighbour's real material.
                existing = dst.read(1, window=win)
                placed = np.where(placed != 0, placed, existing)
                dst.write(placed, 1, window=win)

            # Histogram from the finished raster, not from the per-tile writes:
            # overlapping tiles would otherwise count the same pixel twice.
            # Read block-wise so a full geocell never lands in memory at once.
            for _, block in dst.block_windows(1):
                vals, counts = np.unique(dst.read(1, window=block), return_counts=True)
                for v, n in zip(vals, counts):
                    histogram[int(v)] = histogram.get(int(v), 0) + int(n)

        tmp_path.replace(out_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return {
        "path": str(out_path),
        "width": width,
        "height": height,
        "n_tiles": len(tile_paths),
        "histogram": histogram,
    }


# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #


def export_rm_pair(
    source: Path,
    out_stem: Path,
    classes: Sequence[Dict[str, str]],
    strict: bool = False,
) -> Optional[Dict[str, object]]:
    """Emit ``<out_stem>_rm.tif`` + ``<out_stem>_rm.xml`` from a classified output.

    *source* is either a folder of classified tiles or a single classified
    ``.tif``; *out_stem* is a path whose parent and stem name the pair (the
    manifest's ``[output].path`` works directly). *classes* is the MEA class
    list — its order defines the CMIX index space.

    Returns the ``assemble_rm_raster`` summary plus ``cmt`` (the XML path), or
    ``None`` when *source* holds no classified tile. Never raises for an empty
    or unreadable source: the RM pair is an additional deliverable and must not
    turn a successful classification into a failed run.
    """
    source = Path(source)
    out_stem = Path(out_stem)

    if source.is_dir():
        tiles = _classified_tifs(source)
    elif source.is_file():
        tiles = [source]
    else:
        log.warning("RM export: no such classified output: %s", source)
        return None

    if not tiles:
        log.warning("RM export: no classified tiles found under %s", source)
        return None

    palette = build_palette(classes)
    rm_tif = out_stem.with_name(out_stem.stem + "_rm.tif")
    rm_xml = out_stem.with_name(out_stem.stem + "_rm.xml")

    info = assemble_rm_raster(tiles, rm_tif, palette, strict=strict)
    info["cmt"] = write_cell_cmt_xml(rm_xml, classes)
    return info


def summarize(info: Dict[str, object], classes: Sequence[Dict[str, str]]) -> str:
    """One-line-per-material breakdown for CLI output."""
    names = {DEFAULT_CMIX: DEFAULT_COMPOSITE_NAME}
    for idx, cls in enumerate(classes, start=1):
        names[idx] = cls.get("name", f"class-{idx}")
    hist: Dict[int, int] = info.get("histogram", {})  # type: ignore[assignment]
    total = sum(hist.values()) or 1
    lines = [
        f"  RM raster: {info['path']} "
        f"({info['width']}x{info['height']} px from {info['n_tiles']} tile(s))",
        f"  RM CMT:    {info.get('cmt')}",
    ]
    for cmix in sorted(hist):
        n = hist[cmix]
        lines.append(
            f"    CMIX {cmix} {names.get(cmix, '?'):<16} {n:>12,} px  "
            f"({n / total * 100:5.1f}%)"
        )
    return "\n".join(lines)
