# Running the Project — Operational Guide

This file is the day-to-day "how do I start things up and what's broken" reference. If this
is your first day, read [ONBOARDING.md](ONBOARDING.md) first. For architecture and feature
description, read [README.md](README.md).

There are four runnable entry points. Most users only need #1 or #2.

| # | App | When to use |
|---|-----|-------------|
| 1 | **Web app** (FastAPI + React) — primary | Interactive work: pick a raster, tune materials, watch progress |
| 2 | **CLI** (`cli.py`) | Scripted runs, folder batches, geocell/manifest automation |
| 3 | **MEA Calibration Tool** (separate FastAPI + React) | Sampling material reference colors before a classification run |
| 4 | **Tkinter app** (legacy) | **Unmaintained.** Kept for history only |

> **Always use `.venv\Scripts\python.exe`.** A bare `python` may resolve to a different
> install on this machine where `pkg_resources` is gone and the GroundingDINO /
> `triton-windows` chain breaks. Commands below spell out the interpreter deliberately.

---

## 1. Web App

### Prerequisites
- Python 3.11+ (3.11.9 is what the offline installer ships)
- Node.js 16+ and npm
- Optional: NVIDIA GPU with CUDA 11.x or 12.x drivers (CuPy)

### One-click

```bat
start_webapp.bat
```

Starts uvicorn (backend) and `npm run dev` (frontend) in two terminals and opens the browser.

### Manual

Terminal 1 — backend:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
# Optional: .\.venv\Scripts\python.exe -m pip install -r backend\requirements-gpu.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Backend is at `http://127.0.0.1:8000`.
- Swagger UI: `http://127.0.0.1:8000/docs`
- All endpoints are summarised in [docs/API_REFERENCE.md](docs/API_REFERENCE.md).

Terminal 2 — frontend:

```powershell
cd web_app
npm install
npm run dev
```

Vite serves at **`http://127.0.0.1:5174`** (set in `web_app/vite.config.ts`) and proxies
`/api/*` to the backend. The backend's `_StripApiPrefix` middleware strips the `/api` so the
same client also works against a packaged `dist/` build served from FastAPI directly.

> **Gotcha:** FastAPI mounts `web_app/dist/` as static, so opening `:8000` serves the
> **last built** frontend, not your live edits. During development use 5174.

### Production build of the frontend

```powershell
cd web_app
npm run build      # writes web_app/dist/
```

Once `dist/` exists, the FastAPI server mounts it as static files — open `http://127.0.0.1:8000`
directly, no Vite required.

---

## 2. CLI

Same pipeline, no browser. Full reference: [docs/CLI_GUIDE.md](docs/CLI_GUIDE.md), or
`cli.py --examples` for the embedded guide.

```powershell
# Simple positional form — auto-implies --mea --sam3-enabled,
# reads SDE + water_mask config from shapefile_config.json
.\.venv\Scripts\python.exe cli.py C:\images\ortho.tif C:\out\ortho_classified.tif

# Folder -> folder = batch mode (one shared KMeans model across the folder)
.\.venv\Scripts\python.exe cli.py C:\images\ C:\out\

# Geocell / manifest mode
.\.venv\Scripts\python.exe cli.py --manifest C:\cfg\geocell.toml

# Runnable example that ships with the repo
.\.venv\Scripts\python.exe cli.py --manifest installer_assets\examples\sample_data\geocell.toml
```

Useful flags: `--no-sam3` (KMeans naturals only — needs no PyTorch/GPU),
`--water-mask PATH`, `--tiling`, `--tile-size PX`, `--workers N`, `--detect-shadows`.

Test an SDE connection independently of a classification run:

```powershell
.\.venv\Scripts\python.exe sde_conn_test.py
```

---

## 3. MEA Calibration Tool

Independent app — uses its own FastAPI backend on a different port and writes the calibration
profile to `%ProgramData%\MaterialClassification\mea_calibration_profile.json`. The main app
picks it up automatically via [backend/app/mea_profile.py](backend/app/mea_profile.py).

```powershell
cd mea_calibration_tool
python -m uvicorn app.main:app --app-dir backend --port 8100
cd web_app && npm install && npm run dev
```

Or use [mea_calibration_tool/launcher.py](mea_calibration_tool/launcher.py) (and the
`launcher.py` shipped inside the offline installer package) which boots both processes.

See [docs/MEA_CALIBRATION_TOOL.md](docs/MEA_CALIBRATION_TOOL.md) for the workflow.

---

## 4. Tkinter App (legacy — unmaintained)

```powershell
.\.venv\Scripts\python.exe tkinter_app.py
```

No backend / Node required. It has not tracked the phase 4–6 MEA changes; do not use it as
a reference for current behaviour.

---

## Running the tests

`pytest` is **not** in `backend/requirements.txt` — install it first:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Before opening a PR, also verify the mirrors:

```powershell
.\.venv\Scripts\python.exe tools\sync_mirrors.py --check
```

There is no CI (`.github/` has no workflows), so nothing runs either of these for you.

---

## AI Feature Extraction Setup

`extract-features` (roads/buildings/trees/fields/water) and `extract-roads` need
HuggingFace model weights cached locally:

| Model | Purpose | Approx size |
|-------|---------|-------------|
| `google/owlv2-base-patch16-ensemble` | Open-vocabulary detection | 593 MB |
| `facebook/sam2-hiera-large` | SAM 2 mask refinement | 857 MB |
| `facebook/sam3` | SAM 3 (preferred when available) | 3.3 GB |
| `ShilongLiu/GroundingDINO` | LangSAM fallback | ~300 MB |

The first time the backend runs they download to `~\.cache\huggingface\hub\`. On offline
stations, copy the `hub\` folder to `<install dir>\models\hf_cache\hub\` — the launcher sets
`HF_HUB_OFFLINE=1` and `HF_HOME` so the models resolve from local snapshots.

Triton (used by SAM3) is mocked on Windows when `triton-windows` isn't available, so OWLv2+SAM2
remains usable as a fallback.

---

## GPU vs CPU

`_probe_acceleration()` in `backend/app/core.py` walks a fixed priority list:

```
faiss-gpu  →  cupy  →  faiss-cpu  →  cuml  →  sklearn
(conda)      (pip)     (pip)         (WSL2)   (always)
```

The selected engine prints a `[KMeans] engine=… gpu=…` banner at import and shows on the
status bar. Install `backend/requirements-gpu.txt` to enable CuPy. A missing or broken CuPy
is a **warning, not a failure** — the pipeline just runs on CPU.

Check which engine you got without running a classification:

```powershell
.\.venv\Scripts\python.exe -c "from backend.app import core; print(core._ACCEL_ENGINE)"
```

The GPU wheels are pinned to **CUDA 12.4**. Unpinned, pip pulls the 12.9 series, which
needs NVIDIA driver ≥ 575 — the target A4000 box reports CUDA 12.4 (driver ~550) and 12.9
wheels fail at runtime with `cudaErrorInsufficientDriver`, silently dropping to CPU.

See [docs/GPU_ACCELERATION.md](docs/GPU_ACCELERATION.md) for installation specifics.

---

## Tile Processing

Big rasters (> ~5000×5000) blow up memory. Enable **Tile Processing** in the sidebar's
*Performance* panel.

- **Auto** queries `POST /suggest-tile-size` and picks a side length that fits comfortably
  in current available RAM.
- Sizes that wouldn't fit are hidden from the dropdown.
- Tile workers are limited by `tile_workers` in `PerformanceSection`; you can also cap the
  total via the **Limit max threads** option (`max_threads` parameter).
- **Manifest/geocell mode always tiles**, at 512 px, regardless of these settings — the
  output shape must be deterministic for the downstream consumer.

Note that the *tile size* is power-of-2, but the output raster is **not** padded to
power-of-2 dimensions (that behaviour was removed in `fd055ff`; it produced a black
L-shaped strip on every output).

---

## Building Distributables

Three stages: build exes → bundle offline payload → install on the target.

| Output | Script | Spec |
|--------|--------|------|
| `web_app/dist/` + `ClassificationWebApp.exe` + `MaterialClassification_CLI.exe` | `build_exe.bat` (3 steps) | `WebApp.spec`, `MaterialClassification_CLI.spec` |
| `ClassificationApp_Setup.exe` (Inno Setup) | `build_installer.bat` | `ClassificationApp.iss` |
| `offline_installer/` payload (USB) | `prepare_offline.bat` | `offline_installer/Setup.ps1` |

`MaterialClassification_CLI.exe` wraps `cli_launcher.py`, a thin subprocess shim that runs
the on-disk `cli.py` in the install's `.venv` — that keeps the exe ~5 MB instead of
bundling torch, and means a `cli.py` patch takes effect without rebuilding the exe.

`prepare_offline.bat` prompts y/N for the GPU pack (~800 MB) and **verifies every required
wheel is present afterwards** — the ~400 MB `nvidia-cublas-cu12` wheel is the most common
silent drop on a flaky connection.

On the target: `Setup.bat` (wizard) then **`Post-Install.bat`**, which patches in `cli.py`,
`cli_launcher.py`, both exes, the `shapefile_config.json` template and the SAM3 BPE
tokenizer asset — files the pre-compiled installer `.exe` predates.

The PyInstaller specs (`WebApp.spec`, `ClassificationApp.spec`,
`MaterialClassification_CLI.spec`, `ClassificationWebApp.spec`) include CuPy CUDA DLLs via
the runtime hook so the frozen EXE keeps GPU acceleration.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|-------------------|
| Blank page in browser | Frontend not built — `cd web_app && npm run build` |
| `ModuleNotFoundError: uvicorn` | `pip install -r backend/requirements.txt` |
| Backend port 8000 already in use | `netstat -ano \| findstr :8000` and kill the PID |
| Frontend can't reach `/api/...` | Confirm backend is on 127.0.0.1:8000; check `vite.config.ts` proxy |
| `ERROR 1: PROJ: proj_identify: Cannot find proj.db` | core.py runs `_setup_proj_lib()` — the venv must contain `pyproj`'s data; reinstall `pyproj` |
| GPU not used | `backend/requirements-gpu.txt` not installed, or NVIDIA drivers missing on the target |
| AI extraction fails | Model weights missing or `triton` import error — see *AI Feature Extraction Setup* above |
| `GeoSeries already has a CRS …` | Old tip — `set_crs(..., allow_override=True)` is now used everywhere |
| Vector overlay produces 0 pixels | CRS/transform mismatch — see [RASTERIZE_DEBUG.md](RASTERIZE_DEBUG.md) |
| `Set-ExecutionPolicy` blocks venv activation | `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| `OutOfMemory` on large image | Enable Tile Processing; pick a smaller tile size from the dropdown |
| `pkg_resources` / `SLConfig` errors | You're on the wrong interpreter — use `.venv\Scripts\python.exe` |
| Batch output is almost entirely one material | Mask-source classes leaked into the shared KMeans. See `_split_classes_by_source` in `pipeline.py` |
| `FAIL: mosaic build failed` in manifest mode | Full-frame mosaic ran out of RAM. Known Bug 4 — use coarser input GSD, or the windowed rewrite on PR #4 |
| `FAIL: no source intersects geocell` | Layer `folder`/`glob` wrong, or `[geocell]` coords don't cover the imagery |
| Manifest rejected with "unknown table" | By design — every manifest model sets `extra="forbid"`. Check for a typo'd table name |
| Frontend changes don't show up | You're on `:8000` (static `dist/`), not `:5174` (Vite dev) |
| Installed app behaves differently from dev | Mirror drift — run `tools\sync_mirrors.py --check` |

---

## Stopping Services

- Backend / frontend / tkinter app: `Ctrl+C` in the terminal that owns it.
- `start_webapp.bat`: close both spawned terminals.
- Frozen EXE: close the launched browser tab and the console window.
