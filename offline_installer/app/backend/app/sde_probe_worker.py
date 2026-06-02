"""ArcPy probe worker for the SDE connection test (diagnostic only).

Runs under ArcGIS Pro's bundled Python (which has arcpy). It is *not*
imported by the app. Reads a JSON spec from stdin, opens the configured
SDE workspace, and reports per-layer existence / feature count / extent
/ spatial reference. Writes a single JSON result to stdout.

Mirrors sde_arcpy_worker.py's JSON-over-stdio style so the orchestrator
(sde_conn_test.py) handles both the same way. This is connection
diagnostics only: it does NOT select/copy features (that path is tested
by Stage 2 of sde_conn_test.py, which drives the real worker).

Stdin:
    {"sde_connection": "<.sde path>", "layers": {"buildings": "...", ...}}

Stdout:
    {"arcpy_version": "3.x",
     "layers": {"buildings": {"ok": true, "count": 123,
                              "extent": [xmin, ymin, xmax, ymax],
                              "wkid": 32636, "crs_name": "...",
                              "shape_type": "Polygon"}, ...},
     "fatal": null}
"""
from __future__ import annotations

import json
import sys
import traceback


def _emit_fatal(message: str, trace: str) -> None:
    json.dump({"layers": {}, "fatal": {"error": message, "trace": trace}}, sys.stdout)


def main() -> int:
    try:
        spec = json.loads(sys.stdin.read())
    except Exception as exc:
        _emit_fatal(f"could not parse stdin spec: {exc}", traceback.format_exc())
        return 1

    try:
        import arcpy  # must come after stdin parse so import errors are JSON
    except Exception as exc:
        _emit_fatal(f"arcpy import failed: {exc}", traceback.format_exc())
        return 1

    connection = spec.get("sde_connection")
    layers = spec.get("layers") or {}
    if not connection:
        _emit_fatal("spec missing sde_connection", "")
        return 1

    out = {"arcpy_version": getattr(arcpy, "__version__", "?"), "layers": {}, "fatal": None}

    try:
        arcpy.env.workspace = connection
    except Exception as exc:
        _emit_fatal(f"cannot open workspace {connection!r}: {exc}", traceback.format_exc())
        return 1

    for feature_type, layer_name in layers.items():
        rec = {"layer": layer_name, "ok": False}
        if not layer_name:
            rec["error"] = "no layer configured for this feature type"
            out["layers"][feature_type] = rec
            continue
        try:
            if not arcpy.Exists(layer_name):
                rec["error"] = "layer does not exist in the workspace"
                out["layers"][feature_type] = rec
                continue
            desc = arcpy.Describe(layer_name)
            rec["shape_type"] = getattr(desc, "shapeType", "?")
            sr = getattr(desc, "spatialReference", None)
            if sr is not None:
                rec["wkid"] = getattr(sr, "factoryCode", None)
                rec["crs_name"] = getattr(sr, "name", None)
            ext = getattr(desc, "extent", None)
            if ext is not None:
                rec["extent"] = [ext.XMin, ext.YMin, ext.XMax, ext.YMax]
            rec["count"] = int(arcpy.management.GetCount(layer_name).getOutput(0))
            rec["ok"] = True
        except Exception as exc:
            rec["error"] = str(exc)
            rec["trace"] = traceback.format_exc()
        out["layers"][feature_type] = rec

    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _exc:
        _emit_fatal(f"unhandled exception: {_exc}", traceback.format_exc())
        sys.exit(1)
