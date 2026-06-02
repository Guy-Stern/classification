"""Smoke test: invoke sde_extractor against the mock worker end-to-end.

Picks one raster from the ortho folder, calls extract_to_shapefiles for
each feature type, verifies we get back per-tile shapefile paths with
features. No classification runs — this is purely the SDE plumbing.

Requires MC_SDE_WORKER_SCRIPT env var pointing at mock_worker.py.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
PROJECT_ROOT = HERE.parent.parent

# Make sure the project root is importable so we can pick up backend/...
sys.path.insert(0, str(PROJECT_ROOT))

# Point sde_extractor at the mock worker before importing it.
os.environ["MC_SDE_WORKER_SCRIPT"] = str(HERE / "mock_worker.py")

import rasterio
from backend.app import sde_extractor, shapefile_config

DATA = r"C:\Users\B\Desktop\gs\IER\samples\RadiometricOrtho\data"


def main():
    # Sanity-print the loaded SDE config so we know what we're hitting.
    sde_cfg = shapefile_config.get_sde()
    print("SDE config:")
    for k, v in sde_cfg.items():
        print(f"  {k}: {v}")
    print()

    tifs = sorted(p for p in Path(DATA).iterdir() if p.suffix.lower() == ".tif")
    print(f"{len(tifs)} rasters in {DATA}")
    if not tifs:
        sys.exit("no rasters found")

    raster = tifs[0]
    with rasterio.open(str(raster)) as src:
        crs = src.crs
        bounds = src.bounds
    wkid = crs.to_epsg() if crs else None
    print(f"Test raster: {raster.name}  CRS=EPSG:{wkid}  bounds={bounds}")

    # tiny buffer matching the resolver's _BOUNDS_BUFFER_METRES (50 m)
    # in geographic units (50/111320 ≈ 4.49e-4 deg)
    buf = 50.0 / 111_320.0 if crs and crs.is_geographic else 50.0
    bounds_buf = (bounds.left - buf, bounds.bottom - buf, bounds.right + buf, bounds.top + buf)
    print(f"Buffered bounds: {bounds_buf}")

    for feature_type in ("buildings", "roads", "water"):
        print(f"\n--- {feature_type} ---")
        shps = sde_extractor.extract_to_shapefiles(
            feature_type, bounds_buf, wkid, raster.stem,
        )
        print(f"  -> {len(shps)} tile shapefile(s) produced")
        for s in shps:
            print(f"     {s}  exists={Path(s).exists()}  size={Path(s).stat().st_size if Path(s).exists() else 'n/a'}")


if __name__ == "__main__":
    main()
