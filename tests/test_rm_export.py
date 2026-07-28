"""Unit tests for backend.app.rm_export (CMIX raster + cell-wide CMT).

The CMT assertions deliberately use **cdb-build's own parser** as the oracle
rather than re-implementing the schema here: if ``cmt_reader.parse_cmt``
accepts our XML, the downstream RM build will too. Those tests skip when
cdb-build is not installed (see ``ier_cmt``).

Run:  .venv/Scripts/python.exe -m pytest tests/test_rm_export.py -v
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import rm_export as rx  # noqa: E402

# A stand-in for MEA_CLASSES that needs no torch import. The real palette is
# exercised by test_rm_export_real_palette.py.
CLASSES = [
    {"name": "BM_ASPHALT", "color": "#2D2D30", "composite_name": "ASPHALT"},
    {"name": "BM_CONCRETE", "color": "#B4B4B4", "composite_name": "CONCRETE"},
    {"name": "BM_VEGETATION", "color": "#228B22", "composite_name": "GENVEGETATION"},
    {"name": "BM_WATER", "color": "#1C6BA0", "composite_name": "WATER"},
    {"name": "BM_SAND", "color": "#EDC9AF", "composite_name": "SAND"},
    {"name": "BM_SOIL", "color": "#654321", "composite_name": "SOIL"},
]

# Candidate install roots for cdb-build, in priority order.
_IER_ROOTS = [
    Path("C:/cdb-build/runtime/venv/Lib/site-packages"),
    Path("C:/work/IER/src"),
]


@pytest.fixture(scope="session")
def ier_cmt():
    """cdb-build's ``cmt_reader`` module, or skip if it is not installed."""
    for root in _IER_ROOTS:
        mod_path = root / "cdb_build" / "cmt_reader.py"
        if not mod_path.is_file():
            continue
        name = "_ier_cmt_reader"
        spec = importlib.util.spec_from_file_location(name, mod_path)
        module = importlib.util.module_from_spec(spec)
        # Must be registered BEFORE exec_module: @dataclass resolves annotations
        # via sys.modules[cls.__module__], which is None for an unregistered
        # module and blows up inside dataclasses._is_type.
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # lxml missing, incompatible interpreter, ...
            sys.modules.pop(name, None)
            pytest.skip(f"cdb-build cmt_reader at {mod_path} not importable: {exc}")
        return module
    pytest.skip("cdb-build not found; RM CMT conformance tests skipped")


def write_rgb(path: Path, rgb: np.ndarray, west=35.0, north=34.0, res=0.001, crs="EPSG:4326"):
    """Write an (3, H, W) uint8 tile, mirroring the classifier's tile profile."""
    _, h, w = rgb.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=3, dtype="uint8",
        crs=crs, transform=from_origin(west, north, res, res), nodata=0,
    ) as dst:
        dst.write(rgb)
    return path


def solid(color, h=4, w=4):
    arr = np.zeros((3, h, w), dtype=np.uint8)
    for i, v in enumerate(color):
        arr[i, :, :] = v
    return arr


# --------------------------------------------------------------------------- #
# Palette                                                                      #
# --------------------------------------------------------------------------- #


def test_build_palette_is_positional():
    pal = rx.build_palette(CLASSES)
    assert pal[(45, 45, 48)] == 1        # BM_ASPHALT
    assert pal[(180, 180, 180)] == 2     # BM_CONCRETE
    assert pal[(34, 139, 34)] == 3       # BM_VEGETATION
    assert pal[(28, 107, 160)] == 4      # BM_WATER
    assert pal[(237, 201, 175)] == 5     # BM_SAND
    assert pal[(101, 67, 33)] == 6       # BM_SOIL
    assert len(pal) == 6
    assert (0, 0, 0) not in pal          # black is reserved for CMIX 0


def test_build_palette_rejects_black_class():
    with pytest.raises(ValueError, match="black"):
        rx.build_palette([{"name": "BM_X", "color": "#000000"}])


def test_build_palette_rejects_duplicate_colors():
    dup = [
        {"name": "BM_A", "color": "#112233"},
        {"name": "BM_B", "color": "#112233"},
    ]
    with pytest.raises(ValueError, match="injective"):
        rx.build_palette(dup)


def test_rgb_to_cmix_roundtrips_every_class_and_black():
    """The exact inverse of core._apply_color_table, for all 6 classes + black."""
    pal = rx.build_palette(CLASSES)
    colors = [rx._hex_to_rgb(c["color"]) for c in CLASSES]
    # One column per class, plus a black column.
    rgb = np.zeros((3, 2, len(colors) + 1), dtype=np.uint8)
    for col, c in enumerate(colors):
        for ch in range(3):
            rgb[ch, :, col] = c[ch]

    cmix = rx.rgb_to_cmix(rgb, pal, strict=True)
    assert cmix.dtype == np.uint8
    assert list(cmix[0]) == [1, 2, 3, 4, 5, 6, 0]
    assert list(cmix[1]) == [1, 2, 3, 4, 5, 6, 0]


def test_rgb_to_cmix_unknown_color_warns_and_defaults(caplog):
    pal = rx.build_palette(CLASSES)
    rgb = solid((7, 200, 9), h=2, w=3)          # not in the palette
    with caplog.at_level("WARNING"):
        cmix = rx.rgb_to_cmix(rgb, pal, context="tile_x.tif")
    assert (cmix == rx.DEFAULT_CMIX).all()
    assert "outside the class palette" in caplog.text
    assert "#07c809" in caplog.text             # the offending colour, packed hex
    assert "tile_x.tif" in caplog.text


def test_rgb_to_cmix_strict_raises_on_unknown_color():
    pal = rx.build_palette(CLASSES)
    rgb = solid((7, 200, 9))
    with pytest.raises(rx.PaletteError, match="outside the class palette"):
        rx.rgb_to_cmix(rgb, pal, strict=True)


def test_rgb_to_cmix_near_miss_is_not_snapped():
    """One-off from a palette colour must NOT be silently rounded to it."""
    pal = rx.build_palette(CLASSES)
    rgb = solid((34, 139, 35))                  # BM_VEGETATION is (34,139,34)
    with pytest.raises(rx.PaletteError):
        rx.rgb_to_cmix(rgb, pal, strict=True)


def test_rgb_to_cmix_rejects_non_rgb_shape():
    pal = rx.build_palette(CLASSES)
    with pytest.raises(ValueError, match=r"\(3, H, W\)"):
        rx.rgb_to_cmix(np.zeros((4, 4), dtype=np.uint8), pal)


def test_rgb_to_cmix_ignores_extra_bands():
    """A 4-band (RGBA) tile is read on its first three bands."""
    pal = rx.build_palette(CLASSES)
    rgb = np.zeros((4, 2, 2), dtype=np.uint8)
    rgb[0], rgb[1], rgb[2], rgb[3] = 34, 139, 34, 255
    assert (rx.rgb_to_cmix(rgb, pal, strict=True) == 3).all()


# --------------------------------------------------------------------------- #
# CMT XML                                                                      #
# --------------------------------------------------------------------------- #


def test_cmt_has_default_entry_at_index_zero(tmp_path):
    p = rx.write_cell_cmt_xml(tmp_path / "cell_rm.xml", CLASSES)
    text = Path(p).read_text(encoding="utf-8")
    assert '<Composite_Material index="0">' in text
    assert "<Name>DEFAULT</Name>" in text
    assert "<Name>BM_SOIL</Name>" in text
    # All 7 entries (0..6) present, in order.
    idx = [int(line.split('index="')[1].split('"')[0])
           for line in text.splitlines() if "<Composite_Material index=" in line]
    assert idx == [0, 1, 2, 3, 4, 5, 6]


def test_cmt_is_deterministic(tmp_path):
    a = Path(rx.write_cell_cmt_xml(tmp_path / "a_rm.xml", CLASSES)).read_bytes()
    b = Path(rx.write_cell_cmt_xml(tmp_path / "b_rm.xml", CLASSES)).read_bytes()
    assert a == b


def test_cmt_leaves_no_tmp_file(tmp_path):
    rx.write_cell_cmt_xml(tmp_path / "cell_rm.xml", CLASSES)
    assert list(tmp_path.glob("*.tmp")) == []


def test_cmt_parses_with_ier_reader(tmp_path, ier_cmt):
    """The conformance oracle: cdb-build's own parser must accept our CMT."""
    p = rx.write_cell_cmt_xml(tmp_path / "N33E035_rm.xml", CLASSES)
    table = ier_cmt.parse_cmt(Path(p))
    table.validate()                                   # weights sum to 100, unique idx

    assert set(table.materials) == {0, 1, 2, 3, 4, 5, 6}
    assert table.materials[0].name == "DEFAULT"
    assert table.materials[0].primary_substrate.materials[0].name == "BM_SOIL"
    assert table.materials[0].primary_substrate.materials[0].weight == 100
    assert table.materials[3].name == "GENVEGETATION"
    assert table.materials[3].primary_substrate.materials[0].name == "BM_VEGETATION"
    # Every entry has exactly one primary substrate summing to 100.
    for cm in table.materials.values():
        assert sum(m.weight for m in cm.primary_substrate.materials) == 100


def test_cmt_subset_covers_every_cmix_the_raster_can_hold(tmp_path, ier_cmt):
    """cdb-build subsets the CMT per tile; every value we emit must be defined.

    This is the exact check ``datasets/raster_material.build_rm_tile`` performs
    before writing a D006 — a CMIX in the raster with no CMT entry halts the build.
    """
    p = rx.write_cell_cmt_xml(tmp_path / "N33E035_rm.xml", CLASSES)
    table = ier_cmt.parse_cmt(Path(p))
    emittable = set(range(0, len(CLASSES) + 1))
    assert not emittable - set(table.materials)
    sub = ier_cmt.subset_cmt(table, emittable)
    sub.validate()
    assert set(sub.materials) == emittable


# --------------------------------------------------------------------------- #
# Raster assembly                                                              #
# --------------------------------------------------------------------------- #


def test_assemble_single_tile_values_and_profile(tmp_path):
    pal = rx.build_palette(CLASSES)
    rgb = np.zeros((3, 4, 4), dtype=np.uint8)
    rgb[:, 0, :] = np.array([[34], [139], [34]])            # row 0 -> vegetation (3)
    rgb[:, 1, :] = np.array([[28], [107], [160]])           # row 1 -> water (4)
    rgb[:, 2, :] = np.array([[101], [67], [33]])            # row 2 -> soil (6)
    # row 3 left black -> 0
    write_rgb(tmp_path / "t.tif", rgb)

    out = tmp_path / "cell_rm.tif"
    info = rx.assemble_rm_raster([tmp_path / "t.tif"], out, pal, strict=True)

    with rasterio.open(out) as src:
        assert src.count == 1
        assert src.dtypes[0] == "uint8"
        assert src.nodata is None            # CMIX 0 is a material, not nodata
        assert src.crs.to_epsg() == 4326
        a = src.read(1)
    assert list(a[0]) == [3, 3, 3, 3]
    assert list(a[1]) == [4, 4, 4, 4]
    assert list(a[2]) == [6, 6, 6, 6]
    assert list(a[3]) == [0, 0, 0, 0]
    assert info["n_tiles"] == 1
    assert info["histogram"][3] == 4


def test_assemble_mosaics_tiles_into_one_grid(tmp_path):
    """2x2 tiles of 4px at 0.001deg -> one 8x8 raster, each quadrant preserved."""
    pal = rx.build_palette(CLASSES)
    quadrants = {
        (0, 0): ((34, 139, 34), 3),        # NW vegetation
        (0, 1): ((28, 107, 160), 4),       # NE water
        (1, 0): ((237, 201, 175), 5),      # SW sand
        (1, 1): ((101, 67, 33), 6),        # SE soil
    }
    res, n = 0.001, 4
    for (r, c), (color, _) in quadrants.items():
        write_rgb(
            tmp_path / f"t_r{r}_c{c}.tif", solid(color, n, n),
            west=35.0 + c * n * res, north=34.0 - r * n * res, res=res,
        )

    out = tmp_path / "cell_rm.tif"
    info = rx.assemble_rm_raster(
        sorted(tmp_path.glob("t_*.tif")), out, pal, strict=True
    )
    assert (info["width"], info["height"]) == (8, 8)

    with rasterio.open(out) as src:
        a = src.read(1)
        b = src.bounds
    for (r, c), (_, cmix) in quadrants.items():
        block = a[r * n:(r + 1) * n, c * n:(c + 1) * n]
        assert (block == cmix).all(), f"quadrant r{r}c{c} = {np.unique(block)}"
    assert b.left == pytest.approx(35.0)
    assert b.top == pytest.approx(34.0)
    assert b.right == pytest.approx(35.0 + 8 * res)


def test_assemble_gap_between_tiles_is_default_cmix(tmp_path):
    """Unwritten area between disjoint tiles must read back as CMIX 0."""
    pal = rx.build_palette(CLASSES)
    res, n = 0.001, 4
    write_rgb(tmp_path / "a.tif", solid((34, 139, 34), n, n), west=35.0, north=34.0, res=res)
    # 4px gap to the east.
    write_rgb(tmp_path / "b.tif", solid((28, 107, 160), n, n),
              west=35.0 + 8 * res, north=34.0, res=res)

    out = tmp_path / "cell_rm.tif"
    rx.assemble_rm_raster(sorted(tmp_path.glob("*.tif")), out, pal, strict=True)
    with rasterio.open(out) as src:
        a = src.read(1)
    assert a.shape == (4, 12)
    assert (a[:, 0:4] == 3).all()
    assert (a[:, 4:8] == 0).all()      # the gap
    assert (a[:, 8:12] == 4).all()


def test_assemble_partial_edge_tile(tmp_path):
    """A short edge tile (as the classifier emits) still places correctly."""
    pal = rx.build_palette(CLASSES)
    res = 0.001
    write_rgb(tmp_path / "a.tif", solid((34, 139, 34), 4, 4), west=35.0, north=34.0, res=res)
    write_rgb(tmp_path / "b.tif", solid((101, 67, 33), 4, 2),   # 2px wide edge tile
              west=35.0 + 4 * res, north=34.0, res=res)

    out = tmp_path / "cell_rm.tif"
    info = rx.assemble_rm_raster(sorted(tmp_path.glob("*.tif")), out, pal, strict=True)
    assert (info["width"], info["height"]) == (6, 4)
    with rasterio.open(out) as src:
        a = src.read(1)
    assert (a[:, 0:4] == 3).all()
    assert (a[:, 4:6] == 6).all()


def test_assemble_black_tile_does_not_erase_neighbour_overlap(tmp_path):
    """An all-black tile overlapping a classified one must not blank it."""
    pal = rx.build_palette(CLASSES)
    res = 0.001
    write_rgb(tmp_path / "a_real.tif", solid((34, 139, 34), 4, 4),
              west=35.0, north=34.0, res=res)
    write_rgb(tmp_path / "b_black.tif", solid((0, 0, 0), 4, 4),
              west=35.0, north=34.0, res=res)      # exactly on top

    out = tmp_path / "cell_rm.tif"
    rx.assemble_rm_raster(sorted(tmp_path.glob("*.tif")), out, pal, strict=True)
    with rasterio.open(out) as src:
        a = src.read(1)
    assert (a == 3).all()


def test_assemble_histogram_matches_the_written_raster(tmp_path):
    """Histogram must describe the file, not the sum of per-tile writes.

    Overlapping tiles write the same pixel twice; counting at write time would
    double-report it and make the CLI summary disagree with the raster.
    """
    pal = rx.build_palette(CLASSES)
    res = 0.001
    write_rgb(tmp_path / "a.tif", solid((34, 139, 34), 4, 4), west=35.0, north=34.0, res=res)
    write_rgb(tmp_path / "b.tif", solid((0, 0, 0), 4, 4), west=35.0, north=34.0, res=res)
    write_rgb(tmp_path / "c.tif", solid((101, 67, 33), 4, 4),
              west=35.0 + 2 * res, north=34.0, res=res)   # partial overlap with a

    out = tmp_path / "cell_rm.tif"
    info = rx.assemble_rm_raster(sorted(tmp_path.glob("[abc].tif")), out, pal, strict=True)

    with rasterio.open(out) as src:
        a = src.read(1)
    vals, counts = np.unique(a, return_counts=True)
    actual = {int(v): int(n) for v, n in zip(vals, counts)}
    assert info["histogram"] == actual
    assert sum(info["histogram"].values()) == a.size


def test_assemble_is_deterministic(tmp_path):
    pal = rx.build_palette(CLASSES)
    res = 0.001
    write_rgb(tmp_path / "a.tif", solid((34, 139, 34), 4, 4), west=35.0, north=34.0, res=res)
    write_rgb(tmp_path / "b.tif", solid((101, 67, 33), 4, 4),
              west=35.0 + 4 * res, north=34.0, res=res)
    tiles = sorted(tmp_path.glob("[ab].tif"))

    one = tmp_path / "one_rm.tif"
    two = tmp_path / "two_rm.tif"
    rx.assemble_rm_raster(tiles, one, pal, strict=True)
    rx.assemble_rm_raster(tiles, two, pal, strict=True)
    assert one.read_bytes() == two.read_bytes()


def test_assemble_rejects_mixed_crs(tmp_path):
    pal = rx.build_palette(CLASSES)
    write_rgb(tmp_path / "a.tif", solid((34, 139, 34)), crs="EPSG:4326")
    write_rgb(tmp_path / "b.tif", solid((101, 67, 33)), crs="EPSG:3857")
    with pytest.raises(ValueError, match="disagree on CRS"):
        rx.assemble_rm_raster(sorted(tmp_path.glob("*.tif")), tmp_path / "o.tif", pal)


def test_assemble_empty_input_raises(tmp_path):
    pal = rx.build_palette(CLASSES)
    with pytest.raises(ValueError, match="no classified tiles"):
        rx.assemble_rm_raster([], tmp_path / "o.tif", pal)


def test_assemble_leaves_no_tmp_on_failure(tmp_path):
    """A palette failure mid-assembly must not leave a partial .tmp behind."""
    pal = rx.build_palette(CLASSES)
    write_rgb(tmp_path / "bad.tif", solid((7, 200, 9)))
    with pytest.raises(rx.PaletteError):
        rx.assemble_rm_raster([tmp_path / "bad.tif"], tmp_path / "o_rm.tif", pal, strict=True)
    assert not (tmp_path / "o_rm.tif").exists()
    assert list(tmp_path.glob("*.tmp")) == []


# --------------------------------------------------------------------------- #
# export_rm_pair                                                               #
# --------------------------------------------------------------------------- #


def test_export_rm_pair_from_directory(tmp_path):
    tiles = tmp_path / "N33E035_classified_tiles"
    tiles.mkdir()
    write_rgb(tiles / "N33E035_tile_r0_c0.tif", solid((34, 139, 34), 4, 4))
    # Sidecars that must be ignored by the tile glob.
    (tiles / "N33E035_tile_r0_c0.xml").write_text("<x/>", encoding="utf-8")
    (tiles / "all_imgs.txs").write_text("x", encoding="utf-8")

    info = rx.export_rm_pair(tiles, tmp_path / "N33E035.tif", CLASSES, strict=True)
    assert info is not None
    assert (tmp_path / "N33E035_rm.tif").is_file()
    assert (tmp_path / "N33E035_rm.xml").is_file()
    assert info["n_tiles"] == 1


def test_export_rm_pair_skips_dot_prefixed_temps(tmp_path):
    tiles = tmp_path / "t"
    tiles.mkdir()
    write_rgb(tiles / "real.tif", solid((34, 139, 34), 4, 4))
    write_rgb(tiles / ".N33E035_1234_mosaic.tmp.tif", solid((101, 67, 33), 4, 4))
    info = rx.export_rm_pair(tiles, tmp_path / "cell.tif", CLASSES, strict=True)
    assert info["n_tiles"] == 1


def test_export_rm_pair_from_single_file(tmp_path):
    write_rgb(tmp_path / "out.tif", solid((28, 107, 160), 4, 4))
    info = rx.export_rm_pair(tmp_path / "out.tif", tmp_path / "out.tif", CLASSES, strict=True)
    assert info["n_tiles"] == 1
    with rasterio.open(tmp_path / "out_rm.tif") as src:
        assert (src.read(1) == 4).all()


def test_export_rm_pair_missing_source_returns_none(tmp_path):
    assert rx.export_rm_pair(tmp_path / "nope", tmp_path / "o.tif", CLASSES) is None


def test_export_rm_pair_empty_dir_returns_none(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert rx.export_rm_pair(d, tmp_path / "o.tif", CLASSES) is None


def test_summarize_mentions_every_present_material(tmp_path):
    write_rgb(tmp_path / "out.tif", solid((28, 107, 160), 4, 4))
    info = rx.export_rm_pair(tmp_path / "out.tif", tmp_path / "out.tif", CLASSES)
    text = rx.summarize(info, CLASSES)
    assert "BM_WATER" in text
    assert "out_rm.tif" in text
    assert "out_rm.xml" in text
