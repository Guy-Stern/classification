"""Standalone SDE connection test for the (air-gapped) install.

Reads the installed shapefile_config.json -- the SAME file the app uses
-- then exercises the SDE path in isolation (no classification) and
writes a full log you can hand back for diagnosis.

  Stage 0  preflight: config + paths exist, no SET_ME_ placeholders left.
  Stage 1  arcpy probe: launch sde_probe_worker.py under the configured
           ArcGIS Pro python; per layer report whether arcpy loaded, the
           .sde workspace opened, the layer exists, its feature count,
           CRS and extent. This is the core connection test.
  Stage 2  (only with --raster) real extraction: run the production
           worker via sde_extractor.extract_to_shapefiles over the
           raster's footprint -- proves the full path (tiling ->
           SelectLayerByLocation -> CopyFeatures -> shapefiles).

Run it on the target with the app's venv, from the install root:

    .venv\\Scripts\\python.exe sde_conn_test.py
    .venv\\Scripts\\python.exe sde_conn_test.py --raster C:\\path\\to\\ortho.tif

A log is written next to this script as sde_conn_test_<timestamp>.log.
Send that .log back for diagnosis.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


class _Tee:
    """Duplicate writes to several streams; never let one stream's
    failure (e.g. a console that can't encode a char) abort the others."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
            except Exception:
                pass

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str = "") -> None:
    print(f"[{_ts()}] {msg}")


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Test the SDE connection configured in shapefile_config.json"
    )
    ap.add_argument(
        "--install-dir", default=None,
        help="Install root (folder containing backend\\ and shapefile_config.json). "
             "Default: this script's folder.",
    )
    ap.add_argument(
        "--raster", default=None,
        help="Optional ortho .tif. If given, also runs a REAL extraction over its "
             "footprint (Stage 2) to test the full production path.",
    )
    ap.add_argument("--no-extract", action="store_true",
                    help="Skip Stage 2 even if --raster is given.")
    ap.add_argument("--timeout", type=int, default=300,
                    help="Per-subprocess timeout in seconds (default 300).")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent
    install_dir = Path(args.install_dir).resolve() if args.install_dir else here

    log_path = here / f"sde_conn_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logf = open(log_path, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, logf)
    sys.stderr = _Tee(sys.__stderr__, logf)

    section("SDE CONNECTION TEST")
    log(f"log file    : {log_path}")
    log(f"install dir : {install_dir}")
    log(f"this python : {sys.executable}")

    # Import the installed backend so we read the EXACT config the app uses
    # and drive the EXACT extraction code the pipeline uses.
    sys.path.insert(0, str(install_dir))
    try:
        from backend.app import shapefile_config, sde_extractor
    except Exception as exc:
        log(f"FAIL: cannot import backend from {install_dir} ({exc})")
        log("      Run this from the install root, or pass --install-dir <root>.")
        traceback.print_exc()
        logf.flush()
        return 2

    log(f"config file : {shapefile_config.config_path()}")
    sde = shapefile_config.get_sde()

    # ---- Stage 0: preflight -------------------------------------------------
    section("STAGE 0 - config + preflight")
    for k in ("enabled", "connection_file", "arcpy_python", "tile_size_metres", "timeout_seconds"):
        log(f"  sde.{k} = {sde.get(k)!r}")
    log(f"  sde.layers = {sde.get('layers')!r}")

    if not sde.get("enabled"):
        log("  WARN: sde.enabled is false -- the app would skip SDE. (Test continues anyway.)")

    conn = sde.get("connection_file") or ""
    arcpy_py = sde.get("arcpy_python") or ""
    configured_layers = {k: v for k, v in (sde.get("layers") or {}).items() if v}

    def check(label: str, cond: bool, detail: str = "") -> bool:
        log(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
        return cond

    check("connection_file set", bool(conn), conn or "(empty)")
    conn_exists = bool(conn) and Path(conn).exists()
    check("connection_file exists on disk", conn_exists, conn)
    check("connection_file is not a placeholder", "SET_ME" not in conn,
          "still has the SET_ME_ placeholder - edit shapefile_config.json" if "SET_ME" in conn else "")
    check("arcpy_python set", bool(arcpy_py), arcpy_py or "(empty)")
    arcpy_exists = bool(arcpy_py) and Path(arcpy_py).exists()
    check("arcpy_python exists on disk", arcpy_exists, arcpy_py)
    check("at least one layer configured", bool(configured_layers),
          ", ".join(configured_layers) if configured_layers else "none")
    placeholder_layers = [k for k, v in configured_layers.items() if "SET_ME" in v]
    check("layers are not placeholders", not placeholder_layers,
          ("still placeholders: " + ", ".join(placeholder_layers)) if placeholder_layers else "")

    real_layers = {k: v for k, v in configured_layers.items() if "SET_ME" not in v}
    if not (conn_exists and arcpy_exists and real_layers):
        section("RESULT")
        log("STOPPED at preflight. Fix the FAIL items above in:")
        log(f"  {shapefile_config.config_path()}")
        log("Then re-run this script.")
        log(f"Full log: {log_path}")
        logf.flush()
        return 1

    # ---- Stage 1: arcpy probe ----------------------------------------------
    section("STAGE 1 - arcpy probe (open .sde + per-layer count / CRS / extent)")
    probe = Path(sde_extractor.__file__).resolve().parent / "sde_probe_worker.py"
    log(f"  probe worker : {probe}  exists={probe.exists()}")
    if not probe.exists():
        log("  FAIL: sde_probe_worker.py is missing next to sde_extractor.py.")
        section("RESULT")
        log(f"Full log: {log_path}")
        logf.flush()
        return 1

    log(f"  launching    : {arcpy_py} {probe.name}")
    spec = {"sde_connection": conn, "layers": sde.get("layers") or {}}
    try:
        proc = subprocess.run(
            [arcpy_py, str(probe)], input=json.dumps(spec),
            capture_output=True, text=True, timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        log(f"  FAIL: probe timed out after {args.timeout}s (workspace connect can be slow; try --timeout).")
        section("RESULT")
        log(f"Full log: {log_path}")
        logf.flush()
        return 1
    except Exception as exc:
        log(f"  FAIL: could not launch probe: {exc}")
        traceback.print_exc()
        section("RESULT")
        log(f"Full log: {log_path}")
        logf.flush()
        return 1

    log(f"  probe exit={proc.returncode}  stdout_len={len(proc.stdout)}  stderr_len={len(proc.stderr)}")
    if proc.stderr.strip():
        log("  probe stderr:")
        print(proc.stderr)

    try:
        result = json.loads(proc.stdout)
    except Exception as exc:
        log(f"  FAIL: cannot parse probe output ({exc}). Raw stdout follows:")
        print(proc.stdout or "(empty)")
        section("RESULT")
        log(f"Full log: {log_path}")
        logf.flush()
        return 1

    if result.get("fatal"):
        f = result["fatal"]
        log(f"  FAIL (fatal): {f.get('error')}")
        log(f"  trace:\n{f.get('trace', '(none)')}")
        section("RESULT")
        log("Connection / arcpy failed. Common causes:")
        log("  - arcpy_python is not really ArcGIS Pro's python (no arcpy)")
        log("  - the .sde file is wrong, or the database is unreachable / credentials expired")
        log(f"Full log: {log_path}")
        logf.flush()
        return 1

    log(f"  arcpy version: {result.get('arcpy_version')}")
    layer_results = result.get("layers", {})
    stage1_ok = bool(layer_results)
    for ft, rec in layer_results.items():
        if rec.get("ok"):
            log(f"  [PASS] {ft}: layer={rec.get('layer')!r} count={rec.get('count')} "
                f"shape={rec.get('shape_type')} wkid={rec.get('wkid')} crs={rec.get('crs_name')!r}")
            log(f"         extent={rec.get('extent')}")
        else:
            stage1_ok = False
            log(f"  [FAIL] {ft}: layer={rec.get('layer')!r} - {rec.get('error')}")
            if rec.get("trace"):
                log(f"         trace:\n{rec['trace']}")

    # ---- Stage 2: real extraction (optional) -------------------------------
    if args.raster and not args.no_extract:
        section("STAGE 2 - real extraction over the raster footprint")
        rpath = Path(args.raster)
        if not rpath.exists():
            log(f"  SKIP: raster not found: {rpath}")
        else:
            try:
                import rasterio
                with rasterio.open(str(rpath)) as src:
                    b = src.bounds
                    wkid = src.crs.to_epsg() if src.crs else None
                bounds = (b.left, b.bottom, b.right, b.top)
                log(f"  raster={rpath.name}  wkid={wkid}  bounds={bounds}")
                for ft, lyr in (sde.get("layers") or {}).items():
                    if not lyr or "SET_ME" in lyr:
                        continue
                    log(f"  --- extracting {ft} ({lyr}) ---")
                    shps = sde_extractor.extract_to_shapefiles(ft, bounds, wkid, rpath.stem)
                    log(f"  {ft}: {len(shps)} tile shapefile(s) produced")
                    for s in shps:
                        log(f"     {s}")
            except Exception as exc:
                log(f"  FAIL: Stage 2 errored: {exc}")
                traceback.print_exc()
    elif not args.raster:
        section("STAGE 2 - skipped")
        log("  Pass --raster <ortho.tif> to also test the full extraction path "
            "(tiling + feature selection + shapefile output).")

    # ---- Result -------------------------------------------------------------
    section("RESULT")
    log(f"  stage 1 (connection + layers): {'PASS' if stage1_ok else 'FAIL - see above'}")
    log(f"Full log written to:\n  {log_path}")
    log("Send that .log file back for diagnosis.")
    logf.flush()
    return 0 if stage1_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
