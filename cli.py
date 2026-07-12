# -*- coding: utf-8 -*-
EXAMPLES_TEXT = """
Material Classification CLI - Full Guide

TWO INVOCATION STYLES
────────────────────────────────────────────────────────────────────────
  Simple (positional, recommended):
      cli.py <input_path> <output_path>

      Defaults to the 6-material MEA pipeline (--mea --sam3-enabled).
      SDE (buildings/roads) + water_mask config are read from
      shapefile_config.json (next to app_config.json — see
      "SHAPEFILE CONFIG" below).

  Flag-based (full control):
      cli.py --input <file_or_folder> --classes <N>
             [--output <path>] [--step <step>] [--mode <mode>]
             [--smoothing <value>] [--tile-size <px>]
             [--workers <N>] [--tiling] [--max-threads]
             [--no-spectral] [--no-texture] [--indices]
             [--detect-shadows]
             [--mea] [--no-sam3] [--water-mask <path>]

Parameter meanings:

    --input / -i PATH      [required]
        Input raster file (.tif/.tiff/.jpg/.jpeg) OR folder.
        If folder is provided, all supported files are processed.
        Example: --input C:\\images\\photo.tif
        Example: --input C:\\images\\

    --classes / -c N       [required]
        Number of material classes (minimum 2).
        Colors are deterministic by class index.
        Example: --classes 3

    --output / -o PATH
        Output file or output folder.
        Default: next to input with suffix _classified.tif / _full.tif
        Example: --output C:\\results\\output.tif
        Example: --output C:\\results\\

    --step {step1|step2|full}
        step1 = classification + export only
        step2 = vector rasterization only (expects classified input)
        full  = full pipeline (default)

    --mode {regular|multispectral}
        regular       = RGB mode (default)
        multispectral = multispectral mode

    --smoothing {none|median_1|median_2|median_3|median_5}
        Post-classification smoothing filter.

    --tile-size PX
        Tile size in pixels. Suggested: 256, 512, 1024, 2048, 4096.
        Use -1 for default.

    --workers N
        Number of tile workers. Use -1 for automatic.

    --image-workers N
        Number of images to process in parallel in folder mode.
        Use -1 for automatic.

    --tiling
        Enable tile-based processing.

    --max-threads
        Use maximum available CPU threads.

    --no-spectral
        Disable spectral features.

    --no-texture
        Disable texture features.

    --indices
        Enable spectral indices.

    --detect-shadows
        Enable shadow detection and inference.

    --mea
        Use the 6-material MEA preset:
        BM_VEGETATION, BM_SAND, BM_SOIL (KMeans-source);
        BM_ASPHALT, BM_CONCRETE (SAM3 / shapefile mask-source);
        BM_WATER (shapefile-only).
        When --mea is set, --classes is ignored. Triggers the
        SAM3-first classify_v6 pipeline (matches the GUI).

    --no-sam3
        Disable the SAM3 mask stage in --mea mode (KMeans-only fallback).
        Default: SAM3 enabled when --mea is set.

    --water-mask PATH
        Path to a water-mask GeoTIFF (band 1 > 0 = water). Painted
        directly as BM_WATER. Overrides the water_mask configured in
        shapefile_config.json for this run. --mea only.

SHAPEFILE CONFIG (shapefile_config.json)
────────────────────────────────────────────────────────────────────────
Lives next to app_config.json (project root in dev, install dir for
the bundled exe). Buildings and roads are pulled per-raster from an Esri
enterprise geodatabase (the "sde" block); water is a single raster mask.

    {
      "water_mask": "C:/data/water_mask.tif",
      "sde": {
        "enabled": true,
        "connection_file": "C:/data/conn.sde",
        "arcpy_python": "C:/.../arcgispro-py3/python.exe",
        "tile_size_metres": 5000,
        "timeout_seconds": 1800,
        "road_width_attr": "WIDTH",
        "road_width_fallback_m": 2.0,
        "layers": { "buildings": "GDB.SCHEMA.BUILDINGS",
                    "roads": "GDB.SCHEMA.ROADS" }
      }
    }

Behavior:
  - water_mask: a georeferenced GeoTIFF where band 1 > 0 marks water.
    Covers the whole AOI; it's reprojected/clipped to each ortho
    automatically and painted as BM_WATER. "" = no water painted.
  - sde: per ortho, building/road features are extracted for the
    raster's footprint via an arcpy subprocess. Road LINES are buffered
    to polygons using road_width_attr (full width in metres; <=10-char
    field name) or road_width_fallback_m (default 2 m) when absent.
  - --water-mask overrides water_mask for a single run.
  - SDE disabled / no features → SAM3 takes over for roads & buildings
    (water only paints when a water_mask is set).

Examples:

    python cli.py --input photo.tif --classes 3

    python cli.py --input C:\\images\\ --classes 5 --mode multispectral

    python cli.py --input photo.tif --classes 4 --step step1 --tile-size 1024 --output C:\\results\\

    python cli.py --input photo.tif --classes 3 --tiling --workers 8 --smoothing median_3 --no-texture

    python cli.py --input C:\\images\\ --classes 5 --tiling --workers 4 --image-workers 3

    python cli.py --input ms_image.tif --classes 5 --mode multispectral --detect-shadows

    python cli.py --input photo.tif --classes 3 --tile-size -1 --workers -1

    # ── Simple positional form (defaults to --mea --sam3-enabled) ──
    # Shapefiles auto-trimmed from shapefile_config.json:
    python cli.py C:\\orthos\\area_42.tif C:\\results\\area_42.tif

    # Same, but folder in / folder out:
    python cli.py C:\\orthos\\ C:\\results\\

    # ── Flag-based MEA (SAM3-first) — same pipeline the GUI uses ──
    python cli.py --input photo.tif --mea --output C:\\results\\

    # MEA with an explicit water mask (overrides the config water_mask):
    python cli.py photo.tif out.tif --water-mask water_mask.tif

    # MEA, KMeans-only (no SAM3, no SDE — roads/buildings/water empty):
    python cli.py photo.tif out.tif --no-sam3

LAYERED-PRIORITY GEOCELL MODE (--manifest)
────────────────────────────────────────────────────────────────────────
Instead of a batch of independent tiffs, describe ONE CDB geocell and
LAYERS of source orthos in a TOML manifest. The layers are composited into
a single EPSG:4326 mosaic for that geocell (highest priority wins per
pixel, lower layers fill underneath), then classified with the 6-material
MEA pipeline. Output = one classified GeoTIFF + MEA XML for the geocell —
ready to drop into a CDB build as raster_material.

    python cli.py --manifest geocell.toml

Manifest schema (geocell.toml):

    [geocell]
    south_lat = 45          # integer S edge; west_lon must be a multiple of
    west_lon  = 6           # the CDB lat-zone width (1° for |lat|<50, 2° 50-70,
                            # 4° 70-75, 6° 75-80, 12° >=80). Names the cell N45E006.

    [[layers]]
    folder   = "D:/orthos/2024_campaign"
    priority = 1            # 1 = highest, wins on overlap
    glob     = "**/*.tif"   # optional; default picks up *.tif/*.tiff/*.jp2.
                            #   May be a list: ["**/*.tif", "**/*.jp2"]
    name     = "2024"       # optional, logging only

    [[layers]]
    folder   = "D:/orthos/archive_2019_jp2"   # a JPEG-2000 layer, e.g.
    priority = 2                               # composited under the 2024 tiffs

    # sources = ["D:/orthos/one_off.tif"]   # optional flat files, below all layers

    [classify]                 # optional; defaults = SAM3 on, water from config
    sam3       = true          # false = KMeans naturals only (needs no PyTorch)
    water_mask = "D:/data/water_mask.tif"   # overrides shapefile_config.json

    [output]
    path      = "D:/cdb_out/N45E006_material.tif"   # .xml written beside it
    overwrite = false

Notes:
  - Layers may be GeoTIFF or JPEG 2000 (.jp2); each layer's default glob picks
    up .tif/.tiff/.jp2, so different layers can mix formats in one manifest.
  - Reprojects every layer to EPSG:4326 and samples at the FINEST covering
    layer's resolution (auto). A cell that would exceed ~20000 px/side is
    coarsened with a warning rather than exploding.
  - Files not touching the geocell are dropped before compositing.
  - [classify] mirrors the positional flags: sam3=false = --no-sam3 (KMeans
    naturals only, no PyTorch); water_mask = --water-mask (overrides
    shapefile_config.json). Omit the table for the defaults (SAM3 on, water
    from config). SDE (roads/buildings) always comes from shapefile_config.json.
  - --manifest cannot be combined with INPUT/OUTPUT/--input/--output/--classes.
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Force UTF-8 for stdout/stderr on Windows so print() with box-drawing
# characters, arrows, etc. doesn't throw UnicodeEncodeError under cmd.exe's
# default cp1252 codec. Matches backend/app/main.py top-of-file setup so
# the GUI and CLI behave identically. Must run BEFORE backend imports so
# subprocess workers inherit PYTHONIOENCODING.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Force UTF-8 for stdout/stderr on Windows so print() with box-drawing
# characters, arrows, etc. doesn't throw UnicodeEncodeError under cmd.exe's
# default cp1252 codec. Matches the backend/app/main.py top-of-file setup
# so the GUI and CLI behave the same way. Must run BEFORE any backend
# import so worker subprocesses inherit PYTHONIOENCODING.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SMOOTHING_OPTIONS = ["none", "median_1", "median_2", "median_3", "median_5"]
TILE_SIZE_OPTIONS = [256, 512, 1024, 2048, 4096]
VALID_EXTENSIONS = {".tif", ".tiff", ".jpg", ".jpeg"}

# Fixed color palette — same index always gets the same color
PALETTE = [
    "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF",
    "#00FFFF", "#FF8000", "#8000FF", "#00FF80", "#FF0080",
    "#0080FF", "#80FF00", "#FF4500", "#1E90FF", "#32CD32",
    "#FF1493", "#FFD700", "#4B0082", "#00CED1", "#FF6347",
    "#9400D3", "#00FA9A", "#FF69B4", "#ADFF2F", "#DC143C",
    "#00BFFF", "#7FFF00", "#20B2AA", "#FF7F50", "#6A5ACD",
]


def build_classes(count: int):
    """Return a list of class dicts. Same index always gets the same color."""
    classes = []
    for i in range(count):
        if i < len(PALETTE):
            color = PALETTE[i]
        else:
            import colorsys
            hue = (i * 0.618033988749895) % 1.0
            sat = 0.9 if i % 2 == 0 else 0.7
            val = 0.95 if (i // 2) % 2 == 0 else 0.75
            r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
            color = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
        classes.append({"id": f"class-{i+1}", "name": f"Class {i+1}", "color": color})
    return classes


def run_single(
    raster_path: str,
    output_path: str,
    args,
    classes: list,
    pretrained_scaler=None,
    pretrained_kmeans=None,
    pretrained_color_table=None,
    pretrained_mea_mapping=None,
):
    """Run pipeline on a single raster file.

    Routing:
      --mea        → classify_v6 (SAM3-first 6-material pipeline; matches GUI)
      legacy mode  → classify / classify_and_export (KMeans-only)
    """
    from backend.app.core import classify, classify_and_export, rasterize_vectors_onto_classification

    feature_flags = {
        "spectral": not args.no_spectral,
        "texture": not args.no_texture,
        "indices": args.indices,
    }
    tile_size = args.tile_size if args.tile_size != -1 else 512
    workers = args.workers if args.workers != -1 else max(1, os.cpu_count() or 1)
    max_threads = os.cpu_count() if args.max_threads else None
    is_mea = bool(getattr(args, "mea", False))

    # Build vector_layers from --vector arguments (legacy + step2 path)
    vector_layers = [
        {
            "id": f"vector-{i+1}",
            "name": Path(v).name,
            "filePath": v,
            "classId": classes[0]["id"] if classes else "class-1",
        }
        for i, v in enumerate(args.vector)
    ]

    # When --mea: assign per-layer class and inject overrideColor
    if is_mea and vector_layers:
        mea_by_name = {c["name"]: c for c in classes}
        for i, vl in enumerate(vector_layers):
            cls_name = args.vector_class[i] if i < len(args.vector_class) else classes[i % len(classes)]["name"]
            cls = mea_by_name.get(cls_name, classes[i % len(classes)])
            vl["classId"] = cls["id"]
            hex_c = cls["color"].lstrip("#")
            vl["overrideColor"] = [int(hex_c[0:2], 16), int(hex_c[2:4], 16), int(hex_c[4:6], 16)]

    common = dict(
        classes=classes,
        smoothing=args.smoothing,
        feature_flags=feature_flags,
        output_path=output_path,
        tile_mode=args.tiling,
        tile_max_pixels=tile_size ** 2,
        tile_overlap=0,
        tile_output_dir=None,
        tile_workers=workers,
        detect_shadows=args.detect_shadows,
        max_threads=max_threads,
        pretrained_scaler=pretrained_scaler,
        pretrained_kmeans=pretrained_kmeans,
        pretrained_color_table=pretrained_color_table,
        pretrained_mea_mapping=pretrained_mea_mapping,
    )

    # ── step2 (vector rasterization) is the same in MEA and legacy modes ────
    if args.step == "step2":
        return rasterize_vectors_onto_classification(
            classification_path=raster_path,
            vector_layers=vector_layers,
            classes=classes,
            output_path=output_path,
            tile_mode=args.tiling,
            tile_max_pixels=tile_size ** 2,
            tile_overlap=0,
            tile_output_dir=None,
            tile_workers=workers,
            max_threads=max_threads,
        )

    # ── MEA (6-material) → classify_v6 ──────────────────────────────────────
    if is_mea:
        from backend.app.pipeline import classify_v6

        # classify_v6 handles its own kmeans + masks + xml; vector_layers
        # are NOT a v6 input. If the user passed --vector with --mea, we run
        # v6 first then overlay vectors on top via step2 (full mode only).
        v6_kwargs = {k: v for k, v in common.items() if k != "classes"}
        result = classify_v6(
            raster_path=raster_path,
            classes=classes,
            sam3_enabled=getattr(args, "sam3_enabled", True),
            water_mask=getattr(args, "water_mask", None),
            single_fused_output=True,  # CLI: emit only the fused output, at the requested path
            **v6_kwargs,
        )
        if result.get("status") != "ok":
            return result

        # step1 = classification only; full = classification + vector overlay
        if args.step == "full" and vector_layers:
            v6_output = result.get("outputPath")
            return rasterize_vectors_onto_classification(
                classification_path=v6_output,
                vector_layers=vector_layers,
                classes=classes,
                output_path=output_path,
                tile_mode=args.tiling,
                tile_max_pixels=tile_size ** 2,
                tile_overlap=0,
                tile_output_dir=None,
                tile_workers=workers,
                max_threads=max_threads,
            )
        return result

    # ── Legacy non-MEA (custom KMeans count) ────────────────────────────────
    if args.step == "step1":
        return classify_and_export(raster_path=raster_path, **common)
    return classify(raster_path=raster_path, vector_layers=vector_layers, **common)  # full


def derive_output(input_path: Path, output_arg: str, suffix: str, input_root: Path | None = None) -> str:
    """Derive output file path from input and optional output argument."""
    if output_arg:
        out = Path(output_arg)
        if out.is_dir() or not out.suffix:
            if input_root:
                relative_parent = input_path.relative_to(input_root).parent
                output_dir = out / relative_parent
            else:
                output_dir = out
            output_dir.mkdir(parents=True, exist_ok=True)
            return str(output_dir / (input_path.stem + suffix + ".tif"))
        # If output has no suffix, treat as directory-like
        return str(out)
    return str(input_path.parent / (input_path.stem + suffix + ".tif"))


def run_manifest(manifest_path: str):
    """Layered-priority geocell run driven by a TOML manifest.

    Parses the manifest, composites its priority layers of source orthos into a
    single EPSG:4326 mosaic for the manifest's CDB geocell (highest priority
    wins per pixel), then classifies that mosaic with the 6-material MEA
    pipeline (``classify_v6``). SAM3 and the water-mask come from the manifest's
    optional ``[classify]`` table (defaults: SAM3 on, water from config); SDE
    (roads/buildings) config is read from ``shapefile_config.json`` exactly as in
    the positional form. Output is a folder of georeferenced classified tiles at
    ``<cell>_classified_tiles/`` next to the manifest's ``[output]`` path: the
    pipeline always tiles, so the output shape is deterministic regardless of the
    worker's free RAM (the JARVIS geocell pipeline consumes the tiled folder).
    """
    # Light deps (rasterio/pydantic/tomllib) up front; the torch-heavy classify
    # imports (core, pipeline) are deferred until after the mosaic is built so a
    # bad manifest/mosaic fails fast without paying the model-import cost.
    from backend.app.manifest import load_manifest
    from backend.app.mosaic_catalog import build_catalog, summary_by_priority
    from backend.app.mosaic_builder import build_mosaic

    try:
        manifest = load_manifest(manifest_path)
        geocell = manifest.geocell.to_geocell()   # validates zone-width snap
    except Exception as e:
        print(f"FAIL: invalid manifest {manifest_path}: {e}")
        sys.exit(1)

    bounds = geocell.bounds_wgs84()
    out_path = Path(manifest.output.path)
    print("=" * 70)
    print(f"[cli] geocell manifest run: {geocell.name}")
    print(f"      bounds (W,S,E,N):   {tuple(round(b, 4) for b in bounds)}")
    print(f"      output:             {out_path}")
    _wm = manifest.classify.water_mask
    print(f"      classify:           sam3={manifest.classify.sam3}, "
          f"water_mask={_wm if _wm else '(from shapefile_config.json)'}")
    print("=" * 70)

    if out_path.exists() and not manifest.output.overwrite:
        print(f"FAIL: output already exists (set [output] overwrite = true to replace): {out_path}")
        sys.exit(1)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Build the priority mosaic for the geocell ─────────────────────────────
    try:
        entries = build_catalog(manifest.layers, manifest.sources, bounds)
    except Exception as e:
        print(f"FAIL: catalog build failed: {e}")
        sys.exit(1)
    if not entries:
        print(f"FAIL: no source intersects geocell {geocell.name} {tuple(round(b, 4) for b in bounds)}.\n"
              f"      Check the layer folders/globs and the [geocell] coordinates.")
        sys.exit(1)
    for prio, count, bbox in summary_by_priority(entries):
        print(f"  priority={prio}: {count} file(s), bbox "
              f"(W={bbox[0]:.4f} S={bbox[1]:.4f} E={bbox[2]:.4f} N={bbox[3]:.4f})")

    # PID in the name so concurrent runs (scheduler / CI double-trigger, or a
    # re-run while a prior run is still classifying) can't collide on the temp.
    tmp_mosaic = out_path.parent / f".{geocell.name}_{os.getpid()}_mosaic.tmp.tif"
    try:
        try:
            info = build_mosaic(entries, bounds, tmp_mosaic)
        except Exception as e:
            print(f"FAIL: mosaic build failed: {e}")
            sys.exit(1)
        print(f"[cli] mosaic: {info['n_sources']} source(s) -> "
              f"{info['width']}x{info['height']} px @ {info['gsd_deg']:.3e} deg/px; "
              f"{info['missing_fraction'] * 100:.1f}% of the cell uncovered (black fill)")

        # Classify the mosaic. Same MEA defaults as the positional form, but we
        # FORCE tile mode so the output is always a folder of georeferenced tiles
        # (<cell>_classified_tiles/) — a deterministic shape regardless of free
        # RAM. Downstream (JARVIS geocell) consumes the tiled folder directly.
        from backend.app.core import MEA_CLASSES
        from backend.app.pipeline import classify_v6
        result = classify_v6(
            raster_path=str(tmp_mosaic),
            classes=MEA_CLASSES,
            smoothing="none",
            feature_flags={"spectral": True, "texture": True, "indices": False},
            output_path=str(out_path),
            sam3_enabled=manifest.classify.sam3,
            water_mask=(str(manifest.classify.water_mask)
                        if manifest.classify.water_mask else None),
            single_fused_output=False,          # never collapse to a single file
            tile_mode=True,                     # always tile -> always a folder
            tile_max_pixels=512 ** 2,
            tile_overlap=0,
            tile_output_dir=str(out_path),      # -> <cell>_classified_tiles/ beside [output].path
            tile_workers=max(1, os.cpu_count() or 1),
            detect_shadows=False,
            max_threads=None,
        )
    finally:
        try:
            tmp_mosaic.unlink(missing_ok=True)
        except Exception:
            pass

    if result.get("status") == "ok":
        print(f"OK Saved (tiled folder): {result.get('outputPath') or out_path}")
        sys.exit(0)
    print(f"FAIL: {result.get('message', str(result))}")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description=(
            "Material Classification CLI\n"
            "Classify raster imagery by material classes from the command line.\n\n"
            "Two invocation styles:\n"
            "  Simple:   cli.py <input> <output>\n"
            "            (defaults to --mea --sam3-enabled; reads SDE +\n"
            "            water_mask config from shapefile_config.json.)\n\n"
            "  Detailed: cli.py --input ... --classes ... [other flags]\n\n"
            "For a full guide, parameter docs, the shapefile_config.json schema,\n"
            "and examples: python cli.py --examples"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=True,
    )

    # ── Positional (simple form) ──────────────────────────────────────────────
    parser.add_argument(
        "input_pos",
        nargs="?",
        default=None,
        metavar="INPUT",
        help="Input raster file or folder (positional).\n"
             "When given without --classes/--mea, defaults to --mea --sam3-enabled.\n"
             "Mutually exclusive with --input.",
    )
    parser.add_argument(
        "output_pos",
        nargs="?",
        default=None,
        metavar="OUTPUT",
        help="Output file or folder (positional). Mutually exclusive with --output.",
    )

    # ── Help / Examples ───────────────────────────────────────────────────────
    parser.add_argument(
        "--examples", "-e",
        action="store_true",
        help="Show full guide with parameter explanations and examples.",
    )

    # ── Layered-priority geocell mode (TOML manifest) ─────────────────────────
    parser.add_argument(
        "--manifest",
        default=None,
        metavar="PATH",
        help="Run in layered-priority geocell mode from a TOML manifest.\n"
             "Composites priority layers of orthos into one EPSG:4326 mosaic for\n"
             "the manifest's CDB geocell, then runs the 6-material MEA pipeline.\n"
             "Mutually exclusive with INPUT/OUTPUT/--input/--output/--classes.\n"
             "See --examples for the manifest schema.",
    )

    # ── Required-ish (one of: positional INPUT, --input, or --examples) ───────
    parser.add_argument(
        "--input", "-i",
        default=None,
        metavar="PATH",
           help="Path to raster file or folder.\n"
               "Supported extensions: .tif .tiff .jpg .jpeg\n"
             "If a folder is provided, all supported files are processed recursively (including subfolders).\n"
             "Use either this OR the positional INPUT argument, not both.",
    )
    parser.add_argument(
        "--classes", "-c",
        default=None,
        type=int,
        metavar="N",
           help="Number of material classes (minimum 2).\n"
               "Colors are deterministic by class index.\n"
             "Required for non-MEA runs. Ignored when --mea or the simple\n"
             "positional form is used.",
    )

    # ── Optional ──────────────────────────────────────────────────────────────
    parser.add_argument(
        "--output", "-o",
        default=None,
        metavar="PATH",
           help="Output file or output folder path.\n"
               "Default: next to input with suffix _classified.tif\n"
             "Use either this OR the positional OUTPUT argument, not both.",
    )
    parser.add_argument(
        "--step",
        default="full",
        choices=["step1", "step2", "full"],
           help="Pipeline step:\n"
               "  step1 = classification + export only\n"
               "  step2 = vector rasterization only\n"
               "  full  = full pipeline (default)",
    )
    parser.add_argument(
        "--mode",
        default="regular",
        choices=["regular", "multispectral"],
           help="Imagery mode:\n"
               "  regular       = RGB mode (default)\n"
               "  multispectral = enables spectral indices",
    )
    parser.add_argument(
        "--smoothing",
        default="none",
        choices=SMOOTHING_OPTIONS,
           help="Post-classification smoothing (default: none).\n"
               "Values: none | median_1 | median_2 | median_3 | median_5",
    )
    parser.add_argument(
        "--tile-size",
        type=int,
        default=512,
        metavar="PX",
        help="Tile size in pixels (default: 512).\n"
             "Values: 256 | 512 | 1024 | 2048 | 4096\n"
             "Use -1 for default.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=-1,
        metavar="N",
        help="Number of parallel workers for tile processing.\n"
             "Default: CPU core count. Use -1 for automatic.",
    )
    parser.add_argument(
        "--image-workers",
        type=int,
        default=-1,
        metavar="N",
        help="Number of images to process in parallel in folder mode.\n"
             "Default: min(4, CPU core count). Use -1 for automatic.",
    )
    parser.add_argument(
        "--tiling",
        action="store_true",
        help="Enable tile-based processing for large images.",
    )
    parser.add_argument(
        "--max-threads",
        action="store_true",
        help="Use all available CPU threads.",
    )
    parser.add_argument(
        "--no-spectral",
        action="store_true",
        help="Disable spectral features.",
    )
    parser.add_argument(
        "--no-texture",
        action="store_true",
        help="Disable texture features.",
    )
    parser.add_argument(
        "--indices",
        action="store_true",
        help="Enable spectral indices (e.g. NDVI).\n"
             "Automatically enabled in multispectral mode.",
    )
    parser.add_argument(
        "--detect-shadows",
        action="store_true",
        default=False,
        help="Enable shadow detection, pre-processing balance and class inference (default: off).",
    )
    parser.add_argument(
        "--no-detect-shadows",
        action="store_false",
        dest="detect_shadows",
        help="Disable shadow detection and pre-processing.",
    )
    parser.add_argument(
        "--vector",
        action="append",
        default=[],
        metavar="PATH",
        help="Path to a vector shapefile (.shp) to rasterize onto the result.\n"
             "Can be specified multiple times for multiple layers.",
    )
    parser.add_argument(
        "--mea",
        action="store_true",
        help="Use the 6-material MEA preset and run the SAM3-first\n"
             "classify_v6 pipeline (matches the GUI). Makes --classes optional.\n"
             "Classes: BM_VEGETATION, BM_SAND, BM_SOIL, BM_ASPHALT,\n"
             "         BM_CONCRETE, BM_WATER.",
    )
    parser.add_argument(
        "--no-sam3",
        action="store_false",
        dest="sam3_enabled",
        default=True,
        help="Disable the SAM3 mask stage in --mea mode (KMeans-only fallback).",
    )
    parser.add_argument(
        "--water-mask",
        default=None,
        metavar="PATH",
        dest="water_mask",
        help="Water-mask GeoTIFF (band 1 > 0 = water). Painted directly as\n"
             "BM_WATER. Overrides the water_mask in shapefile_config.json for\n"
             "this run. --mea only.",
    )
    parser.add_argument(
        "--vector-class",
        action="append",
        default=[],
        metavar="CLASS_NAME",
        dest="vector_class",
        help="MEA class name for the nth --vector layer (in order).\n"
             "Example: BM_CONCRETE. Only meaningful with --mea.\n"
             "Valid names (6-material schema):\n"
             "  BM_VEGETATION, BM_SAND, BM_SOIL,\n"
             "  BM_ASPHALT, BM_CONCRETE, BM_WATER",
    )

    args = parser.parse_args()

    # ── Show examples and exit ────────────────────────────────────────────────
    if args.examples:
        print(EXAMPLES_TEXT)
        sys.exit(0)

    # ── Layered-priority geocell mode (TOML manifest) ─────────────────────────
    # Dispatch early: the manifest fully describes the run, so it can't be
    # combined with the positional/flag input forms.
    if args.manifest is not None:
        if any([args.input_pos, args.output_pos, args.input, args.output,
                args.classes is not None]):
            parser.error("--manifest cannot be combined with INPUT/OUTPUT/"
                         "--input/--output/--classes.")
        run_manifest(args.manifest)
        return  # defensive: run_manifest calls sys.exit()

    # ── Reconcile positional vs flag forms ─────────────────────────────────────
    # The simple form is `cli.py <input> <output>`. Positionals live in
    # args.input_pos / args.output_pos; flag form lives in args.input /
    # args.output. Reject mixing the two, then collapse onto args.input /
    # args.output so the rest of the flow stays unchanged.
    if args.input_pos is not None and args.input is not None:
        parser.error("Pass INPUT either positionally or via --input, not both.")
    if args.output_pos is not None and args.output is not None:
        parser.error("Pass OUTPUT either positionally or via --output, not both.")
    used_simple_form = args.input_pos is not None
    if used_simple_form:
        args.input = args.input_pos
        if args.output_pos is not None:
            args.output = args.output_pos

    # Simple form implies MEA (the GUI's default 6-material pipeline).
    # If the user explicitly passed --classes alongside positionals, we
    # respect that and stay in legacy KMeans mode.
    if used_simple_form and not args.mea and args.classes is None:
        args.mea = True
        print("[cli] simple positional form: defaulting to --mea --sam3-enabled "
              "(SDE + water_mask config from shapefile_config.json)")

    # ── Require input + (classes or mea) ───────────────────────────────────────
    if args.input is None:
        parser.error("INPUT is required. Pass it positionally (cli.py <input> <output>)\n"
                     "or via --input. See --examples for the full guide.")
    if args.classes is None and not args.mea:
        parser.error("--classes / -c is required (or use --mea for the 6-material preset).\n"
                     "See --examples for the full guide.")

    # ── Validate tile size ────────────────────────────────────────────────────
    if args.tile_size != -1 and args.tile_size not in TILE_SIZE_OPTIONS:
        print(f"WARNING: tile-size {args.tile_size} is non-standard. "
              f"Recommended: {TILE_SIZE_OPTIONS}")

    # ── Auto-enable indices for multispectral ─────────────────────────────────
    if args.mode == "multispectral":
        args.indices = True

    # ── Build class list ──────────────────────────────────────────────────────
    if args.mea:
        from backend.app.core import MEA_CLASSES
        classes = MEA_CLASSES
        args.classes = len(classes)
    else:
        if args.classes < 2:
            print("ERROR: --classes must be >= 2")
            sys.exit(1)
        classes = build_classes(args.classes)

    if args.step == "step1":
        suffix = "_classified"
    elif args.step == "step2":
        suffix = "_with_vectors"
    else:
        suffix = "_full"

    # ── Run pipeline ──────────────────────────────────────────────────────────
    input_path = Path(args.input)

    if input_path.is_dir():
        # Batch mode — process all supported files recursively
        files = sorted([
            p for p in input_path.rglob("*")
            if p.is_file() and p.suffix.lower() in VALID_EXTENSIONS
        ])
        if not files:
            print(f"No .tif/.tiff/.jpg/.jpeg files found in (recursive): {input_path}")
            sys.exit(1)

        image_workers = args.image_workers if args.image_workers != -1 else min(4, max(1, os.cpu_count() or 1))
        image_workers = max(1, int(image_workers))
        print(f"Found {len(files)} file(s) to process. image_workers={image_workers}")

        # Folder batch classifies each tile INDEPENDENTLY (per-tile KMeans),
        # exactly like the proven single-file path. We deliberately do NOT train
        # one shared folder-pooled KMeans: pooling biased the 3 cluster centroids
        # toward the folder's sand/soil-dominant colors (starving vegetation of a
        # centroid -> folder-wide veg collapse) and was engine-nondeterministic
        # (faiss-GPU vs sklearn-CPU centroids landed mid-tan pixels on opposite
        # sides of the SAND/SOIL brightness gate -> per-machine SAND<->SOIL flips).
        # Per-tile centroids represent each tile's own colors, so every tile
        # matches its single-file output. The pretrained_* args below stay None.
        shared_scaler = None
        shared_kmeans = None
        shared_color_table = None
        shared_mea_mapping = None

        def _process_file(file_path: Path):
            out_path = derive_output(file_path, args.output, suffix, input_root=input_path)
            try:
                result = run_single(
                    str(file_path),
                    out_path,
                    args,
                    classes,
                    pretrained_scaler=shared_scaler,
                    pretrained_kmeans=shared_kmeans,
                    pretrained_color_table=shared_color_table,
                    pretrained_mea_mapping=shared_mea_mapping,
                )
            except Exception:
                # Capture the real traceback so a per-file failure is diagnosable
                # instead of being lost as a one-line message (the cause of the
                # silent "17 tiles produced no output" in earlier batch runs).
                import traceback as _tb
                return ("error", str(file_path), _tb.format_exc())
            if result.get("status") == "ok":
                return ("ok", str(file_path), result.get("outputPath") or out_path)
            return ("error", str(file_path), result.get("message", str(result)))

        saved, errors = [], []
        with ThreadPoolExecutor(max_workers=image_workers) as executor:
            futures = {executor.submit(_process_file, file_path): file_path for file_path in files}
            for idx, future in enumerate(as_completed(futures), 1):
                src = futures[future]
                relative_display = src.relative_to(input_path)
                try:
                    item = future.result()
                    if item[0] == "ok":
                        saved.append(item[2])
                        print(f"[{idx}/{len(files)}] OK {relative_display} -> {item[2]}")
                    else:
                        errors.append((item[1], item[2]))
                        print(f"[{idx}/{len(files)}] FAIL {relative_display} : {item[2]}")
                except Exception as e:
                    errors.append((str(src), str(e)))
                    print(f"[{idx}/{len(files)}] FAIL {relative_display} : {e}")

        print()
        print(f"Done. {len(saved)}/{len(files)} files processed successfully.")
        if errors:
            print(f"{len(errors)} error(s):")
            for path, msg in errors:
                # msg may be a full traceback; print the first line inline and
                # write the complete detail to a log file next to the outputs.
                first_line = str(msg).strip().splitlines()[-1] if str(msg).strip() else str(msg)
                print(f"  - {path}: {first_line}")
            _log_dir = Path(args.output) if args.output and Path(args.output).is_dir() else Path.cwd()
            _fail_log = _log_dir / "batch_failures.log"
            try:
                with open(_fail_log, "w", encoding="utf-8") as _fh:
                    for path, msg in errors:
                        _fh.write(f"===== {path} =====\n{msg}\n\n")
                print(f"Full failure tracebacks written to: {_fail_log}")
            except Exception as _log_err:
                print(f"(could not write failure log: {_log_err})")
        sys.exit(0 if not errors else 1)

    elif input_path.is_file():
        out = derive_output(input_path, args.output, suffix)
        print(f"Processing: {input_path.name} -> {out}")
        try:
            result = run_single(str(input_path), out, args, classes)
            if result.get("status") == "ok":
                print(f"OK Saved: {result.get('outputPath') or out}")
                sys.exit(0)
            else:
                print(f"FAIL: {result.get('message', str(result))}")
                sys.exit(1)
        except Exception as e:
            print(f"FAIL Exception: {e}")
            sys.exit(1)
    else:
        print(f"ERROR: Input path does not exist: {input_path}")
        sys.exit(1)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
