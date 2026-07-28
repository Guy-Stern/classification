# Onboarding — Material Classification

**Read this first.** It is the day-1 path from "I just cloned the repo" to "I understand
the pipeline and can make a change safely." Everything else in the repo is reference
material you reach for later; this file is the map.

- Visual companion (diagrams, print/share friendly): **[docs/ONBOARDING.pdf](docs/ONBOARDING.pdf)**
- High-level feature overview: [README.md](README.md)
- Day-to-day commands: [RUNNING_GUIDE.md](RUNNING_GUIDE.md)
- Current branches / open work: [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)

Last verified against `main` @ `5ce6d4f` (2026-07-27).

---

## 1. What this project does — in 60 seconds

You give it an **orthophoto** (a top-down, georeferenced aerial/satellite image). It gives
you back a **material map**: the same image, but every pixel recolored to say what it is
made of — asphalt, concrete, vegetation, water, sand, soil.

That output feeds a simulation/terrain toolchain, so alongside the GeoTIFF it writes a
sidecar **`<stem>.xml` material table** (`<Composite_Material_Table>`, ARGB colors) plus
`.txr`/`.txs` files the simulator reads.

```
   input ortho (.tif/.jp2)                  output
   ┌───────────────────┐        ┌──────────────────────────────┐
   │   real imagery    │  ───►  │ classified.tif  (6 flat colors) │
   │  roofs, roads,    │        │ classified.xml  (material table)│
   │  trees, river     │        │ classified.txr / .txs (sim)     │
   └───────────────────┘        └──────────────────────────────┘
```

The interesting engineering problem: **color alone cannot tell you what a material is.**
A grey roof and a grey road are the same pixels. So the pipeline uses two different
sources of truth and fuses them — that is the core idea of the whole codebase (§3).

---

## 2. Day 1 — get it running

### 2.1 Prerequisites

| Need | Version | Note |
|------|---------|------|
| Python | **3.11.x** | 3.11.9 is what ships in the installer. **Not 3.12+, not 3.14.** |
| Node.js | 16+ | Frontend only. Skip if you only care about the CLI. |
| NVIDIA GPU | optional | Speeds up KMeans. Everything works CPU-only. |

> **The single most important environment rule:** always use
> **`.venv/Scripts/python.exe`**. A bare `python` on a dev box here may resolve to a
> different install where `pkg_resources` is gone and GroundingDINO/`triton-windows`
> break. Every command below spells out the interpreter on purpose.

### 2.2 Setup

```bash
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
```

Optional GPU pack (CuPy + CUDA 12.4-pinned runtime wheels — see
[docs/GPU_ACCELERATION.md](docs/GPU_ACCELERATION.md)):

```bash
.venv/Scripts/python.exe -m pip install -r backend/requirements-gpu.txt
```

Frontend (only if you want the web UI):

```bash
cd web_app && npm install
```

### 2.3 Verify it works

Fastest smoke test — import the engine and see which compute backend it picked:

```bash
.venv/Scripts/python.exe -c "from backend.app import core; print(core._ACCEL_ENGINE)"
```

You should see one of `faiss-gpu` / `cupy` / `faiss-cpu` / `cuml` / `sklearn` printed,
preceded by a `[KMeans] engine=… gpu=…` banner. `faiss-cpu` or `sklearn` is a perfectly
fine result — it just means no GPU pack.

Then run the CLI's built-in guide (no imagery needed, no models loaded):

```bash
.venv/Scripts/python.exe cli.py --examples
```

Then the real thing, on the sample data that ships in the repo:

```bash
.venv/Scripts/python.exe cli.py --manifest installer_assets/examples/sample_data/geocell.toml
```

### 2.4 Run the web app

Two terminals (or just `start_webapp.bat`):

```bash
.venv/Scripts/python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

```bash
cd web_app && npm run dev
```

- Backend → `http://127.0.0.1:8000` (Swagger at `/docs`)
- Frontend → `http://127.0.0.1:5174`

> **Gotcha:** FastAPI also serves `web_app/dist/` as static at `:8000`. If you open
> `:8000` in a browser you get the **last built** frontend, not your live edits. During
> development use the Vite server on **5174**, or re-run `npm run build`.

---

## 3. The mental model — read this before the code

### 3.1 Six materials, two sources

```
BM_VEGETATION  ┐
BM_SAND        ├─ source = "kmeans"  → color clustering on the raster's RGB
BM_SOIL        ┘

BM_ASPHALT     ┐
BM_CONCRETE    ├─ source = "mask"    → SAM3 segmentation / SDE / shapefile / raster mask
BM_WATER       ┘
```

**Why the split.** Natural ground cover separates cleanly in color space — green is
vegetation, pale yellow is sand, brown is soil. Built surfaces do not: asphalt, a grey
roof and a shadow are spectrally identical. So man-made classes never come from color.
They come from a **mask** — either an AI segmentation (SAM3), an authoritative GIS source
(Esri SDE / shapefile), or a pre-rendered raster mask (water).

This one distinction explains most of the architecture. Two consequences you *will* hit:

- **Never put mask-source classes into a shared KMeans model.** They have no useful
  spectral signature and they poison cluster assignment for everything else. This was a
  real production bug (commit `240c4ca`): batch outputs came back almost entirely water.
  `_split_classes_by_source()` in [backend/app/pipeline.py](backend/app/pipeline.py)
  exists solely to enforce this.
- **Masks are authoritative and always paint.** A soft-prior "veto" system exists in
  `pipeline.py` but its thresholds are deliberately set above 1.0, which disables it. If a
  shapefile says a polygon is a building, it is painted concrete even if the pixels look
  green.

Anchor colors live in [shared/mea_defaults.json](shared/mea_defaults.json) — **one anchor
per kmeans material** (vegetation `[0,100,0]`, sand `[230,200,130]`, soil `[85,55,30]`).
That is intentional: wide spectral separation makes a single anchor more robust than
overlapping anchor clouds. A user can override them with the MEA Calibration Tool, which
writes `%ProgramData%\MaterialClassification\mea_calibration_profile.json`.

### 3.2 The `classify_v6` flow

`classify_v6()` in [backend/app/pipeline.py](backend/app/pipeline.py) is the orchestrator
every entry point funnels into. Four phases:

```
                  ┌─────────────────────────────────────────────┐
  raster.tif ───► │ 1. ACQUIRE MASKS                            │
                  │    water  ← water_mask GeoTIFF (band1 > 0)  │
                  │    roads  ← SDE (buffered to polys) / SAM3  │
                  │    bldgs  ← SDE / SAM3                      │
                  └──────────────────┬──────────────────────────┘
                                     │
                  ┌──────────────────▼──────────────────────────┐
  raster.tif ───► │ 2. KMEANS on the 3 kmeans-source classes    │
                  │    core.classify_and_export()               │
                  │    Hungarian 1:1 cluster→material assignment│
                  └──────────────────┬──────────────────────────┘
                                     │
                  ┌──────────────────▼──────────────────────────┐
                  │ 3. FUSE: paint water → roads → buildings    │
                  │    (later masks win on overlap)             │
                  └──────────────────┬──────────────────────────┘
                                     │
                  ┌──────────────────▼──────────────────────────┐
                  │ 4. REWRITE XML so all 6 materials appear    │
                  │    (even though only 3 came from KMeans)    │
                  └──────────────────┬──────────────────────────┘
                                     ▼
                          classified.tif + .xml + .txr/.txs
```

Phase 2 uses **Hungarian assignment** (strict 1:1 cluster→material) rather than
nearest-anchor, so one material can never absorb multiple clusters and starve the others.

### 3.3 Tiling

Big rasters do not fit in RAM. `core.py` splits the input into tiles, classifies them in
a worker pool, and writes either a stitched raster or a folder of georeferenced tiles.

- `suggest_tile_size()` picks the largest power-of-2 side from {256…4096} that fits in
  currently-available RAM. The web UI's **Auto** setting calls `POST /suggest-tile-size`.
- Manifest/geocell mode (§4.3) **always** forces tiled output at 512 px so the deliverable
  shape is deterministic regardless of the worker's free RAM.

> Historical note: outputs used to be padded to power-of-2 *dimensions* for an old texture
> engine. That is gone (commit `fd055ff`) — it produced a black L-shaped strip on every
> output. Only the *tile size* is power-of-2 now, never the raster.

---

## 4. The four ways to run it

| # | Entry point | Who uses it | Start reading at |
|---|-------------|-------------|------------------|
| 1 | **Web app** | Interactive operators | `backend/app/main.py` + `web_app/src/App.tsx` |
| 2 | **CLI — file/folder** | Scripted batch runs | `cli.py::main` |
| 3 | **CLI — manifest** | JARVIS geocell automation | `cli.py::run_manifest` |
| 4 | **MEA Calibration Tool** | Tuning reference colors | `mea_calibration_tool/` |

`tkinter_app.py` also exists. It is **unmaintained** — do not spend time on it.

### 4.1 Web app

React + Vite frontend talks to FastAPI. The client always calls `/api/...`; a
`_StripApiPrefix` middleware in `main.py` strips the prefix so the *same* client code works
against both the Vite dev proxy and the packaged static build. Endpoints are catalogued in
[docs/API_REFERENCE.md](docs/API_REFERENCE.md).

### 4.2 CLI — positional form

```bash
.venv/Scripts/python.exe cli.py <input.tif|folder> <output.tif|folder>
```

The positional form auto-implies `--mea --sam3-enabled` and reads SDE/water config from
`shapefile_config.json`. Folder→folder is **batch mode**: one shared KMeans model is
trained across the whole folder (on kmeans-source classes only) so tile boundaries do not
show as color discontinuities. Full reference: [docs/CLI_GUIDE.md](docs/CLI_GUIDE.md).

### 4.3 CLI — manifest / geocell mode

This is the newest and least obvious mode, added mid-2026 for the JARVIS automation.

```bash
.venv/Scripts/python.exe cli.py --manifest geocell.toml
```

One TOML = one **CDB geocell** (a 1°-tall map cell, e.g. `N33E035`) = one classification
run. Instead of a single input raster you declare **priority layers** of source imagery:

```toml
[geocell]
south_lat = 33
west_lon  = 35

[[layers]]
folder   = "D:/orthos/2024_campaign"
priority = 1              # 1 = highest, wins on overlap (QGIS/Photoshop ordering)

[[layers]]
folder   = "D:/orthos/archive_2019"
priority = 2

[classify]                # optional
sam3 = false              # KMeans naturals only — needs no PyTorch/GPU

[output]
path      = "D:/out/N33E035.tif"
overwrite = false
```

Pipeline: discover sources (GeoTIFF **and** JPEG 2000) → intersect with the cell →
composite them into one EPSG:4326 mosaic (priority last-wins, near-black borders excluded)
→ hand that mosaic to `classify_v6` → write `N33E035_classified_tiles/`.

Code path, in order:
[manifest.py](backend/app/manifest.py) (schema) → [geocell.py](backend/app/geocell.py)
(CDB cell math) → [mosaic_catalog.py](backend/app/mosaic_catalog.py) (discovery/filtering)
→ [mosaic_builder.py](backend/app/mosaic_builder.py) (compositing) →
[pipeline.py](backend/app/pipeline.py).

Working example, ready to run: `installer_assets/examples/sample_data/geocell.toml`.

---

## 5. Where the code lives — a reading order

Do not start with `core.py`. It is ~4,500 lines and it is the *implementation*, not the
*design*. Read in this order:

| Order | File | Lines | Why |
|-------|------|-------|-----|
| 1 | [backend/app/pipeline.py](backend/app/pipeline.py) | 765 | The orchestrator. Best single file for understanding the product. |
| 2 | [cli.py](cli.py) | 955 | Every mode's entry logic, plus the embedded user guide. |
| 3 | [backend/app/main.py](backend/app/main.py) | 1,334 | The HTTP surface + progress/cancel plumbing. |
| 4 | [backend/app/manifest.py](backend/app/manifest.py) | 255 | Small, well-commented, shows the geocell design. |
| 5 | [backend/app/shapefile_resolver.py](backend/app/shapefile_resolver.py) | 371 | The "smart-trim" GIS resolver — a genuinely clever bit. |
| 6 | [backend/app/core.py](backend/app/core.py) | 4,538 | Reference only. Grep it; don't read it front to back. |

Everything else:

```
backend/app/
  core.py                KMeans engine, tiling, MEA export, XML/.txr/.txs writers
  pipeline.py            classify_v6 — the SAM3-first 6-material orchestrator
  main.py                FastAPI endpoints, SSE progress, cancellation
  road_extraction.py     OWLv2 + SAM2/SAM3 text-prompted segmentation, FEATURE_CONFIGS
  manifest.py            TOML geocell manifest schema (pydantic)
  geocell.py             OGC CDB geocell geometry (pure math, no deps)
  mosaic_catalog.py      Source discovery + geocell intersection
  mosaic_builder.py      Priority last-wins mosaic → one EPSG:4326 GeoTIFF
  shapefile_config.py    Loads shapefile_config.json (water_mask + sde block)
  shapefile_resolver.py  Envelope-intersect, trim, reproject, union → temp shp
  sde_extractor.py       Esri SDE extraction orchestrator (spawns arcpy worker)
  sde_arcpy_worker.py    Runs under ArcGIS Pro's Python; the only arcpy importer
  sde_probe_worker.py    Connection/layer probe for sde_conn_test.py
  config.py              Persistent app_config.json
  mea_profile.py         Reads the MEA calibration profile

web_app/src/
  App.tsx                Root component
  api/client.ts          FastAPI client + SSE progress
  store/index.tsx        Reducer — single source of UI truth
  constants/mea.ts       Frontend copy of the 6-material schema
  components/sidebar/    Input, Materials, Features, Vectors, Performance,
                         Classification, Actions, Settings

shared/                  Cross-app constants (mea_defaults.json = the anchors)
tools/sync_mirrors.py    Mirror discipline enforcement (see §6.1)
offline_installer/       Air-gapped install payload + a full mirror of the app tree
installer_assets/        Files the pre-compiled installer .exe doesn't know about
docs/                    Topic deep-dives
```

---

## 6. Repo rules that will bite you

### 6.1 Mirror discipline — the #1 way to break this repo

`offline_installer/app/{backend,web_app,shared}` is a **byte-identical mirror** of the dev
tree, because the offline installer ships pre-built app files. Editing one side and not the
other means the installed product silently differs from the code you tested.

**After editing any mirrored file:**

```bash
.venv/Scripts/python.exe tools/sync_mirrors.py
```

To check without writing (this is what CI *would* run — see §7.3):

```bash
.venv/Scripts/python.exe tools/sync_mirrors.py --check
```

`mea_calibration_tool/` is a third mirror of some shared frontend files. `sync_mirrors.py`
handles all of them; you never need to copy by hand.

### 6.2 Style expectations

From [CLAUDE.md](CLAUDE.md), and they are enforced in review:

- **Think before coding.** State assumptions; ask rather than silently pick.
- **Simplicity first.** Minimum code that solves the problem. No speculative abstractions.
- **Surgical changes.** Touch only what you must. No drive-by refactors, no reformatting
  adjacent code. Match the surrounding style.
- **Aggressive dead-code removal** is preferred over backwards-compat shims. When a class
  or endpoint is dropped, it is deleted, not deprecated.

### 6.3 Commits

`phase N:` for the major MEA epoch commits (phases 1–6, historical). Conventional prefixes
for everything else: `feat:`, `fix:`, `docs:`, `chore:`, `perf:`, `test:`, `bench:`.
Scopes in use: `(manifest)`, `(installer)`, `(cli)`, `(batch)`, `(sde)`, `(gpu)`,
`(mosaic)`, `(prepare_offline)`.

Work goes on a branch → PR → merge to `main`. All three merged PRs so far followed that.

### 6.4 Things that are *supposed* to look wrong

- **`shared/mea_classes.json` lists 13 classes.** It is a leftover from the pre-phase-4
  schema and **no code reads it** — the live schema is `MEA_CLASSES` in `core.py` plus
  `shared/mea_defaults.json`. Don't "fix" it by wiring it back in.
- **`web_app/src/constants/mea.ts` marks `BM_WATER` as `source: "kmeans"`** while the
  backend calls it `"mask"`. The frontend field is only used for calibration-UI labelling,
  so it is cosmetic — but it is wrong, and worth a one-line fix if you are in there.
- **Deleted wheels in `git status`.** `offline_installer/offline_packages*/` shows a few
  deleted `.whl` files. Those are the CUDA 12.9 wheels and a stray CPU torchvision that
  were **deliberately quarantined** (they break the target A4000 box). Don't restore them,
  and don't commit the deletion casually either — see [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md).
- **`graphify-out/` is referenced but absent.** The knowledge graph was lost in the
  May-2026 deletion incident and the tool isn't installed. Read the source directly.

---

## 7. Current state (2026-07-27)

### 7.1 What's on `main`

`main` @ `5ce6d4f`. Three PRs merged, all in the manifest/geocell area:

| PR | What | Merged |
|----|------|--------|
| [#1](https://github.com/Guy-Stern/classification/pull/1) | JPEG 2000 + GeoTIFF layered geocell manifest; installer usage examples | 2026-07-09 |
| [#2](https://github.com/Guy-Stern/classification/pull/2) | Optional `[classify]` table (per-run SAM3/water knobs) + tiled geocell output | 2026-07-12 |
| [#3](https://github.com/Guy-Stern/classification/pull/3) | Deterministic tile names + overwrite guard/clean for tiled output | 2026-07-13 |

### 7.2 Open work

**One open PR: [#4 — JARVIS-managed silent installer](https://github.com/Guy-Stern/classification/pull/4)**
(`feat/silent-managed-installer`, tip `f10889d`, opened 2026-07-16). It is a clean
fast-forward ahead of `main` — 9 commits, ~3.7k added lines, and it carries **two unrelated
bodies of work**:

1. **Silent installer** — `installer_silent/`, `publish.ps1`/`publish.bat`,
   `docs/AIRGAP_A4000_DEPLOY.md`. A headless installer for the JARVIS Workshop.
2. **Tier 0–2 mosaic performance overhaul** — windowed/parallel/area-average geocell join,
   overview-pinned reads, footprint cache, resumable tiled mosaic cache with VRT handoff,
   plus `benchmarks/`. **This is the fix for known Bug 4** (§7.3).

Known open bugs are tracked in
[docs/MC_MANIFEST_BUGS_2026-07-12.md](docs/MC_MANIFEST_BUGS_2026-07-12.md). Bugs 1 and 2
are fixed on `main`; **Bug 3** (per-tile XML material tables aren't unified across a cell)
and **Bug 4** (full-frame in-RAM mosaic → OOM on constrained boxes) are open, with Bug 4's
fix sitting on PR #4.

Full detail — every branch, what's stale, what's a deploy artifact:
**[docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)**.

### 7.3 Testing reality check

`backend/tests/` holds 9 pytest modules, 84 test functions, covering manifest, geocell,
mosaic catalog/builder, MEA v6, batch hardening and road-width resolution. **`pytest` is
not installed in `.venv`** — install it before you try to run them:

```bash
.venv/Scripts/python.exe -m pip install pytest
.venv/Scripts/python.exe -m pytest backend/tests -q
```

There is also **no CI**: `.github/` contains only `copilot-instructions.md`, no workflows.
`sync_mirrors.py --check` and the test suite are documented as "CI mode" but nothing runs
them automatically. Run them yourself before opening a PR.

---

## 8. Cookbook

**Add a new MEA material.** Edit `MEA_CLASSES` and `_MEA_COMPOSITE_NAMES` in
`backend/app/core.py`, add anchors to `shared/mea_defaults.json`, add the entry to
`web_app/src/constants/mea.ts`, then `sync_mirrors.py`. Decide `source` first — `kmeans`
means you must supply anchor colors that separate cleanly from existing ones; `mask` means
you must supply a mask source in `pipeline.py::_acquire_mask`.

**Add a new AI-extractable feature.** Add an entry to `FEATURE_CONFIGS` in
`backend/app/road_extraction.py` (prompts, merge color, optional `color_detect` callback
and `threshold`), then a checkbox in `web_app/src/components/sidebar/FeaturesSection.tsx`.

**Add a CLI flag.** `cli.py::main` for the `argparse` entry, and `EXAMPLES_TEXT` at the top
of the same file for the user-facing docs (`--examples` prints it). Keep them in sync — the
embedded guide is what operators actually read.

**Change the geocell manifest schema.** `backend/app/manifest.py`. Note `extra="forbid"` on
every model: an unknown table is a hard error by design, so adding a field is a breaking
change for nobody but removing one is.

**Debug "0 pixels rasterized."** [RASTERIZE_DEBUG.md](RASTERIZE_DEBUG.md) (Hebrew) — it is
almost always a CRS/transform mismatch.

**Rebuild the CLI guide PDF.** `.venv/Scripts/python.exe tools/build_cli_guide_pdf.py`.
Same for this onboarding doc: `tools/build_onboarding_pdf.py`.

**Ship a build.** `build_exe.bat` (frontend + 2 exes) → `prepare_offline.bat` (USB payload)
→ on the target, `Setup.bat` then `Post-Install.bat`. Details in
[STANDALONE_DEPLOYMENT.md](STANDALONE_DEPLOYMENT.md).

---

## 9. Glossary

| Term | Meaning |
|------|---------|
| **MEA** | The material schema this project targets. 6 classes, `BM_*` names. |
| **Orthophoto** | Top-down georeferenced aerial imagery, geometrically corrected so distances are true. |
| **Geocell** | OGC CDB addressing unit: a 1°-tall cell whose longitude width widens toward the poles. Named `N33E035`. |
| **CDB** | OGC Common DataBase — the simulation terrain standard the output feeds. |
| **GSD** | Ground Sample Distance — real-world size of one pixel. |
| **SAM3** | Meta's Segment Anything 3. Text-prompted segmentation; supplies road/building masks. |
| **OWLv2** | Open-vocabulary object detector, paired with SAM2 as the fallback when SAM3 is unavailable. |
| **SDE** | Esri enterprise geodatabase. Authoritative GIS source for roads/buildings; read via an `arcpy` subprocess. |
| **JARVIS** | The external orchestration system that drives this tool in manifest mode. |
| **Manifest** | The TOML file describing one geocell run (§4.3). |
| **Hungarian assignment** | Optimal 1:1 matching algorithm; here it maps KMeans clusters to materials. |
| **Mirror** | `offline_installer/app/` — the byte-identical copy of the app tree (§6.1). |

---

## 10. Where to go next

| You want to… | Read |
|--------------|------|
| Run something today | [RUNNING_GUIDE.md](RUNNING_GUIDE.md) |
| Use the CLI properly | [docs/CLI_GUIDE.md](docs/CLI_GUIDE.md) (or `cli.py --examples`) |
| Understand the wiring | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Call the HTTP API | [docs/API_REFERENCE.md](docs/API_REFERENCE.md) |
| Know what's broken / in flight | [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) |
| Tune the AI masks | [docs/AI_FEATURE_EXTRACTION.md](docs/AI_FEATURE_EXTRACTION.md) |
| Get GPU working | [docs/GPU_ACCELERATION.md](docs/GPU_ACCELERATION.md) |
| Deploy to an offline station | [STANDALONE_DEPLOYMENT.md](STANDALONE_DEPLOYMENT.md) |
| Tune material colors | [docs/MEA_CALIBRATION_TOOL.md](docs/MEA_CALIBRATION_TOOL.md) |
