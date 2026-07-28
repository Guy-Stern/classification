# Architecture

High-level map of how the classification project's pieces fit together. For a guided
introduction start at [../ONBOARDING.md](../ONBOARDING.md); for operational commands see
[../RUNNING_GUIDE.md](../RUNNING_GUIDE.md); for deployment see
[../STANDALONE_DEPLOYMENT.md](../STANDALONE_DEPLOYMENT.md).

> The "god nodes" table below came from a graphify knowledge graph at `graphify-out/`.
> **That directory no longer exists** (lost in the May-2026 deletion incident) and graphify
> is not installed, so the edge counts are a point-in-time snapshot, not live data. Read
> the source directly.

---

## God Nodes (most-connected functions — snapshot, pre-phase-6)

| Node | Edges | Where |
|------|-------|-------|
| `classify_and_export()` | 44 | [backend/app/core.py](../backend/app/core.py) |
| `get()` (web client wrapper) | 35 | [web_app/src/api/client.ts](../web_app/src/api/client.ts) |
| `rasterize_vectors_onto_classification()` | 17 | [backend/app/core.py](../backend/app/core.py) |
| `build_shared_color_table()` | 16 | [backend/app/core.py](../backend/app/core.py) |
| FastAPI app at `backend/app/main.py` | 16 | [backend/app/main.py](../backend/app/main.py) |
| `_classify_tile_worker()` | 15 | [backend/app/core.py](../backend/app/core.py) |
| `classify()` (back-compat wrapper) | 14 | [backend/app/core.py](../backend/app/core.py) |
| Reducer (app state) | 14 | [web_app/src/store/index.tsx](../web_app/src/store/index.tsx) |
| `AppState` interface | 12 | [web_app/src/types.ts](../web_app/src/types.ts) |
| `ActionsSection` sidebar panel | 12 | [web_app/src/components/sidebar/ActionsSection.tsx](../web_app/src/components/sidebar/ActionsSection.tsx) |

---

## Process / Service Layout

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Browser                                                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  React + Vite (web_app)                                              │  │
│  │  ┌──────────────┐  ┌─────────────┐  ┌──────────────────────────┐ │  │
│  │  │  Sidebar     │  │  MapView    │  │  LayerPanel / StatusBar  │ │  │
│  │  │  panels      │  │  (Leaflet)  │  └──────────────────────────┘ │  │
│  │  └──────────────┘  └─────────────┘                                 │  │
│  │           │                                                          │  │
│  │           ▼  api/client.ts ──── /api/* via Vite proxy ────────┐    │  │
│  └────────────────────────────────────────────────────────────────┼────┘  │
└────────────────────────────────────────────────────────────────────┼───────┘
                                                                     │
                                                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  FastAPI backend  (uvicorn :8000)                                            │
│  backend/app/main.py                                                         │
│   ├── _StripApiPrefix middleware  (strips /api so the same client works     │
│   │                                  against dev proxy and prod static)    │
│   ├── /classify, /classify-step1, /classify-step2, /classify-batch          │
│   ├── /extract-roads, /merge-road-mask                                      │
│   ├── /extract-features, /merge-feature-masks                               │
│   ├── /progress/{task_id}  (SSE), /cancel/{task_id}                         │
│   ├── /raster-info, /raster-as-png, /list-dir, /scan-folder                 │
│   ├── /suggest-tile-size, /gpu-info, /app-config, /set-sam3-path …          │
│   │                                                                         │
│   ├── pipeline.py    ← classify_v6 — THE orchestrator every entry point     │
│   │                    funnels into: mask acquire → KMeans → fuse → XML     │
│   ├── core.py        ← classify_and_export, rasterize_vectors,              │
│   │                    train_kmeans_model, build_shared_color_table,        │
│   │                    suggest_tile_size, _classify_tile_worker,            │
│   │                    _detect_shadows_and_infer, _MEA_COMPOSITE_NAMES,     │
│   │                    _write_composite_material_xml, .txr/.txs writers    │
│   ├── road_extraction.py  ← extract_roads / extract_feature_masks /         │
│   │                          merge_*, FEATURE_CONFIGS                       │
│   ├── shapefile_config.py / shapefile_resolver.py  ← GIS mask sources       │
│   ├── sde_extractor.py + sde_arcpy_worker.py  ← Esri SDE via arcpy subproc  │
│   ├── manifest.py / geocell.py / mosaic_catalog.py / mosaic_builder.py      │
│   │                    ← geocell manifest mode (CLI only, see below)        │
│   ├── config.py      ← persistent JSON app config                           │
│   └── mea_profile.py ← reads %ProgramData%\…\mea_calibration_profile.json   │
└────────────────────────────────────────────────────────────────────────────┘
            ▲
            │  same core + pipeline, no HTTP
            │
┌────────────────────────────────────────────────────────────────────────────┐
│  cli.py  (also shipped as MaterialClassification_CLI.exe)                    │
│   file / folder-batch / --manifest geocell modes                             │
└────────────────────────────────────────────────────────────────────────────┘
            ▲
            │  reads
            │
%ProgramData%\MaterialClassification\mea_calibration_profile.json
            ▲
            │  written by
            │
┌────────────────────────────────────────────────────────────────────────────┐
│  MEA Calibration Tool  (separate FastAPI :8100 + React)                     │
│  mea_calibration_tool/backend/app/{main,profile,raster_io,sampling}.py      │
└────────────────────────────────────────────────────────────────────────────┘
```

The two apps are intentionally decoupled — the calibration tool only writes the
profile JSON, the main app only reads it. There is no IPC.

---

## Two-step pipeline (Step 1 + Step 2)

```
raster.tif ──► classify_and_export()  ──►  classified.tif (RGB)
                                            └─► <stem>.xml (MEA only)
                                            └─► <stem>.txr / .txs (MEA only)
                                            └─► EPSG:4326 reprojection

classified.tif + vectors ──► rasterize_vectors_onto_classification() ──► merged.tif
```

`classify()` is a thin wrapper that calls both steps in order — kept for backward
compatibility with the Tkinter app and `cli.py`.

The `/classify` endpoint runs the full pipeline; `/classify-step1` runs step 1 only;
`/classify-step2` runs step 2 only on an existing classification.

> Outputs used to be padded to power-of-2 **dimensions** for an old texture-engine
> assumption. Removed in `fd055ff` — it produced a black L-shaped strip on every output.
> Only the *tile size* is power-of-2 now, never the raster itself.

---

## `classify_v6` — the MEA orchestrator

[../backend/app/pipeline.py](../backend/app/pipeline.py). Every MEA run — web app, CLI,
manifest mode — goes through this. It sits *above* the two-step core pipeline.

```
                     ┌──────────────────────────────────────────────┐
   raster.tif ─────► │ Phase 1  ACQUIRE MASKS  (_acquire_mask)      │
                     │   water  ← water_mask GeoTIFF (band 1 > 0)   │
                     │   roads  ← SDE (buffered) → SAM3 → empty     │
                     │   bldgs  ← SDE → SAM3 → empty                │
                     └──────────────────┬───────────────────────────┘
                     ┌──────────────────▼───────────────────────────┐
   raster.tif ─────► │ Phase 2  KMEANS on source="kmeans" classes   │
                     │   _split_classes_by_source() filters the list│
                     │   core.classify_and_export() does the work   │
                     │   Hungarian 1:1 cluster→material assignment  │
                     └──────────────────┬───────────────────────────┘
                     ┌──────────────────▼───────────────────────────┐
                     │ Phase 3  FUSE: water → roads → buildings     │
                     │   later masks win on overlap                 │
                     │   soft-prior veto DISABLED (thresholds >1.0) │
                     └──────────────────┬───────────────────────────┘
                     ┌──────────────────▼───────────────────────────┐
                     │ Phase 4  Rewrite XML with all 6 materials    │
                     └──────────────────┬───────────────────────────┘
                                        ▼
                        classified.tif + .xml + .txr / .txs
```

Two invariants worth internalising:

- **Mask-source classes never enter a KMeans model.** They have no useful spectral
  signature and poison cluster assignment for everything else. `_split_classes_by_source()`
  exists to enforce this; ignoring it caused a production bug where batch outputs came back
  almost entirely water (`240c4ca`).
- **Masks are authoritative.** `_VETO_THRESHOLDS` in `pipeline.py` are all set above 1.0,
  which disables the veto. A shapefile or SAM3 detection always paints, even if the
  underlying RGB looks like vegetation.

---

## Geocell manifest mode (CLI only)

`cli.py --manifest geocell.toml`. One TOML = one OGC CDB geocell = one `classify_v6` run.
Instead of a single input raster, the manifest declares **priority layers** of source
imagery.

```
geocell.toml
    │  manifest.py     validate (pydantic, extra="forbid")
    ▼
GeocellManifest ──► geocell.py       CDB cell math → WGS-84 bounds, name (N33E035)
    │                                 west_lon must snap to the lat-zone width
    ▼
mosaic_catalog.py    discover *.tif / *.tiff / *.jp2 per layer
    │                intersect footprints with the cell, drop non-overlapping
    ▼
mosaic_builder.py    reproject each source to the target grid via WarpedVRT
    │                composite PRIORITY LAST-WINS (priority 1 written last)
    │                near-black pixels (mean RGB < 8) excluded from validity
    │                MAX_MOSAIC_SIDE_PX=20000 caps the grid, coarsening GSD if needed
    ▼
<cell>_mosaic.tmp.tif  ──► classify_v6(tile_mode=True, tile_max_pixels=512²,
    │                                   tile_name_stem=<cell>)
    ▼
<cell>_classified_tiles/   N33E035_tile_r{row}_c{col}.tif + .xml + .txr
```

Design decisions that are easy to misread as bugs:

- **Output is always a folder**, never the `[output].path` file. Tiling is forced so the
  deliverable shape is deterministic regardless of the worker's free RAM; the overwrite
  guard therefore checks the `_classified_tiles/` directory, not the `.tif`.
- **Tile names derive from the cell name**, not the temp mosaic's stem — so the same
  manifest yields byte-identical filenames across runs (requeue reproducibility).
- **Priority direction matches QGIS/Photoshop**: lower number = on top. Flat `sources`
  entries composite *below* every `[[layers]]` entry.
- The mosaic build is currently **full-frame in RAM**. On a constrained box this is the
  ceiling for a full 1° cell — see Bug 4 in
  [MC_MANIFEST_BUGS_2026-07-12.md](MC_MANIFEST_BUGS_2026-07-12.md); the windowed rewrite
  is on the open PR #4 branch.

---

## Batch shared-model classification

`POST /classify-batch` trains one KMeans model on a representative sample, then
re-uses the trained centroids and a **shared color table** (`build_shared_color_table()`)
to classify every raster in the batch. This avoids each image getting its own
unrelated cluster IDs.

Used when the user picks multiple files at once.

---

## AI feature extraction pipeline

```
raster.tif
   │
   ▼
should_extract_feature(raster, feature_type)        ← RGB / linearity pre-filter
   │
   ▼ pass
FEATURE_CONFIGS[feature_type] (1+ sub-prompts)
   │
   ▼ for each sub-prompt
extract_feature_masks( … )
   │   ├── OWLv2 + SAM2/3 text-prompted segmentation
   │   ├── color_detect: color+geometry CV detector
   │   └── union (OR) per-tile masks → <suffix>.tif
   │
   ▼
_<feature_type>/<sub-suffix>.tif        ← organized subfolders
   │
   ▼
merge_feature_masks_onto_classification(...)        ← chains across all classifications
```

See [AI_FEATURE_EXTRACTION.md](AI_FEATURE_EXTRACTION.md) for prompts, feature colors,
and model fallback chain.

---

## Web-app state

`web_app/src/store/index.tsx` owns the reducer and exposes
`StoreProvider`, `useAppState`, `useAppDispatch`. Sidebar sections dispatch actions;
`MapView` and `LayerPanel` read state.

The `AppState` interface in `web_app/src/types.ts` is the single source of truth for
what the UI tracks: active raster, vector layers, MEA-mode toggle, performance
settings, classification result, progress events, …

---

## Build / packaging

- `WebApp.spec` (PyInstaller) → `dist/ClassificationWebApp.exe`. Includes a runtime
  hook that pre-loads CuPy CUDA DLLs by full path so GPU works in the frozen exe.
- `ClassificationApp.iss` (Inno Setup) → `ClassificationApp_Setup.exe`.
- `prepare_offline.bat` → `offline_installer/` USB payload (embedded Python + all
  wheels + optional HF model cache).

---

## Extension points

- New AI feature: add an entry to `FEATURE_CONFIGS` in
  [backend/app/road_extraction.py](../backend/app/road_extraction.py) — prompts,
  default merge color, optional `color_detect` callback, optional `threshold`.
  Add a checkbox in `web_app/src/components/sidebar/FeaturesSection.tsx`.
- New post-processing step: insert in `classify_and_export()` between
  *Pixel assignment* and *Saving output* phases (see `_PHASE_WEIGHTS` in
  [backend/app/main.py](../backend/app/main.py)).
- New MEA material: edit `MEA_CLASSES` / `_MEA_COMPOSITE_NAMES` in `core.py`,
  [shared/mea_defaults.json](../shared/mea_defaults.json) (anchors), and
  [web_app/src/constants/mea.ts](../web_app/src/constants/mea.ts). Decide `source` first —
  `"kmeans"` needs anchor colors that separate cleanly from the existing ones; `"mask"`
  needs a mask source wired into `pipeline.py::_acquire_mask`.
  (`shared/mea_classes.json` is the legacy 13-class list and is **not** read by any code.)
- New geocell manifest field: [backend/app/manifest.py](../backend/app/manifest.py). Every
  model sets `extra="forbid"`, so an unknown table is a hard error by design.

**After any of these, run `tools/sync_mirrors.py`** — `offline_installer/app/` is a
byte-identical mirror and drift there has caused shipped-product bugs before.
