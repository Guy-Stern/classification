Classification Web App + CLI — Offline Installer
==================================================

THIS INSTALLER IS 100% OFFLINE. No internet connection is needed
on the target machine at any point. Every Python wheel and binary
is bundled inside this folder.

  WHAT THIS BUILD INCLUDES: 6-material classification, the layered
  JPEG-2000 / GeoTIFF geocell manifest (--manifest), and GPU KMeans
  (PyTorch cu121 + CUDA 12.4 wheels).
  WHAT IT DOES NOT INCLUDE: the multi-GB SAM3 AI model weights and the
  sam3 / segment-geospatial packages, so SAM3 road/building extraction
  is DISABLED until you add them. Everything else (KMeans, shapefile /
  SDE masks, the --manifest mode) works exactly as shipped.

Layout
------
  ClassificationInstaller.exe   The install wizard (folder picker,
                                installs Python + base packages).
  Post-Install.bat              Patcher — copies CLI files the wizard
                                misses + installs torch / torchvision
                                (cu121) from the bundled wheel cache +
                                copies the examples\ folder + drops a
                                shapefile_config.json template. ALL FROM
                                LOCAL WHEELS — no network calls. (It also
                                tries sam3 / timm, which this build does
                                not bundle; that step is skipped.)
  README.txt                    This file.
  examples\                     Usage examples for common user cases
                                (USE_CASES.txt) + a ready-to-edit geocell
                                manifest template (geocell.example.toml).
                                Post-Install.bat copies these into the
                                install folder under examples\.

  offline_installer\            All install payload (~5.6 GB):
    Setup.bat / Setup.ps1       Direct-install fallback (use if the
                                wrapper exe doesn't work).
    prerequisites\              Embedded Python 3.11.9 + get-pip.py.
    offline_packages\           Core pip wheels (FastAPI, rasterio,
                                geopandas, ...) + PyTorch cu121, ~84
                                wheels. Does NOT include sam3 / timm /
                                segment-geospatial in this build.
    offline_packages_torch\     PyTorch 2.5.1+cu121 (2.3 GB) +
                                torchvision 0.20.1+cu121 + deps.
    offline_packages_gpu\       CuPy + NVIDIA CUDA runtime libs (for
                                GPU-accelerated KMeans).
    app\
      ClassificationWebApp.exe        Runtime GUI launcher.
      MaterialClassification_CLI.exe  Runtime CLI launcher.
      backend\, web_app\dist\         App code + frontend bundle.
      mea_calibration_tool\, shared\
      models\
        sam3\                         Tokenizer + config JSONs only. The
                                      SAM3 weights (sam3.pt /
                                      sam3.1_multiplex.pt, multi-GB) are
                                      NOT bundled in this build.
        hf_cache\hub\                 Model config JSONs (no weights).

  sam3_runtime\                 SAM3 BPE tokenizer asset that the
                                pip-installed sam3 wheel doesn't ship
                                (Post-Install.bat drops it into the
                                venv site-packages\assets\ dir).


Two-step install on the air-gapped target
------------------------------------------

  1. Double-click  ClassificationInstaller.exe
     - Folder picker: select the  offline_installer  folder.
     - Pick an install dir (default: C:\ClassificationApp).
     - Wizard installs Python + base packages from offline_packages\,
       copies app\ and writes start.bat shortcuts. (No SAM3 AI model
       weights are bundled in this build.)

  2. Double-click  Post-Install.bat
     - Press Enter to accept the install dir from step 1.
     - Patcher copies cli.py / cli_launcher.py / the two .exe files
       (the wrapper from step 1 doesn't know about them — it predates
       this branch's CLI feature).
     - pip-installs torch + torchvision (CUDA 12.1) from
       offline_packages*\ — --no-index means pip CANNOT touch the
       internet. (sam3 / timm / segment-geospatial are NOT bundled in
       this build, so that part is skipped — SAM3 extraction stays off.)
     - Drops the SAM3 BPE tokenizer asset into the venv.
     - Drops a shapefile_config.json template at the install root.

After install
-------------

  GUI:    double-click  <install dir>\start.bat
          (or  ClassificationWebApp.exe)

  CLI:    open CMD, then:
          cd C:\ClassificationApp
          MaterialClassification_CLI.exe input.tif output.tif

  Help:   MaterialClassification_CLI.exe --examples


Enterprise geodatabase (SDE / ArcGIS) setup
-------------------------------------------
This build ships with SDE extraction ENABLED in shapefile_config.json
(written to the install root by Post-Install.bat). On every
classification it pulls building / road features for the raster's
footprint straight from an Esri enterprise geodatabase, using a worker
that runs under ArcGIS Pro's Python (arcpy). Road lines are buffered to
polygons using a 3-tier width (explicit width field -> road-type width ->
fallback; see road_width_attr / Road_Type_* below). Water is painted
separately from a raster mask (see "water_mask" below).

A fully filled-in reference, with every SDE and road-width field
populated, ships next to this README as
  shapefile_config.example.json
(also copied into the install dir by Post-Install.bat). Copy values
from it; do NOT rename it over your live config unless you've edited
the paths to match your machine.

To finish wiring it, edit the "sde" block in
  <install dir>\shapefile_config.json
and replace the SET_ME_ placeholders:

  connection_file   Full path to your .sde connection file on THIS
                    machine. Create one in ArcGIS Pro:
                    Catalog > Database Connections > Add Database
                    Connection, then point this at the resulting .sde.

  layers            The feature-class names in the geodatabase for
                    buildings / roads, e.g. "MYDB.SDE.BUILDINGS".

  Road width is resolved per road feature in 3 tiers, top to bottom:

  road_width_attr   TIER 1. Field on the road features holding the FULL
                    road width in metres (used to buffer road lines into
                    polygons). ESRI shapefiles truncate field names to
                    10 chars, so use the truncated name. Leave "" (or a
                    value <= 0 on a feature) to fall through to tier 2.

  Road_Type_Attr    TIER 2. Field holding the road type/class (also <= 10
                    chars). When a road has no usable road_width_attr, its
                    Road_Type_Attr value is matched (case-insensitively)
                    against the two keys below to pick a width. Leave ""
                    to skip this tier entirely.
  Road_Type_Key_MainRoad / Road_Type_Width_MainRoad_m
                    The Road_Type_Attr value that marks a MAIN road, and
                    the full width in metres to give it.
  Road_Type_Key_SideRoad / Road_Type_Width_SideRoad_m
                    Same for SIDE roads. (A matched type whose width is
                    <= 0 falls through to the fallback.)

  road_width_fallback_m
                    TIER 3. Road width in metres when none of the above
                    applies (default 2.0).

  water_mask        Full path to a single-band GeoTIFF where band 1 > 0
                    marks water. Covers the whole AOI; it's reprojected /
                    clipped to each ortho automatically and painted as
                    BM_WATER. Leave "" for no water. (If you previously
                    set water_mask_path in app_config.json, move that
                    path here.)

  arcpy_python      Path to ArcGIS Pro's python.exe (the one with
                    arcpy). Default assumes a standard install:
                    C:/Program Files/ArcGIS/Pro/bin/Python/envs/arcgispro-py3/python.exe
                    Change only if ArcGIS Pro lives elsewhere.

Requirements: ArcGIS Pro (with arcpy) installed on this machine, and
the account must be able to reach the geodatabase.

Graceful fallback: if any SET_ME_ placeholder is left in place, the
.sde can't be opened, or ArcGIS Pro isn't found, SDE extraction is
skipped and the app falls back to SAM3 for roads/buildings (water only
paints when water_mask is set). To disable SDE entirely, set
"enabled": false.

Verify the connection (recommended, before a full run):
  <install dir>\.venv\Scripts\python.exe sde_conn_test.py
It opens the .sde, lists each layer's feature count / CRS / extent, and
writes sde_conn_test_<timestamp>.log next to the script. Add
  --raster <ortho.tif>
to also test a real extraction (tiling -> selection -> shapefiles).
Send that .log file if anything fails.


Why pip --no-index is air-gap safe
-----------------------------------
Post-Install.bat invokes pip like this:

  pip install --no-index --find-links offline_packages*\ ...

The  --no-index  flag tells pip to NEVER contact PyPI. It only
resolves packages from the directories listed in --find-links.
If a wheel or its transitive dep isn't in those local folders,
pip fails LOUDLY rather than reaching for the internet. Tested on
the dev box with HTTP_PROXY blocked — install succeeded entirely
from local wheels.


SmartScreen warning
-------------------
The first time you run an unsigned .exe, Windows shows
"Windows protected your PC". Click "More info" then "Run anyway".


Troubleshooting
---------------
- Big-raster memory error in fusion ("Unable to allocate N GiB"):
  re-run with --tiling, e.g.
    MaterialClassification_CLI.exe in.tif out.tif --tiling --tile-size 2048
  Tile mode processes the raster in 2048-px squares and avoids the
  monolithic float64 allocation that fails on 10240x10240 inputs.

- "ERROR: ... does not look like a finished install" from
  Post-Install.bat means the wizard didn't finish — re-run
  ClassificationInstaller.exe first.

- For a fully manual install (skip the wrapper exe), run
  offline_installer\Setup.bat directly. Setup.ps1's foreach-copy
  loop is up to date and copies the CLI files itself, so a Setup.bat
  install does NOT need Post-Install.bat for the file copies — only
  for the AI-deps pip install (which Setup.ps1 doesn't currently do).
