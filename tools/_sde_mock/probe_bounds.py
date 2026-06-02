"""Probe the ortho folder: gather union bounds in raster CRS + WGS84 bbox."""
import os, sys, json
import rasterio
from rasterio.warp import transform_bounds

DATA = r"C:\Users\B\Desktop\gs\IER\samples\RadiometricOrtho\data"

tifs = sorted(os.path.join(DATA, f) for f in os.listdir(DATA) if f.lower().endswith(".tif"))
print(f"Found {len(tifs)} rasters")

crs = None
minx = miny = float("inf")
maxx = maxy = float("-inf")
for p in tifs:
    with rasterio.open(p) as s:
        if crs is None:
            crs = s.crs
        b = s.bounds
        minx = min(minx, b.left)
        miny = min(miny, b.bottom)
        maxx = max(maxx, b.right)
        maxy = max(maxy, b.top)

print("CRS:", crs)
print("EPSG:", crs.to_epsg() if crs else None)
print("Bounds (native):", (minx, miny, maxx, maxy))
print("Width x Height (units):", maxx - minx, maxy - miny)

if crs is not None:
    wgs = transform_bounds(crs, "EPSG:4326", minx, miny, maxx, maxy, densify_pts=21)
    print("WGS84 (W, S, E, N):", wgs)
    out = {
        "crs_wkt": crs.to_wkt(),
        "epsg": crs.to_epsg(),
        "bounds_native": [minx, miny, maxx, maxy],
        "bounds_wgs84": list(wgs),
        "n_rasters": len(tifs),
        "first": tifs[0] if tifs else None,
    }
    with open(os.path.join(os.path.dirname(__file__), "ortho_bounds.json"), "w") as f:
        json.dump(out, f, indent=2)
    print("Wrote ortho_bounds.json")
