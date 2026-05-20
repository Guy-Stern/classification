"""Persistent paths for road / building / water shapefiles used as
mask priors during 6-material MEA classification.

This is a parallel to ``app_config.json`` but list-shaped: each feature
type can have multiple source shapefiles (e.g. one per state or region),
and the resolver picks/unions the relevant ones at runtime based on the
ortho's bounds.

The file lives next to ``app_config.json`` (frozen exe dir or repo root
in dev) so users can edit it directly with any text editor.
"""
import json
from typing import Dict, List

from . import config as _app_config

_CONFIG_FILE = _app_config._config_dir() / "shapefile_config.json"

_FEATURE_TYPES = ("buildings", "roads", "water")

_cache: Dict[str, List[str]] | None = None


def load() -> Dict[str, List[str]]:
    """Load shapefile-paths config from disk (cached after first read)."""
    global _cache
    if _cache is not None:
        return _cache

    cfg: Dict[str, List[str]] = {ft: [] for ft in _FEATURE_TYPES}
    if _CONFIG_FILE.exists():
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                stored = json.load(f)
            for ft in _FEATURE_TYPES:
                value = stored.get(ft, [])
                if isinstance(value, list):
                    cfg[ft] = [str(p) for p in value if p]
                else:
                    print(f"[shapefile_config] {ft!r} is not a list, ignoring")
        except Exception as e:
            print(f"[shapefile_config] failed to read {_CONFIG_FILE}: {e}")

    _cache = cfg
    return cfg


def save(updates: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """Merge *updates* into the config and persist to disk."""
    global _cache
    cfg = load()
    for ft, paths in updates.items():
        if ft in _FEATURE_TYPES and isinstance(paths, list):
            cfg[ft] = [str(p) for p in paths if p]
    _cache = cfg

    try:
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[shapefile_config] failed to write {_CONFIG_FILE}: {e}")

    return cfg


def get(feature_type: str) -> List[str]:
    """Return the list of shapefile paths configured for ``feature_type``.

    Empty list if none configured or if the feature type is unknown.
    """
    return list(load().get(feature_type, []))


def config_path() -> str:
    """Return the path to the config file (for display / docs)."""
    return str(_CONFIG_FILE)
