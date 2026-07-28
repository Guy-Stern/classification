"""PROJ data-directory resolution — stdlib-only, safe to import before rasterio.

GDAL/PROJ pick their coordinate database from ``PROJ_LIB``/``PROJ_DATA``. On a
box that also has PostgreSQL/PostGIS, ArcGIS, or an OSGeo stack installed, that
variable often points at *their* ``proj.db``, whose schema predates what modern
pyproj needs, and every ``CRS.from_epsg`` call then dies with:

    PROJ: proj_create_from_database: ... contains DATABASE.LAYOUT.VERSION.MINOR = 2
    whereas a number >= 5 is expected. It comes from another PROJ installation.

``setup_proj_lib`` finds the first candidate whose layout is new enough and
pins both variables to it.

This lives in its own module — with **no** third-party imports — precisely so
the light entry points can call it before paying for the torch/sklearn import
chain. ``core.py`` calls it at import time (as it always did); ``cli.py`` calls
it up front so the manifest path's footprint reads, which happen in
``mosaic_catalog`` *before* ``core`` is ever imported, get a working PROJ too.
"""

from __future__ import annotations

import os
import site
import sys
from pathlib import Path
from typing import List, Optional

# PROJ database layouts below this are incompatible with modern pyproj/rasterio.
MIN_LAYOUT_MINOR = 5


def _layout_minor(proj_dir: Path) -> Optional[int]:
    """The ``DATABASE.LAYOUT.VERSION.MINOR`` of *proj_dir*'s proj.db, or None."""
    try:
        import sqlite3
        db_path = proj_dir / "proj.db"
        if not db_path.exists():
            return None
        con = sqlite3.connect(str(db_path))
        try:
            cur = con.execute(
                "select value from metadata where key='DATABASE.LAYOUT.VERSION.MINOR'"
            )
            row = cur.fetchone()
        finally:
            con.close()
        return int(row[0]) if row else None
    except Exception:
        return None


def _add_if_exists(paths: List[Path], value: Optional[Path]) -> None:
    if value and value.exists() and value not in paths:
        paths.append(value)


def _collect_site_packages() -> List[Path]:
    discovered: List[Path] = []
    try:
        for path_str in site.getsitepackages():
            p = Path(path_str)
            if p.exists() and p not in discovered:
                discovered.append(p)
    except Exception:
        pass

    try:
        user_site = site.getusersitepackages()
        if user_site:
            p = Path(user_site)
            if p.exists() and p not in discovered:
                discovered.append(p)
    except Exception:
        pass

    try:
        for path_str in sys.path:
            p = Path(path_str)
            if (p.name.lower() in {"site-packages", "dist-packages"}
                    and p.exists() and p not in discovered):
                discovered.append(p)
    except Exception:
        pass

    return discovered


def candidate_proj_dirs() -> List[Path]:
    """Every plausible PROJ data dir, best first."""
    candidates: List[Path] = []

    # Bundled proj data in the active environment wins — it always matches the
    # installed rasterio/pyogrio.
    for site_packages in _collect_site_packages():
        _add_if_exists(candidates, site_packages / "rasterio" / "proj_data")
        _add_if_exists(candidates, site_packages / "pyogrio" / "proj_data")

    try:
        from pyproj import datadir as _pyproj_datadir

        pyproj_dir = _pyproj_datadir.get_data_dir()
        if pyproj_dir:
            _add_if_exists(candidates, Path(pyproj_dir))
    except Exception:
        pass

    # Whatever the environment already asked for, checked like any other.
    for var in ("PROJ_DATA", "PROJ_LIB"):
        existing = os.environ.get(var)
        if existing:
            _add_if_exists(candidates, Path(existing))

    for system_proj in (
        Path("/usr/share/proj"),
        Path("/usr/local/share/proj"),
        Path("/opt/homebrew/share/proj"),
        Path("C:/Program Files/PROJ/share/proj"),
    ):
        _add_if_exists(candidates, system_proj)

    return candidates


def setup_proj_lib(verbose: bool = True) -> Optional[str]:
    """Pin ``PROJ_LIB``/``PROJ_DATA`` to a compatible proj.db.

    Returns the directory that was selected, or ``None`` when none qualified
    (in which case the environment is left untouched and a warning is printed).
    Idempotent — calling it twice is harmless.
    """
    # Already pinned to something compatible? Say nothing. Worker processes
    # inherit the parent's environment and re-run this on import, so without
    # this the log gets one banner per worker.
    already = os.environ.get("PROJ_DATA") or os.environ.get("PROJ_LIB")
    if already:
        current = Path(already)
        minor = _layout_minor(current)
        if minor is not None and minor >= MIN_LAYOUT_MINOR:
            os.environ["PROJ_LIB"] = str(current)
            os.environ["PROJ_DATA"] = str(current)
            return str(current)

    for proj_dir in candidate_proj_dirs():
        layout_minor = _layout_minor(proj_dir)
        if layout_minor is None or layout_minor < MIN_LAYOUT_MINOR:
            continue
        os.environ["PROJ_LIB"] = str(proj_dir)
        # PROJ >= 9 reads PROJ_DATA and ignores PROJ_LIB; set both so the pin
        # holds no matter which PROJ the loaded GDAL was linked against.
        os.environ["PROJ_DATA"] = str(proj_dir)
        if verbose:
            print(f"[PROJ] Set PROJ_LIB to: {proj_dir} (layout {layout_minor})")
        return str(proj_dir)

    if verbose:
        print(
            f"[PROJ] WARNING: Could not find compatible proj.db "
            f"(layout >= {MIN_LAYOUT_MINOR})"
        )
    return None
