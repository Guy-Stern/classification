"""Shared pytest setup.

Pins PROJ to rasterio's bundled ``proj.db`` **before** any test module imports
rasterio. This machine ships a PostgreSQL/PostGIS PROJ on ``PROJ_LIB`` whose
database layout (``DATABASE.LAYOUT.VERSION.MINOR = 2``) predates what pyproj
requires (>= 5), so any ``CRS.from_epsg`` call blows up with a confusing
"comes from another PROJ installation" error.

``backend.app.core`` already resolves this for the app at import time
(``core._setup_proj_lib``), but tests touch rasterio without importing core, and
so does ``mosaic_catalog`` on the manifest path — hence doing it here too.
"""

from __future__ import annotations

import os
from pathlib import Path

_BUNDLED = (
    Path(__file__).resolve().parents[1]
    / ".venv" / "Lib" / "site-packages" / "rasterio" / "proj_data"
)

if (_BUNDLED / "proj.db").is_file():
    os.environ["PROJ_LIB"] = str(_BUNDLED)
    os.environ["PROJ_DATA"] = str(_BUNDLED)
