# -*- coding: utf-8 -*-
"""Tests for the geocell manifest schema (backend/app/manifest.py).

Plain-runner (no pytest):
    py backend/tests/test_manifest.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.manifest import DEFAULT_LAYER_GLOBS, load_manifest, GeocellManifest  # noqa: E402


def _write(td, text):
    p = Path(td) / "m.toml"
    p.write_text(text, encoding="utf-8")
    return p


VALID = """
[geocell]
south_lat = 45
west_lon  = 6

[[layers]]
folder   = "D:/orthos/hi_res"
priority = 1
name     = "hi"

[[layers]]
folder   = "D:/orthos/base"
priority = 2

[output]
path = "D:/out/N45E006.tif"
"""


def test_valid_manifest_parses():
    with tempfile.TemporaryDirectory() as td:
        m = load_manifest(_write(td, VALID))
    assert isinstance(m, GeocellManifest)
    assert m.geocell.south_lat == 45 and m.geocell.west_lon == 6
    assert [l.priority for l in m.layers] == [1, 2]
    # Default glob covers TIFF + JPEG 2000 so a layer of either format is picked
    # up without configuration.
    assert m.layers[0].glob == list(DEFAULT_LAYER_GLOBS)
    assert m.layers[0].glob_patterns() == ["**/*.tif", "**/*.tiff", "**/*.jp2"]
    assert m.layers[0].name == "hi"
    assert m.output.overwrite is False             # default
    assert m.parallelism.workers == "auto"         # default
    # to_geocell() validates + names the cell
    assert m.geocell.to_geocell().name == "N45E006"


def test_extra_top_level_table_rejected():
    # Locked decision: SDE/water config stays in shapefile_config.json, so a
    # stray [sde] table must fail loudly (extra="forbid").
    bad = VALID + '\n[sde]\nenabled = true\n'
    with tempfile.TemporaryDirectory() as td:
        try:
            load_manifest(_write(td, bad))
        except Exception:
            return
    raise AssertionError("expected a validation error for an unknown [sde] table")


def test_unknown_layer_field_rejected():
    bad = VALID.replace('priority = 1\nname     = "hi"', 'priority = 1\nweight = 3')
    with tempfile.TemporaryDirectory() as td:
        try:
            load_manifest(_write(td, bad))
        except Exception:
            return
    raise AssertionError("expected a validation error for an unknown layer field")


def test_missing_layers_rejected():
    bad = "[geocell]\nsouth_lat=45\nwest_lon=6\n[output]\npath=\"D:/o.tif\"\n"
    with tempfile.TemporaryDirectory() as td:
        try:
            load_manifest(_write(td, bad))
        except Exception:
            return
    raise AssertionError("expected a validation error when [[layers]] is absent")


def test_nonsnapped_west_lon_rejected_via_to_geocell():
    bad = VALID.replace("west_lon  = 6", "west_lon  = 7").replace("south_lat = 45", "south_lat = 60")
    with tempfile.TemporaryDirectory() as td:
        m = load_manifest(_write(td, bad))
        try:
            m.geocell.to_geocell()
        except ValueError:
            return
    raise AssertionError("expected ValueError: west_lon=7 not a multiple of 2° zone width")


def test_flat_sources_glob_expands_and_empty_glob_fails():
    # `sources` is a top-level array, so it must precede the first table header.
    with tempfile.TemporaryDirectory() as td:
        # Two real files so a glob has something to match.
        (Path(td) / "a.tif").write_bytes(b"x")
        (Path(td) / "b.tif").write_bytes(b"x")
        good = f'sources = ["{Path(td).as_posix()}/*.tif"]\n' + VALID
        m = load_manifest(_write(td, good))
        assert m.sources is not None and len(m.sources) == 2

        bad = f'sources = ["{Path(td).as_posix()}/none_*.tif"]\n' + VALID
        try:
            load_manifest(_write(td, bad))
        except ValueError:
            return
    raise AssertionError("expected ValueError for a sources glob that matched no files")


def test_duplicate_layer_priority_rejected():
    bad = VALID.replace("priority = 2", "priority = 1")   # two layers at priority 1
    with tempfile.TemporaryDirectory() as td:
        try:
            load_manifest(_write(td, bad))
        except Exception:
            return
    raise AssertionError("expected a validation error for duplicate layer priorities")


def test_relative_paths_anchored_to_manifest_dir():
    text = (
        "[geocell]\nsouth_lat = 45\nwest_lon = 6\n"
        "[[layers]]\nfolder = \"orthos/hi\"\npriority = 1\n"
        "[output]\npath = \"out/N45E006.tif\"\n"
    )
    with tempfile.TemporaryDirectory() as td:
        sub = Path(td) / "sub"
        sub.mkdir()
        f = sub / "geocell.toml"
        f.write_text(text, encoding="utf-8")
        m = load_manifest(f)
    # Relative folder/output resolve against the manifest's directory, not CWD.
    assert Path(m.layers[0].folder) == sub.resolve() / "orthos" / "hi"
    assert Path(m.output.path) == sub.resolve() / "out" / "N45E006.tif"


def test_absolute_paths_pass_through_unchanged():
    with tempfile.TemporaryDirectory() as td:
        m = load_manifest(_write(td, VALID))
    # VALID uses drive-absolute paths (D:/...) — anchoring must leave them alone.
    assert str(m.layers[0].folder).replace("\\", "/") == "D:/orthos/hi_res"


def test_string_glob_narrows_layer_to_one_format():
    # A single-string glob (e.g. a JP2-only layer) still validates and normalizes
    # to a one-item pattern list.
    text = VALID.replace('priority = 1\nname     = "hi"',
                         'priority = 1\nglob     = "**/*.jp2"')
    with tempfile.TemporaryDirectory() as td:
        m = load_manifest(_write(td, text))
    assert m.layers[0].glob == "**/*.jp2"
    assert m.layers[0].glob_patterns() == ["**/*.jp2"]


def test_list_glob_accepts_multiple_patterns():
    # Mixed-format layer via an explicit list.
    text = VALID.replace('priority = 1\nname     = "hi"',
                         'priority = 1\nglob     = ["**/*.tif", "**/*.jp2"]')
    with tempfile.TemporaryDirectory() as td:
        m = load_manifest(_write(td, text))
    assert m.layers[0].glob == ["**/*.tif", "**/*.jp2"]
    assert m.layers[0].glob_patterns() == ["**/*.tif", "**/*.jp2"]


def test_empty_glob_list_rejected():
    bad = VALID.replace('priority = 1\nname     = "hi"',
                        'priority = 1\nglob     = []')
    with tempfile.TemporaryDirectory() as td:
        try:
            load_manifest(_write(td, bad))
        except Exception:
            return
    raise AssertionError("expected a validation error for an empty glob list")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
