"""Drop-in replacement for backend/app/sde_arcpy_worker.py — no arcpy.

Speaks the same JSON-over-stdio protocol as the real worker so the
orchestrator (`sde_extractor.extract_to_shapefiles`) does not know the
difference. Backed by local GeoPackages instead of an Esri SDE.

Stdin spec (identical to the real worker)::

    {
      "sde_connection": "<path to a dir of .gpkg files OR a single .gpkg>",
      "layer":          "buildings" | "roads" | "water",
      "out_dir":        "<path>",
      "sr_wkid":        <int|null>,  # raster CRS EPSG
      "tiles": [{"id": 0, "wkt": "POLYGON((...))"}]
    }

Connection resolution:
  - If `sde_connection` is a directory: look for `<layer>.gpkg` inside.
  - If `sde_connection` is a `.gpkg`: open it directly; `layer` is the
    GPKG layer name to query (we still accept buildings/roads/water as
    shortcuts).

Per tile we:
  1. Parse the WKT polygon in the raster's CRS (sr_wkid).
  2. Reproject it to the data's CRS for the spatial query.
  3. pyogrio bbox-read the matching features.
  4. Reproject features to the raster's CRS (so the downstream
     orthowise envelope intersection works on like CRS).
  5. Write `<out_dir>/tile_<id>.shp` if any features matched.

Errors per tile are caught and returned as `{ok: false, error: ...}`;
fatal errors (cannot open the data file at all) emit a `fatal` block.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path


def _emit_fatal(message: str, trace: str) -> None:
    json.dump({"results": [], "fatal": {"error": message, "trace": trace}}, sys.stdout)


def _resolve_layer_source(sde_connection: str, layer: str) -> tuple[str, str | None]:
    """Return (data_path, gpkg_layer_or_none) for the requested layer.

    - Directory: looks for `<layer>.gpkg` first, falls back to .shp.
    - .gpkg file: returns the file; layer name is the GPKG sublayer
      (default = the bare layer name).
    """
    p = Path(sde_connection)
    if p.is_dir():
        for ext in (".gpkg", ".shp"):
            candidate = p / f"{layer}{ext}"
            if candidate.exists():
                return str(candidate), (layer if ext == ".gpkg" else None)
        raise FileNotFoundError(f"No {layer}.gpkg or {layer}.shp in {p}")
    if p.is_file() and p.suffix.lower() == ".gpkg":
        return str(p), layer
    if p.is_file() and p.suffix.lower() == ".shp":
        return str(p), None
    raise FileNotFoundError(f"sde_connection not a recognised data source: {p}")


def main() -> int:
    try:
        spec = json.loads(sys.stdin.read())
    except Exception as exc:
        _emit_fatal(f"could not parse stdin spec: {exc}", traceback.format_exc())
        return 1

    try:
        import geopandas as gpd
        import pyogrio
        from shapely import wkt as shp_wkt
        from shapely.geometry import box
        from pyproj import CRS, Transformer
    except Exception as exc:
        _emit_fatal(f"geo deps import failed: {exc}", traceback.format_exc())
        return 1

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
        data_path, gpkg_layer = _resolve_layer_source(sde_connection, layer_name)
    except Exception as exc:
        _emit_fatal(f"cannot resolve layer source: {exc}", traceback.format_exc())
        return 1

    # Source CRS — peek at the file header to avoid a full read.
    try:
        info_kwargs = {"layer": gpkg_layer} if gpkg_layer else {}
        info = pyogrio.read_info(data_path, **info_kwargs)
        src_crs = info.get("crs")
        if src_crs:
            src_crs_obj = CRS.from_user_input(src_crs)
        else:
            src_crs_obj = None
    except Exception as exc:
        _emit_fatal(f"cannot read header of {data_path}: {exc}", traceback.format_exc())
        return 1

    if sr_wkid:
        try:
            raster_crs_obj = CRS.from_epsg(int(sr_wkid))
        except Exception:
            raster_crs_obj = None
    else:
        raster_crs_obj = None

    # Tile WKTs come in the raster CRS — reproject them to the data CRS
    # for the bbox query. Build the transformer once.
    if src_crs_obj and raster_crs_obj and src_crs_obj != raster_crs_obj:
        to_src = Transformer.from_crs(raster_crs_obj, src_crs_obj, always_xy=True).transform
        to_raster = Transformer.from_crs(src_crs_obj, raster_crs_obj, always_xy=True).transform
    else:
        to_src = None
        to_raster = None

    results = []
    for t in tiles:
        tile_id = int(t["id"])
        out_shp = os.path.join(out_dir, f"tile_{tile_id}.shp")
        try:
            tile_geom_raster_crs = shp_wkt.loads(t["wkt"])
            # Spatial query bbox in the data's CRS:
            if to_src is not None:
                from shapely.ops import transform as shp_transform
                tile_in_src = shp_transform(to_src, tile_geom_raster_crs)
            else:
                tile_in_src = tile_geom_raster_crs
            bbox = tile_in_src.bounds  # (minx, miny, maxx, maxy)

            read_kwargs = {"bbox": bbox}
            if gpkg_layer:
                read_kwargs["layer"] = gpkg_layer

            gdf = pyogrio.read_dataframe(data_path, **read_kwargs)
            count = len(gdf)
            if count == 0:
                results.append({"id": tile_id, "shp": None, "count": 0, "ok": True})
                continue

            # The real arcpy worker hands back data in the SDE layer's
            # native CRS and the resolver reprojects downstream. To stay
            # protocol-compatible, leave the data in src_crs and let the
            # resolver reproject. But ensure CRS is set on the gdf.
            if gdf.crs is None and src_crs_obj is not None:
                gdf = gdf.set_crs(src_crs_obj)

            gdf.to_file(out_shp, driver="ESRI Shapefile")
            results.append({"id": tile_id, "shp": out_shp, "count": int(count), "ok": True})
        except Exception as exc:
            results.append({
                "id": tile_id, "shp": None, "ok": False,
                "error": str(exc), "trace": traceback.format_exc(),
            })

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
