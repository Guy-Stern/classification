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

        shared_scaler = None
        shared_kmeans = None
        shared_color_table = None
        shared_mea_mapping = None

        # For folder batch in step1/full, train ONE shared model and ONE shared
        # color table for full cross-image consistency (same cluster->material mapping).
        if args.step in {"step1", "full"}:
            try:
                from backend.app.core import train_kmeans_model, build_shared_color_table
                feature_flags = {
                    "spectral": not args.no_spectral,
                    "texture": not args.no_texture,
                    "indices": args.indices,
                }
                # MEA mode: train on the 3 kmeans-source materials only
                # (BM_VEGETATION/BM_SAND/BM_SOIL). BM_ASPHALT/BM_CONCRETE/
                # BM_WATER are mask-source — classify_v6 paints them per
                # raster from SAM3 / shapefiles. Mirrors /classify-batch.
                if args.mea:
                    from backend.app.pipeline import _split_classes_by_source
                    train_classes, _ = _split_classes_by_source(classes)
                    print(f"[Batch] MEA mode: shared model trained on "
                          f"{len(train_classes)}/{len(classes)} kmeans-source "
                          f"classes ({[c['name'] for c in train_classes]}); "
                          f"BM_ASPHALT/BM_CONCRETE/BM_WATER painted per-raster.")
                else:
                    train_classes = classes

                print(f"[Batch] Training shared model on {len(files)} raster(s)...")
                shared_scaler, shared_kmeans = train_kmeans_model(
                    [str(p) for p in files],
                    train_classes,
                    feature_flags,
                    detect_shadows=args.detect_shadows,
                )
                print("[Batch] Building shared color table...")
                shared_mea_mapping, shared_color_table = build_shared_color_table(
                    [str(p) for p in files],
                    shared_scaler,
                    shared_kmeans,
                    train_classes,
                    feature_flags,
                )
                print("[Batch] Shared model ready; applying to all files.")
            except Exception as e:
                print(f"[Batch][warn] Shared-model training failed, using per-image fallback: {e}")
                shared_scaler = shared_kmeans = shared_color_table = shared_mea_mapping = None

        def _process_file(file_path: Path):
            out_path = derive_output(file_path, args.output, suffix, input_root=input_path)
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
                print(f"  - {path}: {msg}")
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
