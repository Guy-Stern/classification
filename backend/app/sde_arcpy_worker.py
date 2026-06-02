"""ArcPy subprocess worker for SDE feature extraction (Path B stopgap).

This module is **invoked as a script under ArcGIS Pro's bundled Python**
(which has ``arcpy``). It is *not* imported by the main app — our regular
venv has no arcpy.

Protocol
--------
Reads a single JSON spec from stdin::

    {
      "sde_connection": r"C:\\path\\to\\foo.sde",
      "layer":          "MORIA_GDB.B_BUILDINGS_A",
      "out_dir":        r"C:\\Temp\\sde_extract_XXXX",
      "sr_wkid":        32636,                       # raster CRS WKID (int) or None
      "tiles": [
        {"id": 0, "wkt": "POLYGON((...))"},
        ...
      ]
    }

Writes a single JSON result to stdout::

    {
      "results": [
        {"id": 0, "shp": "C:/Temp/.../tile_0.shp", "count": 1234, "ok": true},
        {"id": 1, "shp": null, "count": 0, "ok": true},
        {"id": 2, "shp": null, "ok": false, "error": "...", "trace": "..."}
      ],
      "fatal": null
    }

Each tile is processed independently — one bad tile does not abort the
batch. A ``fatal`` entry is only set on failures that prevent any tile
from running (e.g. cannot open the SDE workspace, target layer missing).

Tile geometries are tagged with the raster's spatial reference; arcpy
projects them on-the-fly to the SDE layer's CRS during SelectByLocation,
so the output shapefiles end up in the SDE layer's native CRS. The
orchestrator's downstream reprojection (in shapefile_resolver) handles
the final hop to raster CRS.
"""
from __future__ import annotations

import json
import os
import sys
import traceback


def _emit_fatal(message: str, trace: str) -> None:
    json.dump(
        {"results": [], "fatal": {"error": message, "trace": trace}},
        sys.stdout,
    )


def main() -> int:
    try:
        spec = json.loads(sys.stdin.read())
    except Exception as exc:
        _emit_fatal(f"could not parse stdin spec: {exc}", traceback.format_exc())
        return 1

    try:
        import arcpy  # noqa: E402  (must come after stdin parse so errors are JSON)
    except Exception as exc:
        _emit_fatal(f"arcpy import failed: {exc}", traceback.format_exc())
        return 1

    arcpy.env.overwriteOutput = True

    sde_connection = spec.get("sde_connection")
    layer_name = spec.get("layer")
    out_dir = spec.get("out_dir")
    sr_wkid = spec.get("sr_wkid")
    tiles = spec.get("tiles") or []

    if not (sde_connection and layer_name and out_dir):
        _emit_fatal("spec missing sde_connection / layer / out_dir", "")
        return 1

    os.makedirs(out_dir, exist_ok=True)

    try:
        arcpy.env.workspace = sde_connection
    except Exception as exc:
        _emit_fatal(f"cannot set workspace {sde_connection!r}: {exc}", traceback.format_exc())
        return 1

    try:
        sr = arcpy.SpatialReference(sr_wkid) if sr_wkid else None
    except Exception:
        sr = None

    target_layer = "sde_extract_target"
    try:
        arcpy.management.MakeFeatureLayer(layer_name, target_layer)
    except Exception as exc:
        _emit_fatal(
            f"MakeFeatureLayer failed for {layer_name!r}: {exc}",
            traceback.format_exc(),
        )
        return 1

    # All tile rectangles go into one in-memory FC; per-tile selection
    # uses a where-clause to pick a single row. Cheaper than creating an
    # FC per tile and matches arcpy's expected SelectByLocation input
    # (a layer, not a raw Geometry — robust across arcpy versions).
    tiles_fc_ws = "memory"
    tiles_fc_name = "sde_extract_tiles"
    tiles_fc = f"{tiles_fc_ws}/{tiles_fc_name}"
    try:
        if arcpy.Exists(tiles_fc):
            arcpy.management.Delete(tiles_fc)
        arcpy.management.CreateFeatureclass(
            tiles_fc_ws, tiles_fc_name, "POLYGON", spatial_reference=sr,
        )
        arcpy.management.AddField(tiles_fc, "TILE_ID", "LONG")
        with arcpy.da.InsertCursor(tiles_fc, ["TILE_ID", "SHAPE@"]) as cur:
            for t in tiles:
                try:
                    geom = arcpy.FromWKT(t["wkt"], sr) if sr else arcpy.FromWKT(t["wkt"])
                    cur.insertRow([int(t["id"]), geom])
                except Exception as exc:
                    sys.stderr.write(
                        f"[sde_arcpy_worker] skip tile {t.get('id')} (bad WKT): {exc}\n"
                    )
    except Exception as exc:
        _emit_fatal(
            f"could not build tiles feature class: {exc}",
            traceback.format_exc(),
        )
        return 1

    results = []
    for t in tiles:
        tile_id = int(t["id"])
        out_shp = os.path.join(out_dir, f"tile_{tile_id}.shp")
        tile_lyr = f"tile_lyr_{tile_id}"
        try:
            arcpy.management.MakeFeatureLayer(
                tiles_fc, tile_lyr, f"TILE_ID = {tile_id}",
            )
            arcpy.management.SelectLayerByLocation(
                target_layer, "INTERSECT", tile_lyr, "", "NEW_SELECTION",
            )
            count = int(arcpy.management.GetCount(target_layer).getOutput(0))
            if count == 0:
                results.append({"id": tile_id, "shp": None, "count": 0, "ok": True})
                continue
            arcpy.management.CopyFeatures(target_layer, out_shp)
            results.append({"id": tile_id, "shp": out_shp, "count": count, "ok": True})
        except Exception as exc:
            results.append({
                "id": tile_id, "shp": None, "ok": False,
                "error": str(exc), "trace": traceback.format_exc(),
            })
        finally:
            try:
                arcpy.management.Delete(tile_lyr)
            except Exception:
                pass

    json.dump({"results": results, "fatal": None}, sys.stdout)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _exc:
        _emit_fatal(f"unhandled exception: {_exc}", traceback.format_exc())
        sys.exit(1)
