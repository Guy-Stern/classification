"""Persistent config for 6-material MEA mask priors.

Lives next to ``app_config.json`` (frozen exe dir or repo root in dev)
so users can edit it directly with any text editor. Two concerns:

  * ``water_mask`` — a single georeferenced GeoTIFF (band 1 > 0 = water)
    painted directly as BM_WATER. No vector resolve / rasterise.
  * ``sde`` — direct extraction of building / road features from an Esri
    enterprise geodatabase via an arcpy subprocess worker (Path B — see
    ``sde_extractor.py``). ``road_width_attr`` / ``road_width_fallback_m``
    drive line→polygon buffering of road features (see
    ``shapefile_resolver``).
"""
import json
from typing import Any, Dict

from . import config as _app_config

_CONFIG_FILE = _app_config._config_dir() / "shapefile_config.json"

_FEATURE_TYPES = ("buildings", "roads")

# Default skeleton for the optional ``sde`` block. Mirrored in
# installer_assets/Post-Install.bat's config-template logic so a fresh
# install ships with the keys visible (empty) for the operator to fill in.
_SDE_DEFAULT: Dict[str, Any] = {
    "enabled": False,
    "connection_file": "",          # path to the .sde connection file
    "arcpy_python": "",             # path to ArcGIS Pro's python.exe (has arcpy)
    "tile_size_metres": 5000,       # split bounds into 5 km tiles by default
    "timeout_seconds": 1800,        # 30 min ceiling for the arcpy subprocess
    "road_width_attr": "",          # SDE field with full road width in metres (<=10 chars; "" -> use fallback)
    "road_width_fallback_m": 2.0,   # total road width (m) when the attr is missing/<=0; buffer radius = this/2
    "layers": {ft: "" for ft in _FEATURE_TYPES},
}

_cache: Dict[str, Any] | None = None


def load() -> Dict[str, Any]:
    """Load shapefile config from disk (cached after first read).

    Returns a dict with the per-feature-type path lists plus an ``sde``
    block. Missing keys are filled from defaults so callers don't need
    ``.get(..., default)`` everywhere.
    """
    global _cache
    if _cache is not None:
        return _cache

    cfg: Dict[str, Any] = {"water_mask": ""}
    cfg["sde"] = dict(_SDE_DEFAULT)
    cfg["sde"]["layers"] = dict(_SDE_DEFAULT["layers"])

    print(f"[debug-config] looking for shapefile_config.json at: {_CONFIG_FILE}")
    print(f"[debug-config]   exists={_CONFIG_FILE.exists()}  frozen={getattr(__import__('sys'), 'frozen', False)}")
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
            print(f"[debug-config] loaded JSON with top-level keys: {sorted(stored.keys())}")
            cfg["water_mask"] = str(stored.get("water_mask") or "").strip()
            print(f"[debug-config] water_mask = {cfg['water_mask']!r}")
            sde_stored = stored.get("sde")
            if isinstance(sde_stored, dict):
                print(f"[debug-config] found 'sde' block with keys: {sorted(sde_stored.keys())}")
                for key, default in _SDE_DEFAULT.items():
                    if key == "layers":
                        layers_stored = sde_stored.get("layers") or {}
                        if isinstance(layers_stored, dict):
                            for ft in _FEATURE_TYPES:
                                cfg["sde"]["layers"][ft] = str(layers_stored.get(ft) or "")
                    elif key in sde_stored:
                        cfg["sde"][key] = sde_stored[key]
                print(f"[debug-config] final sde.enabled         = {cfg['sde'].get('enabled')!r}")
                print(f"[debug-config] final sde.connection_file = {cfg['sde'].get('connection_file')!r}")
                print(f"[debug-config] final sde.arcpy_python    = {cfg['sde'].get('arcpy_python')!r}")
                print(f"[debug-config] final sde.tile_size_metres= {cfg['sde'].get('tile_size_metres')!r}")
                print(f"[debug-config] final sde.timeout_seconds = {cfg['sde'].get('timeout_seconds')!r}")
                print(f"[debug-config] final sde.road_width_attr = {cfg['sde'].get('road_width_attr')!r}")
                print(f"[debug-config] final sde.road_width_fb_m = {cfg['sde'].get('road_width_fallback_m')!r}")
                print(f"[debug-config] final sde.layers          = {cfg['sde'].get('layers')!r}")
            else:
                print(f"[debug-config] no 'sde' block in JSON — using defaults (enabled=False)")
        except Exception as e:
            print(f"[shapefile_config] failed to read {_CONFIG_FILE}: {e}")
    else:
        print(f"[debug-config] file NOT FOUND — SDE config will be at defaults (enabled=False)")

    _cache = cfg
    return cfg


def save(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *updates* into the config and persist to disk.

    Accepts updates for feature-type path lists and/or the ``sde`` block.
    Unknown keys are ignored.
    """
    global _cache
    cfg = load()
    if "water_mask" in updates:
        cfg["water_mask"] = str(updates["water_mask"] or "").strip()
    if isinstance(updates.get("sde"), dict):
        sde_update = updates["sde"]
        for key, default in _SDE_DEFAULT.items():
            if key == "layers":
                layers_update = sde_update.get("layers")
                if isinstance(layers_update, dict):
                    for ft in _FEATURE_TYPES:
                        if ft in layers_update:
                            cfg["sde"]["layers"][ft] = str(layers_update[ft] or "")
            elif key in sde_update:
                cfg["sde"][key] = sde_update[key]
    _cache = cfg

    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[shapefile_config] failed to write {_CONFIG_FILE}: {e}")

    return cfg


def get_water_mask() -> str:
    """Return the configured water-mask raster path (``""`` if unset).

    Water is painted from this single georeferenced GeoTIFF (band 1 > 0 =
    water) — see ``pipeline.apply_v6_masks_to_classification``.
    """
    return str(load().get("water_mask") or "")


def get_sde() -> Dict[str, Any]:
    """Return the ``sde`` configuration block (always populated with defaults)."""
    return dict(load().get("sde") or _SDE_DEFAULT)


def config_path() -> str:
    """Return the path to the config file (for display / docs)."""
    return str(_CONFIG_FILE)
