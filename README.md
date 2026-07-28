# Image Material Classification

Unsupervised classification of orthophoto / multispectral imagery into MEA (Multi-Element Aperture) material classes,
with optional AI-driven feature extraction (roads, buildings, trees, fields, water) and a sidecar
material table for downstream simulation tools.

The project ships **four runnable entry points**:

| App | Path | Purpose |
|-----|------|---------|
| **Web app** (primary) | `backend/` + `web_app/` | FastAPI + React UI — main pipeline, AI extraction, batch mode |
| **CLI** | `cli.py` | Same pipeline, scripted. File / folder-batch / TOML-manifest geocell modes |
| **MEA Calibration Tool** | `mea_calibration_tool/` | Stand-alone FastAPI + React tool for sampling material reference colors and writing a calibration profile that the main app consumes |
| **Tkinter app** (legacy) | `tkinter_app.py` | Desktop GUI — **unmaintained**; kept for history only |

> **New here? Start with [ONBOARDING.md](ONBOARDING.md)** — the day-1 path from clone to
> first classification, plus the mental model behind the pipeline. A visual, printable
> version is at [docs/ONBOARDING.pdf](docs/ONBOARDING.pdf).

For an operational reference (commands, troubleshooting), see [RUNNING_GUIDE.md](RUNNING_GUIDE.md).
For deployment to a no-internet station, see [STANDALONE_DEPLOYMENT.md](STANDALONE_DEPLOYMENT.md).
For current branches / open PRs / known bugs, see [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md).

---

## Pipeline Overview

The classification pipeline is split into **two independent steps** so the (slow) clustering does
not need to be re-run when only vector overlays change:

```
                 ┌──────────────────────┐    ┌─────────────────────────────────┐
   raster ──►   │ Step 1               │ ── │ Step 2                          │── classified.tif
   (vrt/tif)    │ classify_and_export  │    │ rasterize_vectors_onto_class…   │   (+ companion XML)
                │ KMeans + RGB export  │    │ Vector overlays                 │
                └──────────────────────┘    └─────────────────────────────────┘
                          │                                │
                  POST /classify-step1               POST /classify-step2
                                  POST /classify   (= step1 + step2)
```

Both steps live in [backend/app/core.py](backend/app/core.py) (~4.5 kLoC); the wrapper `classify()`
calls them sequentially and stays backward-compatible.

MEA runs go through a third layer on top: `classify_v6()` in
[backend/app/pipeline.py](backend/app/pipeline.py) orchestrates mask acquisition → KMeans →
fusion → XML rewrite. That is the flow the web app, the CLI and manifest mode all use.

### MEA mode

When the chosen class set matches the 6 MEA materials (`MEA_CLASSES` in `core.py`,
detected by `_is_mea_classes()`), the pipeline:

- Picks per-class colors from the calibration profile (or factory defaults).
- Enforces strict 1:1 cluster→material assignment (Hungarian), so no single material can
  absorb multiple clusters.
- Writes a companion `<output>.xml` `<Composite_Material_Table>` with `_MEA_COMPOSITE_NAMES`
  composite names and ARGB colors.
- Optionally writes `.txr` / `.txs` sidecars for the simulator.
- Reprojects to EPSG:4326.

Material reference colors are read from
`%ProgramData%\MaterialClassification\mea_calibration_profile.json` if present,
falling back to [shared/mea_defaults.json](shared/mea_defaults.json). See
[docs/MEA_CALIBRATION_TOOL.md](docs/MEA_CALIBRATION_TOOL.md).

> `shared/mea_classes.json` still lists the legacy **13**-class schema. **No code reads
> it** — it is a leftover from before phase 4. The live schema is `MEA_CLASSES` in
> `core.py` plus `shared/mea_defaults.json`.

### MEA classes (6)

`BM_ASPHALT`, `BM_CONCRETE`, `BM_VEGETATION`, `BM_WATER`, `BM_SAND`, `BM_SOIL`.

The pipeline is SAM3-first: water comes from a shapefile (if provided),
roads/buildings come from SAM3 (or shapefiles), and KMeans only handles the
three "earth" classes — vegetation, sand, soil. Each kmeans-source material
has a single anchor color (vegetation `[0,100,0]`, sand `[230,200,130]`,
soil `[85,55,30]`) for clean spectral separation.

---

## AI Feature Extraction

The web app can refine the classification with text-prompted segmentation. See
[docs/AI_FEATURE_EXTRACTION.md](docs/AI_FEATURE_EXTRACTION.md) for the full description.

| Feature | Backend prompts (per tile) | Default merge color |
|---------|----------------------------|---------------------|
| Roads | `road, highway, asphalt path` | `BM_ASPHALT` (`#2D2D30`) |
| Buildings | `building, house, roof, rooftop, structure` | `BM_CONCRETE` (`#B4B4B4`) |
| Trees | `tree, trees, forest, woodland, grove` | `BM_VEGETATION` (`#006400`) |
| Fields | `grass, lawn, field, meadow, pasture` + `crop, farmland, agriculture, cultivated field` | `BM_VEGETATION` (`#006400`) |
| Water | `water, lake, pond, reservoir, pool` + `river, stream, canal, waterway, channel` + `sea, ocean, fish pond, swimming pool` | `BM_WATER` (`#1C6BA0`) |

Each feature uses a two-strategy detector — OWLv2+SAM2/3 text segmentation **OR'd** with a
color+geometry CV detector — plus a per-feature linearity / RGB pre-filter so the model is
not invoked on terrain that obviously contains nothing of the target type.

Outputs are written to `_<feature_type>/` subfolders next to the input raster and merged onto
the classification with `merge_feature_masks_onto_classification()`.

---

## Repository Layout

```
classification-master/
├── backend/                  FastAPI service (KMeans + AI extraction)
│   ├── app/
│   │   ├── main.py             FastAPI endpoints, SSE progress, cancellation
│   │   ├── core.py             KMeans engine, tiling, MEA, XML/TXR/TXS export
│   │   ├── pipeline.py         classify_v6 — SAM3-first 6-material orchestrator
│   │   ├── road_extraction.py  OWLv2+SAM2/SAM3 + color/geometry detectors
│   │   ├── manifest.py         TOML geocell manifest schema (pydantic)
│   │   ├── geocell.py          OGC CDB geocell geometry (pure math)
│   │   ├── mosaic_catalog.py   Source discovery + geocell intersection
│   │   ├── mosaic_builder.py   Priority last-wins mosaic → one EPSG:4326 GeoTIFF
│   │   ├── shapefile_config.py Loads shapefile_config.json (water_mask + sde)
│   │   ├── shapefile_resolver.py  Smart-trim: intersect, reproject, union → temp shp
│   │   ├── sde_extractor.py    Esri SDE extraction (spawns the arcpy worker)
│   │   ├── sde_arcpy_worker.py Runs under ArcGIS Pro's Python — the only arcpy importer
│   │   ├── config.py           Persistent JSON app config
│   │   └── mea_profile.py      Reader for the calibration profile
│   ├── tests/                  pytest suite (manifest, geocell, mosaic, MEA v6, batch)
│   ├── requirements.txt
│   └── requirements-gpu.txt    Adds CuPy + nvidia-cuda-* wheels (pinned CUDA 12.4)
│
├── cli.py                    CLI: file / folder-batch / --manifest geocell modes
├── cli_launcher.py           Thin subprocess wrapper so the exe stays ~5 MB
│
├── web_app/                  Vite + React + TypeScript frontend
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api/client.ts        FastAPI client + SSE progress
│   │   ├── store/               Reducer-based state
│   │   ├── constants/mea.ts
│   │   └── components/
│   │       ├── MapView.tsx, LayerPanel.tsx, Layout.tsx, …
│   │       └── sidebar/         InputSection, MaterialsSection,
│   │                            FeaturesSection, VectorsSection,
│   │                            PerformanceSection, ClassificationSection,
│   │                            ActionsSection, SettingsSection,
│   │                            MeaProfileStatus
│   └── package.json
│
├── mea_calibration_tool/     Stand-alone calibration app
│   ├── backend/app/             FastAPI: profile / sampling / raster_io
│   ├── web_app/                 Companion React UI
│   └── launcher.py
│
├── shared/                   Cross-app constants
│   ├── mea_defaults.json        Factory-default reference colors + anchors (live)
│   ├── mea_classes.json         LEGACY 13-class list — unused by any code
│   └── contracts/               JSON-Schema request contracts
│
├── offline_installer/        Self-contained installer for offline stations
│   ├── Setup.bat / Setup.ps1    Installer wizard
│   ├── prerequisites/           Embedded Python + pip
│   ├── offline_packages*/       Bundled wheels (core / torch / GPU)
│   └── app/                     Pre-built application files
│
├── tkinter_app.py            Legacy desktop GUI
├── ClassificationApp.iss     Inno Setup installer script
├── *.spec                    PyInstaller specs (CLI / web app / app)
├── prepare_offline.bat       Builds the offline_installer/ payload
├── build_installer.bat       Builds ClassificationApp_Setup.exe (Inno)
├── start_webapp.bat          One-click launch (backend + Vite dev)
└── docs/                     Topic deep-dives (see below)
```

### Topic deep-dives

- [ONBOARDING.md](ONBOARDING.md) — **start here**: day-1 setup, mental model, reading order
- [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) — branches, PRs, known open bugs
- [docs/CLI_GUIDE.md](docs/CLI_GUIDE.md) — full CLI reference manual (also `cli.py --examples`)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — request/response flow, module map
- [docs/API_REFERENCE.md](docs/API_REFERENCE.md) — every FastAPI endpoint
- [docs/AI_FEATURE_EXTRACTION.md](docs/AI_FEATURE_EXTRACTION.md) — OWLv2 + SAM, model setup
- [docs/MEA_CALIBRATION_TOOL.md](docs/MEA_CALIBRATION_TOOL.md) — calibrating reference colors
- [docs/GPU_ACCELERATION.md](docs/GPU_ACCELERATION.md) — CuPy / FAISS engine selection
- [docs/TILING.md](docs/TILING.md) — tile mode & `suggest_tile_size`
- [docs/MC_MANIFEST_BUGS_2026-07-12.md](docs/MC_MANIFEST_BUGS_2026-07-12.md) — live E2E bug report
- [SHADOW_DETECTION_FEATURE.md](SHADOW_DETECTION_FEATURE.md) — shadow → adjacent-material inference
- [REFACTORING_SUMMARY.md](REFACTORING_SUMMARY.md) — historical: two-step split
- [FIXES_APPLIED.md](FIXES_APPLIED.md) — historical: PROJ / CRS fixes
- [RASTERIZE_DEBUG.md](RASTERIZE_DEBUG.md) — historical: rasterization debugging notes (Hebrew)

---

## CLI & geocell manifest mode

The CLI drives the same `classify_v6` pipeline as the web app, in three modes:

```powershell
# 1. Single file or folder-batch (auto-implies --mea --sam3-enabled)
.\.venv\Scripts\python.exe cli.py <input.tif|folder> <output.tif|folder>

# 2. Explicit flag form (full control)
.\.venv\Scripts\python.exe cli.py --input in.tif --output out.tif --mea --no-sam3

# 3. Layered-priority geocell mode
.\.venv\Scripts\python.exe cli.py --manifest geocell.toml
```

Folder→folder is **batch mode**: one shared KMeans model is trained across the folder, on
the **kmeans-source classes only** (`BM_VEGETATION`/`BM_SAND`/`BM_SOIL`). Mask-source
classes must never enter the shared model — they have no spectral signature and pollute
cluster assignment.

**Manifest mode** maps one TOML file to one OGC CDB geocell (a 1°-tall cell, e.g.
`N33E035`). It declares priority layers of source imagery instead of a single raster:
sources are discovered (GeoTIFF **and** JPEG 2000), intersected with the cell, composited
into a single EPSG:4326 mosaic (priority last-wins, near-black borders excluded), then
classified. Output is always a folder of georeferenced tiles
(`<cell>_classified_tiles/`) so the deliverable shape is deterministic regardless of the
worker's free RAM. A runnable example ships at
`installer_assets/examples/sample_data/geocell.toml`.

---

## Vector / GIS mask sources

Man-made materials never come from color. `BM_ASPHALT`, `BM_CONCRETE` and `BM_WATER` are
painted from masks, resolved in this priority order:

| Source | Configured by | Notes |
|--------|---------------|-------|
| Water-mask GeoTIFF | `shapefile_config.json` → `water_mask`, or `--water-mask` | Band 1 > 0 = water. Painted directly, no vector resolve. |
| Esri SDE | `shapefile_config.json` → `sde` block | Buildings + roads pulled via an `arcpy` subprocess under ArcGIS Pro's Python. Road LINE features are buffered to polygons using a 3-tier width: explicit attribute → road-type width → fallback. Bounds are split into ≤5 km tiles to dodge the 2 GB shapefile cap. |
| Shapefiles | `shapefile_config.json` → path arrays | `shapefile_resolver.py` intersects envelopes with the raster bounds (50 m buffer), reads only intersecting features with pyogrio, reprojects, unions, writes a temp shp. Shapefiles missing a `.prj` are skipped. |
| SAM3 | `--sam3-enabled` (default on) | Text-prompted segmentation. Falls back to OWLv2 + SAM2 when SAM3 weights are unavailable. |

Masks are **authoritative** — the soft-prior veto in `pipeline.py` is deliberately disabled
(thresholds > 1.0), so a shapefile or SAM3 detection always paints over the KMeans result.

Test an SDE connection without running a classification:

```powershell
.\.venv\Scripts\python.exe sde_conn_test.py
```

---

## Quick Start (web app, dev machine)

```powershell
# One-time setup
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd web_app && npm install && cd ..

# Run (two terminals, or use start_webapp.bat)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend     # backend  → 127.0.0.1:8000
cd web_app && npm run dev                                                         # frontend → 127.0.0.1:5174
```

Or simply:

```bat
start_webapp.bat
```

Browser opens automatically at the Vite URL; it talks to FastAPI at `http://127.0.0.1:8000`
through `/api/...` (rewritten by `_StripApiPrefix` middleware in `main.py`).

---

## Build / Distribution

| Goal | Script |
|------|--------|
| Web app + CLI `.exe`s | `build_exe.bat` → 3 steps: Vite `dist/`, `ClassificationWebApp.exe`, `MaterialClassification_CLI.exe` |
| Inno Setup installer | `build_installer.bat` → `ClassificationApp_Setup.exe` (`ClassificationApp.iss`) |
| Offline payload (USB) | `prepare_offline.bat` → `offline_installer/` + staged `installer_assets/` |

On the target: `Setup.bat` (wizard) then `Post-Install.bat` — the latter patches in the
CLI files, the `shapefile_config.json` template and the SAM3 BPE asset, which the
pre-compiled installer `.exe` predates and doesn't know about.

See [STANDALONE_DEPLOYMENT.md](STANDALONE_DEPLOYMENT.md) for the offline-station workflow.

---

## Notes

- Output GeoTIFFs are saved next to the input raster with a `_classified.tif` suffix and are
  reprojected to **EPSG:4326** before writing.
- Every output gets a companion `<stem>.xml` material table; MEA outputs additionally get
  `.txr` / `.txs` sidecars.
- All AI extraction outputs go into organized `_roads/`, `_buildings/`, `_trees/`,
  `_fields/`, `_water/` subfolders next to the source.
- The web app can group multiple input images and cascade-delete their layers as a unit.
- Tile mode is recommended for any raster > ~5000×5000 pixels; the **Auto** tile-size choice
  queries `/suggest-tile-size` and picks a side that fits in current RAM.
- GPU KMeans is automatic — install `requirements-gpu.txt` (CuPy + nvidia-cuda-* wheels) and
  the engine flips from `faiss-cpu` to `cupy` on next start. The GPU wheels are pinned to
  **CUDA 12.4**; the 12.9 series needs driver ≥ 575 and fails on the target A4000 box.
- **Mirror discipline:** `offline_installer/app/{backend,web_app,shared}` is a byte-identical
  mirror of the dev tree. After editing any mirrored file run
  `.\.venv\Scripts\python.exe tools\sync_mirrors.py` (`--check` to verify without writing).
- Always use `.venv\Scripts\python.exe`. A bare `python` may resolve to a different install
  where `pkg_resources` is gone and the GroundingDINO / `triton-windows` chain breaks.
