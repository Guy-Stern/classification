"""Persistent config for 6-material MEA mask priors.

Lives next to ``app_config.json`` (frozen exe dir or repo root in dev)
so users can edit it directly with any text editor, OR - when the machine-scope
``MC_SHAPEFILE_CONFIG`` env var is set - is read from that shared path instead,
which then WINS over the local file (see ``_ENV_OVERRIDE``). Two concerns:

  * ``water_mask`` — a single georeferenced GeoTIFF (band 1 > 0 = water)
    painted directly as BM_WATER. No vector resolve / rasterise.
  * ``sde`` — direct extraction of building / road features from an Esri
    enterprise geodatabase via an arcpy subprocess worker (Path B — see
    ``sde_extractor.py``). Road LINE features are buffered to polygons in
    ``shapefile_resolver`` using a 3-tier width: ``road_width_attr`` (explicit
    per-feature width), else a road-type width (``Road_Type_Attr`` ->
    ``Road_Type_Width_Main/SideRoad_m``), else ``road_width_fallback_m``.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict

from . import config as _app_config

_CONFIG_FILE = _app_config._config_dir() / "shapefile_config.json"

# Optional SHARED config, named by a machine-scope env var. When set it WINS over
# the local file, so one file on a share drives a whole fleet instead of editing
# every box (the local copy JARVIS preserves in `.config\` is then ignored).
#
# Opt-in per box on purpose: the config is entirely PATHS, so a central copy holds
# UNC paths that mean nothing on an air-gapped machine. Networked boxes set the
# var; air-gapped boxes never do and keep today's local-file behaviour exactly.
#
# Set but unusable is a HARD ERROR, never a silent fall back to the local file:
# pointing a box at a shared config and then quietly running a stale local one is
# the divergence this exists to remove, and it would be invisible in the output.
_ENV_OVERRIDE = "MC_SHAPEFILE_CONFIG"

_FEATURE_TYPES = ("buildings", "roads")


class ShapefileConfigError(RuntimeError):
    """The shared config named by ``MC_SHAPEFILE_CONFIG`` is missing or unreadable.

    Deliberately fatal — see ``_ENV_OVERRIDE``. Only ever raised when that variable
    is set; an unset variable keeps the tolerant local-file path.
    """

# Default skeleton for the optional ``sde`` block. Mirrored in
# installer_assets/Post-Install.bat's config-template logic so a fresh
# install ships with the keys visible (empty) for the operator to fill in.
_SDE_DEFAULT: Dict[str, Any] = {
    "enabled": False,
    "connection_file": "",          # path to the .sde connection file
    "arcpy_python": "",             # path to ArcGIS Pro's python.exe (has arcpy)
    "tile_size_metres": 5000,       # split bounds into 5 km tiles by default
    "timeout_seconds": 1800,        # 30 min ceiling for the arcpy subprocess
    # Road width is resolved per feature in 3 tiers (see shapefile_resolver._road_width_m):
    "road_width_attr": "",            # tier 1: SDE field with full road width in metres (<=10 chars; "" -> tier 2)
    "Road_Type_Attr": "",             # tier 2: SDE field holding the road type/class (<=10 chars; "" -> tier disabled)
    "Road_Type_Key_MainRoad": "",     #         value of Road_Type_Attr that marks a MAIN road
    "Road_Type_Width_MainRoad_m": 0.0,  #       full width (m) for main roads (<=0 -> fall through to fallback)
    "Road_Type_Key_SideRoad": "",     #         value of Road_Type_Attr that marks a SIDE road
    "Road_Type_Width_SideRoad_m": 0.0,  #       full width (m) for side roads (<=0 -> fall through to fallback)
    "road_width_fallback_m": 2.0,     # tier 3: total road width (m) when nothing above applies; buffer radius = this/2
    "layers": {ft: "" for ft in _FEATURE_TYPES},
}

_cache: Dict[str, Any] | None = None


def _merge(cfg: Dict[str, Any], stored: Dict[str, Any]) -> None:
    """Merge a parsed config document over the defaults already in *cfg*.

    Shared by both sources so a shared config and a local one are interpreted
    identically - only WHERE the document came from differs.
    """
    cfg["water_mask"] = str(stored.get("water_mask") or "").strip()
    sde_stored = stored.get("sde")
    if not isinstance(sde_stored, dict):
        return
    for key in _SDE_DEFAULT:
        if key == "layers":
            layers_stored = sde_stored.get("layers") or {}
            if isinstance(layers_stored, dict):
                for ft in _FEATURE_TYPES:
                    cfg["sde"]["layers"][ft] = str(layers_stored.get(ft) or "")
        elif key in sde_stored:
            cfg["sde"][key] = sde_stored[key]


def _read_shared(path: Path) -> Dict[str, Any]:
    """Read the shared config named by ``MC_SHAPEFILE_CONFIG``, or raise.

    Every failure mode is fatal and names the path plus the way out, because the
    alternative - dropping back to the local file - is exactly the silent
    divergence the shared config exists to prevent.
    """
    hint = (f"Fix the path or the share, or unset {_ENV_OVERRIDE} to use the local "
            f"{_CONFIG_FILE.name}.")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ShapefileConfigError(
            f"{_ENV_OVERRIDE} points at {path}, which could not be read: {e}. {hint}"
        ) from e
    try:
        stored = json.loads(text)
    except ValueError as e:
        raise ShapefileConfigError(
            f"{_ENV_OVERRIDE} points at {path}, which is not valid JSON: {e}. {hint}"
        ) from e
    if not isinstance(stored, dict):
        raise ShapefileConfigError(
            f"{_ENV_OVERRIDE} points at {path}, whose top level is "
            f"{type(stored).__name__}, not a JSON object. {hint}"
        )
    return stored


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

    # Strip surrounding quotes: `setx MC_SHAPEFILE_CONFIG "\\nas\..."` can store them.
    override = (os.environ.get(_ENV_OVERRIDE) or "").strip().strip('"')
    if override:
        shared = Path(override)
        _merge(cfg, _read_shared(shared))  # any problem raises - see _ENV_OVERRIDE
        print(f"[shapefile_config] source: SHARED {shared} (via {_ENV_OVERRIDE})")
    elif _CONFIG_FILE.exists():
        # Local file stays TOLERANT (unchanged behaviour): a malformed local config
        # degrades to defaults rather than stopping an air-gapped box mid-run.
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                _merge(cfg, json.load(f))
            print(f"[shapefile_config] source: local {_CONFIG_FILE}")
        except Exception as e:
            print(f"[shapefile_config] failed to read {_CONFIG_FILE}: {e} - using defaults")
    else:
        print(f"[shapefile_config] no config at {_CONFIG_FILE} - using defaults (sde.enabled=False)")

    _cache = cfg
    return cfg


def save(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge *updates* into the config and persist to disk.

    Accepts updates for feature-type path lists and/or the ``sde`` block.
    Unknown keys are ignored.

    NOTE: always writes the LOCAL ``_CONFIG_FILE``, never the shared config - a
    box must not rewrite a file the whole fleet reads. Currently uncalled; if it
    is ever wired to an endpoint, refuse the write (or warn) while
    ``MC_SHAPEFILE_CONFIG`` is set, or the save will appear to succeed and then
    be ignored by the next ``load()``.
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
