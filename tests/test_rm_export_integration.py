"""End-to-end tests: the RM pair a real CLI run produces.

Covers **both** invocation styles the CLI supports:

* manifest mode  — ``cli.py --manifest geocell.toml``
* normal mode    — ``cli.py <input.tif> <output.tif>``

These drive ``cli.py`` as a subprocess, so exit codes, stdout contract and the
on-disk deliverables are all exercised exactly as a caller sees them. SAM3 is
off, which keeps every run on the deterministic KMeans-only path (no GPU, no
torch model download) — the RM contract does not depend on it, and the
mask-painted path is covered by the palette assertions either way.

The central invariant asserted here is the one cdb-build enforces at build time:
**every CMIX value present in the RM raster must be defined in the RM CMT.**

Run:  .venv/Scripts/python.exe -m pytest tests/test_rm_export_integration.py -v
Skip: -m "not slow"
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
CLI = REPO / "cli.py"

pytestmark = pytest.mark.slow


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _run_env():
    """Environment for a CLI subprocess.

    PROJ_LIB is pinned to rasterio's bundled proj.db: this machine has a
    PostgreSQL/PostGIS PROJ on PROJ_LIB whose database layout predates what
    pyproj needs, and manifest mode reads source footprints (in
    ``mosaic_catalog``) *before* ``core._setup_proj_lib`` ever gets imported.
    """
    env = dict(os.environ)
    bundled = REPO / ".venv" / "Lib" / "site-packages" / "rasterio" / "proj_data"
    if (bundled / "proj.db").is_file():
        env["PROJ_LIB"] = str(bundled)
        env["PROJ_DATA"] = str(bundled)
    return env


def run_cli(*args, timeout=1800):
    proc = subprocess.run(
        [str(PYTHON), str(CLI), *[str(a) for a in args]],
        cwd=str(REPO), env=_run_env(), capture_output=True, text=True, timeout=timeout,
    )
    return proc


def assert_no_tiles_failed(proc, tiles_dir: Path, expected: int | None = None):
    """Fail loudly when the classifier skipped tiles.

    ``core._classify_tile_worker`` failures are logged and counted but do NOT
    fail the run, so a resource-starved worker silently yields a partial tile
    set. Without this check that shows up much later as a confusing assertion
    about material content, when the real story is missing tiles.
    """
    assert "tiles failed" not in proc.stdout, (
        "classifier skipped tiles:\n"
        + "\n".join(ln for ln in proc.stdout.splitlines()
                    if "failed" in ln or "ERROR" in ln)[:3000]
    )
    if expected is not None:
        got = len([p for p in tiles_dir.glob("*.tif") if not p.name.startswith(".")])
        assert got == expected, f"expected {expected} tiles, found {got}"


def make_source(path: Path, west, north, res, n=64, seed=0):
    """A synthetic RGB ortho with several distinct colour blobs.

    Content is irrelevant to the RM contract — only that it classifies into
    more than one material, so the CMIX raster is not trivially uniform.
    """
    rng = np.random.default_rng(seed)
    rgb = np.zeros((3, n, n), dtype=np.uint8)
    half = n // 2
    # Four quadrants roughly matching vegetation / sand / soil / concrete.
    for (r0, r1, c0, c1), base in (
        ((0, half, 0, half), (40, 130, 45)),
        ((0, half, half, n), (232, 200, 176)),
        ((half, n, 0, half), (105, 70, 36)),
        ((half, n, half, n), (178, 178, 180)),
    ):
        for ch in range(3):
            block = rng.integers(-6, 7, size=(r1 - r0, c1 - c0))
            rgb[ch, r0:r1, c0:c1] = np.clip(base[ch] + block, 1, 255)

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", height=n, width=n, count=3, dtype="uint8",
        crs="EPSG:4326", transform=from_origin(west, north, res, res),
    ) as dst:
        dst.write(rgb)
    return path


def load_ier_cmt():
    """cdb-build's cmt_reader, or None when it is not installed."""
    for root in (
        Path("C:/cdb-build/runtime/venv/Lib/site-packages"),
        Path("C:/work/IER/src"),
    ):
        mod = root / "cdb_build" / "cmt_reader.py"
        if not mod.is_file():
            continue
        name = "_ier_cmt_reader_integration"
        spec = importlib.util.spec_from_file_location(name, mod)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            return None
        return module
    return None


def assert_rm_pair_is_ier_ready(rm_tif: Path, rm_xml: Path, n_classes=6):
    """Assert the pair satisfies cdb-build's [raster_material] input contract."""
    assert rm_tif.is_file(), f"missing RM raster: {rm_tif}"
    assert rm_xml.is_file(), f"missing RM CMT: {rm_xml}"

    with rasterio.open(rm_tif) as src:
        assert src.count == 1, "RM raster must be single-band"
        assert src.dtypes[0] in ("uint8", "uint16"), "RM raster must be uint8/uint16"
        assert src.crs is not None, "RM source has no CRS -> cdb-build rejects it"
        assert src.crs.to_epsg() == 4326
        assert src.nodata is None, "CMIX 0 is a material, not nodata"
        data = src.read(1)

    present = {int(v) for v in np.unique(data)}
    assert present <= set(range(n_classes + 1)), f"CMIX out of range: {present}"

    cmt = load_ier_cmt()
    if cmt is None:
        pytest.skip("cdb-build not installed; skipping CMT parser conformance")

    table = cmt.parse_cmt(rm_xml)
    table.validate()
    # THE contract: build_rm_tile raises for any CMIX absent from the CMT.
    missing = present - set(table.materials)
    assert not missing, f"CMIX {sorted(missing)} in raster but absent from CMT"
    # And the per-tile subset cdb-build derives must stay valid.
    cmt.subset_cmt(table, present).validate()
    return data, table


def tile_histogram(tiles_dir: Path):
    """CMIX histogram derived independently from the RGB tiles."""
    from backend.app import rm_export as rx
    from backend.app.core import MEA_CLASSES

    pal = rx.build_palette(MEA_CLASSES)
    hist: dict[int, int] = {}
    for t in sorted(tiles_dir.glob("*.tif")):
        if t.name.startswith("."):
            continue
        with rasterio.open(t) as src:
            arr = src.read()
        cmix = rx.rgb_to_cmix(arr, pal, strict=True, context=t.name)
        vals, counts = np.unique(cmix, return_counts=True)
        for v, n in zip(vals, counts):
            hist[int(v)] = hist.get(int(v), 0) + int(n)
    return hist


# --------------------------------------------------------------------------- #
# Manifest mode                                                                #
# --------------------------------------------------------------------------- #


def write_manifest(path: Path, src_dir: Path, out_tif: Path, south=33, west=35):
    path.write_text(
        f"[geocell]\n"
        f"south_lat = {south}\n"
        f"west_lon  = {west}\n\n"
        f"[[layers]]\n"
        f"folder   = '{src_dir}'\n"
        f"priority = 1\n"
        f"name     = 'synthetic'\n\n"
        f"[classify]\n"
        f"sam3 = false\n\n"
        f"[output]\n"
        f"path      = '{out_tif}'\n"
        f"overwrite = true\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "res,expect_multi_tile",
    [
        (1 / 500.0, False),   # ~500 px cell -> single 512 tile
        # 2x2 grid. Deliberately modest: classify_v6 sizes its tile pool at
        # os.cpu_count(), and on a many-core box each worker's CUDA context
        # can exhaust VRAM and take the pool down with BrokenProcessPool. That
        # is a pre-existing classifier limit, not an RM-export one, so this
        # test stays under it rather than racing it.
        (1 / 600.0, True),
    ],
    ids=["single_tile", "multi_tile"],
)
def test_manifest_run_emits_ier_ready_rm_pair(tmp_path, res, expect_multi_tile):
    # Cover the whole cell: a sparse source leaves KMeans training on a mostly
    # black mosaic, where cluster assignment is unstable run to run.
    src_dir = tmp_path / "imagery"
    make_source(src_dir / "patch.tif", west=35.0, north=34.0, res=res,
                n=int(round(1 / res)))

    out_tif = tmp_path / "out" / "N33E035.tif"
    manifest = write_manifest(tmp_path / "geocell.toml", src_dir, out_tif)

    proc = run_cli("--manifest", manifest)
    assert proc.returncode == 0, f"CLI failed:\n{proc.stdout[-4000:]}"
    assert "OK Saved (tiled folder):" in proc.stdout

    tiles = out_tif.with_name("N33E035_classified_tiles")
    assert tiles.is_dir()
    assert_no_tiles_failed(proc, tiles)
    n_tiles = len([p for p in tiles.glob("*.tif") if not p.name.startswith(".")])
    assert (n_tiles > 1) == expect_multi_tile, f"got {n_tiles} tile(s)"

    rm_tif = out_tif.with_name("N33E035_rm.tif")
    rm_xml = out_tif.with_name("N33E035_rm.xml")
    data, _ = assert_rm_pair_is_ier_ready(rm_tif, rm_xml)

    # The RM raster must reproduce the tiles exactly, pixel for pixel.
    vals, counts = np.unique(data, return_counts=True)
    rm_hist = {int(v): int(n) for v, n in zip(vals, counts)}
    assert rm_hist == tile_histogram(tiles)

    # ...and it must be more than one material, or the test proves nothing.
    assert len([k for k in rm_hist if k != 0]) >= 2, f"too uniform: {rm_hist}"

    # The pair is reported on stdout so an orchestrator can find it.
    assert "RM raster:" in proc.stdout
    assert "RM CMT:" in proc.stdout


def test_manifest_rm_raster_covers_the_whole_geocell(tmp_path):
    """RM bounds must span the geocell, not just the covered part of it.

    cdb-build tiles the cell and fills anything the source misses with CMIX 0;
    a raster that stops at the imagery edge would leave real tiles unbuilt.
    """
    src_dir = tmp_path / "imagery"
    make_source(src_dir / "patch.tif", west=35.10, north=33.90, res=1 / 500.0, n=64)
    out_tif = tmp_path / "out" / "N33E035.tif"
    manifest = write_manifest(tmp_path / "geocell.toml", src_dir, out_tif)

    proc = run_cli("--manifest", manifest)
    assert proc.returncode == 0, proc.stdout[-4000:]

    with rasterio.open(out_tif.with_name("N33E035_rm.tif")) as src:
        b = src.bounds
        data = src.read(1)
    # Geocell N33E035 spans (35,33)-(36,34); allow a pixel of slack.
    assert b.left == pytest.approx(35.0, abs=0.01)
    assert b.bottom == pytest.approx(33.0, abs=0.01)
    assert b.right == pytest.approx(36.0, abs=0.01)
    assert b.top == pytest.approx(34.0, abs=0.01)
    # Most of the cell has no imagery -> DEFAULT.
    assert (data == 0).mean() > 0.5


def test_manifest_rerun_is_reproducible(tmp_path):
    """Same manifest twice -> byte-identical CMT and same RM geometry."""
    src_dir = tmp_path / "imagery"
    make_source(src_dir / "patch.tif", west=35.10, north=33.90, res=1 / 500.0, n=64)
    out_tif = tmp_path / "out" / "N33E035.tif"
    manifest = write_manifest(tmp_path / "geocell.toml", src_dir, out_tif)

    assert run_cli("--manifest", manifest).returncode == 0
    xml_a = out_tif.with_name("N33E035_rm.xml").read_bytes()
    with rasterio.open(out_tif.with_name("N33E035_rm.tif")) as src:
        shape_a, bounds_a = src.shape, src.bounds

    assert run_cli("--manifest", manifest).returncode == 0
    xml_b = out_tif.with_name("N33E035_rm.xml").read_bytes()
    with rasterio.open(out_tif.with_name("N33E035_rm.tif")) as src:
        shape_b, bounds_b = src.shape, src.bounds

    # The CMT is a pure function of the class list -> byte-identical.
    assert xml_a == xml_b
    # KMeans seeding may reshuffle which cluster wins, but the grid must not move.
    assert shape_a == shape_b
    assert bounds_a == bounds_b


# --------------------------------------------------------------------------- #
# Normal conversion mode                                                       #
# --------------------------------------------------------------------------- #


def test_normal_conversion_emits_ier_ready_rm_pair(tmp_path):
    """cli.py <in.tif> <out.tif> — the positional form (implies --mea)."""
    src = make_source(tmp_path / "in.tif", west=35.10, north=33.90, res=1 / 500.0, n=192)
    out = tmp_path / "out.tif"

    proc = run_cli(src, out, "--no-sam3")
    assert proc.returncode == 0, f"CLI failed:\n{proc.stdout[-4000:]}"
    assert "OK Saved:" in proc.stdout

    rm_tif = tmp_path / "out_rm.tif"
    rm_xml = tmp_path / "out_rm.xml"
    data, _ = assert_rm_pair_is_ier_ready(rm_tif, rm_xml)

    # Normal mode classifies the whole input, so nothing should be DEFAULT
    # beyond genuine near-black nodata — assert real materials dominate.
    assert (data != 0).mean() > 0.5, "expected most of the input to classify"
    assert len({int(v) for v in np.unique(data)} - {0}) >= 2

    # RM raster must line up with the classified output it came from.
    with rasterio.open(rm_tif) as rm, rasterio.open(out) as cls:
        assert rm.shape == cls.shape
        assert rm.bounds == pytest.approx(tuple(cls.bounds))


def test_normal_conversion_rm_matches_classified_output_pixelwise(tmp_path):
    """Every RM pixel is the exact inverse of the classified tile's colour."""
    from backend.app import rm_export as rx
    from backend.app.core import MEA_CLASSES

    src = make_source(tmp_path / "in.tif", west=35.10, north=33.90, res=1 / 500.0, n=192)
    out = tmp_path / "out.tif"
    assert run_cli(src, out, "--no-sam3").returncode == 0

    with rasterio.open(out) as cls:
        rgb = cls.read()
    expected = rx.rgb_to_cmix(rgb, rx.build_palette(MEA_CLASSES), strict=True)
    with rasterio.open(tmp_path / "out_rm.tif") as rm:
        actual = rm.read(1)
    assert np.array_equal(actual, expected)


# --------------------------------------------------------------------------- #
# cdb-build ingest (the real downstream consumer)                              #
# --------------------------------------------------------------------------- #

CDB_BUILD = Path("C:/cdb-build/cdb-build.cmd")
CDB_PY = Path("C:/cdb-build/runtime/venv/Scripts/python.exe")

needs_cdb_build = pytest.mark.skipif(
    not (CDB_BUILD.is_file() and CDB_PY.is_file()),
    reason="cdb-build is not installed at C:/cdb-build",
)


def run_cdb_build(*args, timeout=1800):
    return subprocess.run(
        [str(CDB_BUILD), *[str(a) for a in args]],
        cwd=str(CDB_BUILD.parent), capture_output=True, text=True, timeout=timeout,
    )


def write_ier_manifest(path: Path, cdb_root: Path, rm_tif: Path, rm_xml: Path,
                       south=33, west=35, min_lod=0, max_lod=2):
    path.write_text(
        f"[geocell]\nsouth_lat = {south}\nwest_lon  = {west}\n\n"
        f'[output]\ncdb_root = "{cdb_root.as_posix()}"\n\n'
        f"[lod]\nmin_lod = {min_lod}\nmax_lod = {max_lod}\n\n"
        f"[raster_material]\n"
        f'source_tiff    = "{rm_tif.as_posix()}"\n'
        f'source_cmt_xml = "{rm_xml.as_posix()}"\n',
        encoding="utf-8",
    )
    return path


@needs_cdb_build
def test_rm_pair_builds_a_valid_cdb_geocell(tmp_path):
    """The whole point: MC's RM pair drives a clean cdb-build RM geocell.

    Classify -> export RM pair -> cdb-build build -> cdb-build validate, then
    assert the D005/D006 invariants cdb-build itself relies on.
    """
    src_dir = tmp_path / "imagery"
    make_source(src_dir / "patch.tif", west=35.10, north=33.90, res=1 / 500.0, n=64)
    out_tif = tmp_path / "out" / "N33E035.tif"
    manifest = write_manifest(tmp_path / "geocell.toml", src_dir, out_tif)

    assert run_cli("--manifest", manifest).returncode == 0
    rm_tif = out_tif.with_name("N33E035_rm.tif")
    rm_xml = out_tif.with_name("N33E035_rm.xml")
    assert rm_tif.is_file() and rm_xml.is_file()

    cdb_root = tmp_path / "cdb_root"
    ier_manifest = write_ier_manifest(
        tmp_path / "rm_only.toml", cdb_root, rm_tif, rm_xml
    )

    build = run_cdb_build("build", ier_manifest)
    assert build.returncode == 0, f"cdb-build build failed:\n{build.stdout[-4000:]}\n{build.stderr[-4000:]}"
    assert "Build complete" in build.stdout

    valid = run_cdb_build("validate", cdb_root)
    assert valid.returncode == 0, f"cdb-build validate failed:\n{valid.stdout[-4000:]}"
    assert "no CRITICAL or HIGH findings" in valid.stdout

    d005 = sorted((cdb_root / "Tiles").rglob("*_D005_*.tif"))
    d006 = sorted((cdb_root / "Tiles").rglob("*_D006_*.xml"))
    assert d005, "no D005 tiles produced"
    assert len(d005) == len(d006), "D005/D006 must always be written as a pair"

    cmt = load_ier_cmt()
    if cmt is None:
        pytest.skip("cdb-build cmt_reader not importable here")

    for tif in d005:
        xml = Path(str(tif).replace("005_RMTexture", "006_RMDescriptor")
                           .replace("_D005_", "_D006_").replace(".tif", ".xml"))
        assert xml.is_file(), f"missing D006 for {tif.name}"
        with rasterio.open(tif) as s:
            assert s.count == 1
            assert (s.width, s.height) == (1024, 1024)
            used = {int(v) for v in np.unique(s.read(1))}
        table = cmt.parse_cmt(xml)
        table.validate()
        # cdb-build subsets the source CMT to exactly the tile's CMIX values.
        assert set(table.materials) == used, (
            f"{tif.name}: D005 uses {sorted(used)}, D006 defines "
            f"{sorted(table.materials)}"
        )


@needs_cdb_build
def test_cmt_without_default_entry_halts_cdb_build(tmp_path):
    """Guards the reason CMIX 0 exists at all.

    cdb-build fills every pixel its source does not cover with CMIX 0 and
    hard-fails when that index has no CMT entry. If this ever stops failing,
    the DEFAULT entry has become untested rather than unnecessary.
    """
    import re

    src_dir = tmp_path / "imagery"
    make_source(src_dir / "patch.tif", west=35.10, north=33.90, res=1 / 500.0, n=64)
    out_tif = tmp_path / "out" / "N33E035.tif"
    manifest = write_manifest(tmp_path / "geocell.toml", src_dir, out_tif)
    assert run_cli("--manifest", manifest).returncode == 0

    broken_tif = tmp_path / "broken_rm.tif"
    broken_xml = tmp_path / "broken_rm.xml"
    broken_tif.write_bytes(out_tif.with_name("N33E035_rm.tif").read_bytes())
    stripped = re.sub(
        r'  <Composite_Material index="0">.*?  </Composite_Material>\n',
        "",
        out_tif.with_name("N33E035_rm.xml").read_text(encoding="utf-8"),
        flags=re.S,
    )
    assert 'index="0"' not in stripped
    broken_xml.write_text(stripped, encoding="utf-8")

    ier_manifest = write_ier_manifest(
        tmp_path / "broken.toml", tmp_path / "cdb_broken", broken_tif, broken_xml,
        min_lod=2, max_lod=2,
    )
    build = run_cdb_build("build", ier_manifest)
    assert build.returncode != 0, "cdb-build accepted a CMT with no CMIX 0 entry"
    combined = build.stdout + build.stderr
    assert "NOT defined in the source CMT" in combined
    assert "[0]" in combined


def test_rm_failure_does_not_fail_the_run(tmp_path, monkeypatch):
    """A broken RM export must never turn a good classification into exit 1."""
    import cli as cli_mod

    def boom(*a, **k):
        raise RuntimeError("synthetic RM failure")

    monkeypatch.setattr("backend.app.rm_export.export_rm_pair", boom)
    info = cli_mod._emit_rm_pair(tmp_path, tmp_path / "x.tif", [])
    assert info is None      # swallowed, reported, run continues
