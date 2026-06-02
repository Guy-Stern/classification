## graphify

This project is wired to use a graphify knowledge graph at `graphify-out/`, but
that directory is **not present in this checkout** (lost in the May-2026
deletion incident) and the `graphify` tool is not currently installed. For now
there is **no graph to read or update — read the source directly.**

To restore it: install graphify, then run a full `/graphify .` build (that build
has LLM/token cost). Once `graphify-out/` exists again, the original workflow
applies — read `graphify-out/GRAPH_REPORT.md` before architecture questions,
prefer `graphify-out/wiki/index.md` if present, and run `/graphify . --update`
after code changes (incremental, AST-only, no API cost).

## Project: MaterialClassification

Orthophoto material-classification web app. React + TypeScript + Vite frontend + FastAPI backend, plus a CLI that drives the same pipeline. Active branch: `6-Material-Downgrade`.

### Architecture
- **Frontend**: `web_app/` — React + Vite, entry `web_app/src/main.tsx` → `App.tsx`. Vite dev server at `http://127.0.0.1:5174`.
- **Backend**: `backend/app/main.py` — FastAPI on `http://127.0.0.1:8000`. Mounts `web_app/dist/` as static, so visiting `:8000` serves the LAST BUILT frontend (rebuild after src changes, or use the Vite dev server).
- **Core engine**: `backend/app/core.py` (~4500 lines) — KMeans + MEA classification pipeline.
- **Pipeline orchestrator**: `classify_v6` in `backend/app/pipeline.py` — SAM3-first 6-material flow.
- **Shapefile system** (added in phase 6):
  - `backend/app/shapefile_config.py` — load/save `shapefile_config.json` (`{buildings, roads, water}` arrays of paths) next to `app_config.json`.
  - `backend/app/shapefile_resolver.py` — smart-trim resolver: intersects shapefile envelopes with raster bounds (50 m buffer), pyogrio-reads only the intersecting features, reprojects to raster CRS, unions, writes a temp shp. Skips shapefiles missing `.prj`, atexit cleanup of temp dirs.
- **CLI**: `cli.py` (project root) wraps the pipeline; `cli_launcher.py` is a tiny subprocess wrapper so the PyInstaller exe stays ~5 MB instead of bundling torch.
- **Mirror**: `offline_installer/app/{backend,web_app,shared}` is a near-identical mirror of the dev tree (plus the CLI files at `offline_installer/app/`). Kept in sync via `tools/sync_mirrors.py`. **After editing any mirrored file, run `python tools/sync_mirrors.py` (or `--check` in CI).**
- **Tkinter app**: `tkinter_app.py` exists but is **not maintained** — focus on the web app and CLI.

### Key backend endpoints
- `POST /classify` — full pipeline (classify + rasterize vectors)
- `POST /classify-step1` — classification only
- `POST /classify-step2` — vector rasterization on existing classification
- `POST /classify-batch` — shared-model batch classification (the GUI's batch path)
- `POST /suggest-tile-size` — memory-safe tile side length for a raster
- `POST /raster-info`, `POST /raster-as-png`, `POST /list-dir`, `POST /scan-folder`

### MEA classes (6 — current schema)
- **KMeans-source** (color-clustered): `BM_VEGETATION` ([0,100,0]), `BM_SAND` ([230,200,130]), `BM_SOIL` ([85,55,30]).
- **Mask-source** (SAM3 / shapefile): `BM_ASPHALT`, `BM_CONCRETE`, `BM_WATER` (water is shapefile-only).
- Anchors live in `shared/mea_defaults.json`; merged in `core.py:_resolve_anchor_map()` via `mea_profile.load_active_profile()`. Slimmed to 1 anchor per kmeans material — wide spectral separation makes 1-vs-many anchors more robust than overlapping anchor clouds.
- **Removed in earlier phases**: `BM_EARTHEN`, `BM_SHINGLE`, `BM_FOLIAGE`, `BM_LAND_GRASS`, `BM_LAND_DRY_GRASS`, `BM_METAL`, `BM_METAL_STEEL`, `BM_PAINT_ASPHALT`, `BM_ROCK`. Also gone: `_morphological_road_cleanup`, `/remove-road-objects` endpoint.

### XML export
After every MEA classification, `_write_composite_material_xml` writes a `<stem>.xml` next to the output. Format: `<Composite_Material_Table>` with `<Composite_Material index="N">` entries. Colors ARGB (`#ff` + hex). Composite names from `_MEA_COMPOSITE_NAMES` (e.g. `BM_VEGETATION` → `GENVEGETATION`).

### Environment

**ALWAYS use `.venv/Scripts/python.exe` for this project.**

Python 3.11.9 with torch 2.5.1+cu121 (CUDA), samgeo 1.3.2, transformers, all wired.

Bare `python` on this machine resolves to a fresh Python 3.14 install and is **broken for this stack**: `pkg_resources` removed, `triton-windows` broken, GroundingDINO `SLConfig` fails. Don't use it for the backend or test runners.

```
# Start backend (from repo root):
.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# Or from backend/:
cd backend
../.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### CLI usage

```
# Positional form (auto-implies --mea --sam3-enabled, pulls shapefile paths from shapefile_config.json):
cli.py <input.tif|folder> <output.tif|folder>

# Explicit MEA mode:
cli.py --input input.tif --output output.tif --mea

# Useful flags:
--no-sam3                   skip SAM3 mask source
--road-shapefile PATH       override road shapefile
--building-shapefile PATH   override building shapefile
--water-shapefile PATH      override water shapefile
--examples                  show example invocations
```

**MEA batch mode (folder → folder)**: trains the shared KMeans model on the **kmeans-source classes only** (`BM_VEGETATION/BM_SAND/BM_SOIL`) via `_split_classes_by_source` from `backend.app.pipeline`. The mask-source classes (`BM_ASPHALT/BM_CONCRETE/BM_WATER`) get painted per-raster by `classify_v6` from SAM3 masks + shapefiles. **Do not include mask-source classes in the shared KMeans** — they have no useful spectral signature and pollute cluster assignment.

### Installer pipeline

Three-stage deploy: build exes → bundle offline installer → ship to target.

```
# 1. Build the GUI + CLI exes (project root):
build_exe.bat
  └─ [1/3] Vite frontend (web_app/dist/)
     [2/3] ClassificationWebApp.exe via PyInstaller (wraps launcher.py)
     [3/3] MaterialClassification_CLI.exe via PyInstaller (wraps cli_launcher.py)

# 2. Bundle the offline installer (project root):
prepare_offline.bat
  ├─ Copies bundled Python, pip wheels, frontend, backend, models/, HF cache
  ├─ Includes SAM3 weights from models/sam3/ if present
  ├─ GPU step: prompts y/N for cupy + nvidia-* (~800 MB);
  │   uses --retries 10 --timeout 300; VERIFIES all required wheels are present
  │   post-download (cupy + nvidia-cuda-runtime/cublas/nvrtc/curand) and warns
  │   loudly if any are missing. The cublas wheel (~400 MB pinned to 12.4) is
  │   the most common silent drop on flaky connections.
  └─ Stages installer_assets/ContentsAtProjectRoot/ next to offline_installer/:
        ClassificationInstaller.exe (the .NET WinForms wrapper)
        Post-Install.bat           (patches CLI files into the install)
        README.txt                 (end-user guide)
        sam3_runtime/              (BPE tokenizer asset)

# 3. On the target (offline):
Setup.bat  →  Setup.ps1 wizard
  ├─ Locates Python 3.11 (or copies from bundled prerequisites/)
  ├─ Creates .venv, pip-installs core packages, torch, SAM, optional GPU pack
  ├─ Copies app files, HF cache, writes start.bat
  └─ Done.

Post-Install.bat (run after the wizard):
  - Copies cli.py, cli_launcher.py, ClassificationWebApp.exe,
    MaterialClassification_CLI.exe into the install dir.
  - Drops a shapefile_config.json template at the install root.
  - Drops the BPE tokenizer asset into .venv/Lib/site-packages/assets/.
  These are files the .NET wrapper exe doesn't know about because it was
  pre-compiled before the CLI/shapefile features existed.
```

### `installer_assets/` policy

`installer_assets/ClassificationInstaller.exe` is force-added past the `*.exe` gitignore (`git add -f`). The wrapper is a tiny .NET launcher (~33 KB) that opens a folder picker and runs `offline_installer\Setup.bat` for the user — replaces having to explain "double-click Setup.bat inside the offline_installer folder."

### GPU acceleration on the target

`backend/requirements-gpu.txt` is **pinned to CUDA 12.4** runtime wheels:
- `nvidia-cuda-runtime-cu12==12.4.*`
- `nvidia-cublas-cu12==12.4.*`
- `nvidia-cuda-nvrtc-cu12==12.4.*`
- `cupy-cuda12x` and `nvidia-curand-cu12` unpinned (cupy works with any 12.x; curand is loose).

Why pinned: unpinned, pip pulls the **12.9.x** series, which requires NVIDIA driver ≥ 575. The target A4000 PC has a driver that reports "CUDA Version: 12.4" in `nvidia-smi` (driver ~550), so 12.9 wheels fail at runtime with `cudaErrorInsufficientDriver` and `_probe_acceleration` in `core.py` silently falls back to sklearn CPU.

12.4 wheels are backward-compatible with newer drivers (12.5, 12.6, 12.9, 13.x), so this is strictly safer for deployment.

The acceleration fallback chain in `core.py:_probe_acceleration`:
```
faiss-gpu > cupy > faiss-cpu > cuml > sklearn
```
A missing or broken cupy is a warning, not a hard failure — the pipeline still runs on CPU.

### Behavioral guidance

(Inherited from `../CLAUDE.md` — the parent `gs/MC/CLAUDE.md`.)

- **Think before coding.** State assumptions; ask if uncertain; present alternatives instead of picking silently.
- **Simplicity first.** Minimum code to solve the problem. No speculative abstractions, no features beyond what was asked.
- **Surgical changes.** Touch only what you must. Don't refactor adjacent code, don't fix unrelated formatting, match existing style.
- **Mirror discipline.** Backend Python and shared assets exist twice: once at `backend/`/`shared/`/etc., and once mirrored under `offline_installer/app/`. After editing either side, run `python tools/sync_mirrors.py` so the mirror stays byte-identical. CI uses `--check`.
- **`graphify update .`** after touching code so the knowledge graph stays current.

### User preferences

- Focus on the web app and CLI (React + Vite + FastAPI). Tkinter app is unmaintained.
- Aggressive dead-code removal preferred over backwards-compat shims.
- Tile size auto = `suggest_tile_size` based on RAM.

### Recovery caches (from the May-2026 deletion incident)

The original project lived at `C:\Users\B\Desktop\ofek\Classification-master` and was deleted before all work was pushed. Three caches that survived are useful cross-references when investigating file history:

- `C:\Users\B\Desktop\workinginstaller2\offline_installer\app\` — full source mirror as of May 10 night (includes shapefile_config.py, shapefile_resolver.py, cli.py with --mea, etc.).
- `C:\Users\B\Desktop\LatestInstaller\offline_installer\` — full offline installer package; `offline_packages_gpu/` was patched with the CUDA 12.4 wheel set in May 2026 (~570 MB total, all 7 wheels present including the previously missing nvidia-cublas-cu12).
- `C:\ClassificationApp\` — the live install on this dev machine; has the freshest cli.py from the May 10 afternoon MEA-batch fix.
- `.recovery/may{10am,10pm,13}_edits.txt` — chronological dumps of unpushed Edit/Write ops extracted from session JSONLs. Untracked (intentionally) — keep them around as breadcrumbs but they're not for editing.

### Commit conventions

The branch uses `phase N:` for major MEA epoch commits (phases 1-6) and conventional prefixes (`fix:`, `feat:`, `chore:`, `docs:`) for everything else. Co-author trailer is included on AI-assisted commits.

### Recent history (May 2026)

- `phase 6: offline installer + CLI MEA mode + delivery prep` — installer wrapper, shapefile system, CLI rewrite, HF offline hardening, SAM3.1 path flexibility, 3-step build.
- `fix: train MEA shared model on kmeans-source classes only (CLI batch)` — fixes batch outputs that mis-classified most pixels as water because mask-source classes were polluting the shared KMeans.
- `fix: drop pow-2 padding — eliminate black-L strip on output rasters` — outputs were padded from 10240 → 16384 because of a stale GeoSpecific texture-engine assumption.
- `fix(prepare_offline): verify all GPU wheels downloaded + retries` — silent-failure protection for the GPU bundling step.
- `fix(gpu): pin CUDA runtime wheels to 12.4 for A4000 / older-driver compat` — replaces 12.9.x wheels that require driver ≥575.
