"""Download buildings/roads/water from OSM Overpass for the ortho extent.

Saves three GeoPackages under ./osm_data/: buildings.gpkg, roads.gpkg,
water.gpkg. These are what the mock SDE worker will spatially query per
tile.

Buildings are downloaded in a grid of sub-bboxes (Overpass times out on
single huge queries). Roads and water are smaller, single queries with
extended timeout. Skips layers already on disk so reruns are fast.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon, LineString, Point, MultiPolygon
from shapely.ops import unary_union

HERE = Path(__file__).parent
BOUNDS_FILE = HERE / "ortho_bounds.json"
OUT_DIR = HERE / "osm_data"
OUT_DIR.mkdir(exist_ok=True)

OVERPASS = "https://overpass-api.de/api/interpreter"
# Mirror in case the main is rate-limited:
OVERPASS_MIRROR = "https://overpass.kumi.systems/api/interpreter"

# Buildings split into N x N sub-bboxes (Overpass refuses huge queries).
BUILD_GRID = 4
TIMEOUT_S_QUERY = 600    # what we ask Overpass for
TIMEOUT_S_HTTP = 900     # what we wait on the socket


def _post(query: str, retries: int = 3) -> bytes:
    """POST an Overpass QL query, return the response bytes. Tries mirror on rate-limit.

    Overpass expects the query as the form-encoded ``data=`` body with a
    ``application/x-www-form-urlencoded`` Content-Type. A raw text/plain
    body gets a 406 from some mirrors.
    """
    import urllib.parse as _up
    body = _up.urlencode({"data": query}).encode("utf-8")
    last_exc = None
    for url in (OVERPASS, OVERPASS_MIRROR):
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": "MaterialClassification-SDE-Mock/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=TIMEOUT_S_HTTP) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                last_exc = e
                # 429 (too many requests) or 504 → back off + retry
                if e.code in (429, 504):
                    wait = 15 * (attempt + 1)
                    print(f"  HTTP {e.code} from {url}, retry {attempt+1}/{retries} in {wait}s")
                    time.sleep(wait)
                    continue
                else:
                    raise
            except Exception as e:
                last_exc = e
                print(f"  network error from {url} attempt {attempt+1}: {e}")
                time.sleep(10)
        print(f"  exhausted retries on {url}, trying next mirror")
    raise RuntimeError(f"Overpass exhausted all mirrors: {last_exc}")


def _bbox_str(w, s, e, n) -> str:
    """Overpass bbox order: south, west, north, east."""
    return f"{s},{w},{n},{e}"


def _osm_to_gdf(data: dict, geom_type: str) -> gpd.GeoDataFrame:
    """Convert Overpass JSON 'elements' to a GeoDataFrame.

    geom_type: 'polygon' (buildings, water polygons), 'line' (roads).
    Uses the 'geometry' field returned by `out geom;` (list of {lat,lon}).
    """
    elements = data.get("elements", [])
    rows = []
    for el in elements:
        tags = el.get("tags", {})
        geom = None
        if el["type"] == "way":
            coords = [(p["lon"], p["lat"]) for p in el.get("geometry", [])]
            if len(coords) < 2:
                continue
            if geom_type == "polygon":
                if coords[0] != coords[-1]:
                    coords.append(coords[0])
                if len(coords) >= 4:
                    try:
                        geom = Polygon(coords)
                        if not geom.is_valid:
                            geom = geom.buffer(0)
                    except Exception:
                        continue
            else:
                geom = LineString(coords)
        elif el["type"] == "relation" and geom_type == "polygon":
            # Build a multipolygon from outer rings only — good enough for masks.
            # buffer(0) on an invalid Polygon can return a MultiPolygon, so we
            # union the lot with unary_union to land on a single valid geom.
            outers = []
            for m in el.get("members", []):
                if m.get("role") == "outer" and m.get("type") == "way":
                    coords = [(p["lon"], p["lat"]) for p in m.get("geometry", [])]
                    if len(coords) >= 3:
                        if coords[0] != coords[-1]:
                            coords.append(coords[0])
                        try:
                            poly = Polygon(coords)
                            if not poly.is_valid:
                                poly = poly.buffer(0)
                            outers.append(poly)
                        except Exception:
                            continue
            if outers:
                try:
                    geom = unary_union(outers)
                except Exception:
                    continue
        if geom is not None and not geom.is_empty:
            rows.append({"osm_id": el.get("id"), **tags, "geometry": geom})
    if not rows:
        return gpd.GeoDataFrame({"osm_id": [], "geometry": []}, crs="EPSG:4326")
    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    # Drop tag columns with mostly-None to keep file small
    keep = ["osm_id", "geometry"]
    for col in gdf.columns:
        if col in keep:
            continue
        if gdf[col].notna().sum() >= len(gdf) * 0.05:
            keep.append(col)
    return gdf[keep]


def download_roads(w, s, e, n) -> gpd.GeoDataFrame:
    print(f"[roads] querying {w:.4f},{s:.4f},{e:.4f},{n:.4f}")
    q = f"""[out:json][timeout:{TIMEOUT_S_QUERY}];
(
  way["highway"~"motorway|trunk|primary|secondary|tertiary|unclassified|residential|service|track|road"]
     ({_bbox_str(w,s,e,n)});
);
out geom;"""
    raw = _post(q)
    data = json.loads(raw)
    gdf = _osm_to_gdf(data, "line")
    print(f"[roads] got {len(gdf)} ways")
    return gdf


def download_water(w, s, e, n) -> gpd.GeoDataFrame:
    print(f"[water] querying {w:.4f},{s:.4f},{e:.4f},{n:.4f}")
    q = f"""[out:json][timeout:{TIMEOUT_S_QUERY}];
(
  way["natural"="water"]({_bbox_str(w,s,e,n)});
  way["waterway"="riverbank"]({_bbox_str(w,s,e,n)});
  relation["natural"="water"]({_bbox_str(w,s,e,n)});
  way["landuse"="reservoir"]({_bbox_str(w,s,e,n)});
);
out geom;"""
    raw = _post(q)
    data = json.loads(raw)
    gdf = _osm_to_gdf(data, "polygon")
    print(f"[water] got {len(gdf)} features")
    return gdf


def download_buildings(w, s, e, n, grid: int) -> gpd.GeoDataFrame:
    dx = (e - w) / grid
    dy = (n - s) / grid
    parts = []
    for iy in range(grid):
        for ix in range(grid):
            sub_w = w + ix * dx
            sub_e = sub_w + dx
            sub_s = s + iy * dy
            sub_n = sub_s + dy
            print(f"[buildings] tile {iy*grid + ix + 1}/{grid*grid} "
                  f"{sub_w:.4f},{sub_s:.4f},{sub_e:.4f},{sub_n:.4f}")
            q = f"""[out:json][timeout:{TIMEOUT_S_QUERY}];
(
  way["building"]({_bbox_str(sub_w, sub_s, sub_e, sub_n)});
  relation["building"]({_bbox_str(sub_w, sub_s, sub_e, sub_n)});
);
out geom;"""
            for attempt in range(3):
                try:
                    raw = _post(q)
                    data = json.loads(raw)
                    sub = _osm_to_gdf(data, "polygon")
                    print(f"  -> {len(sub)} buildings")
                    parts.append(sub)
                    time.sleep(2)  # be polite
                    break
                except Exception as e:
                    print(f"  failed attempt {attempt+1}: {e}")
                    time.sleep(20)
            else:
                print(f"  GIVING UP on this tile")
    if not parts:
        return gpd.GeoDataFrame({"osm_id": [], "geometry": []}, crs="EPSG:4326")
    import pandas as pd
    return gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs="EPSG:4326")


def main():
    bounds = json.loads(BOUNDS_FILE.read_text())
    w, s, e, n = bounds["bounds_wgs84"]
    print(f"Ortho extent (WGS84): W={w:.5f} S={s:.5f} E={e:.5f} N={n:.5f}")
    print(f"Area: {(e-w):.3f}° x {(n-s):.3f}° (~{(e-w)*100:.1f} km x {(n-s)*111:.1f} km)")

    layers = {
        "water":     (OUT_DIR / "water.gpkg",     lambda: download_water(w, s, e, n)),
        "roads":     (OUT_DIR / "roads.gpkg",     lambda: download_roads(w, s, e, n)),
        "buildings": (OUT_DIR / "buildings.gpkg", lambda: download_buildings(w, s, e, n, BUILD_GRID)),
    }
    for name, (path, fetch) in layers.items():
        if path.exists():
            print(f"[{name}] {path.name} already on disk, skipping")
            continue
        try:
            gdf = fetch()
        except Exception as ex:
            print(f"[{name}] FAILED: {ex}")
            continue
        if len(gdf) == 0:
            print(f"[{name}] empty, not writing")
            continue
        gdf.to_file(path, driver="GPKG", layer=name)
        print(f"[{name}] wrote {len(gdf)} features -> {path}")

    print("\nSummary:")
    for name, (path, _) in layers.items():
        if path.exists():
            info = gpd.read_file(path, rows=0)  # header only
            n_feats = len(gpd.read_file(path))
            print(f"  {name}: {n_feats} features in {path.name}")
        else:
            print(f"  {name}: NOT DOWNLOADED")


if __name__ == "__main__":
    main()
