# Manifest / geocell-mode bugs found in live JARVIS end-to-end (2026-07-12)

**Repo:** MaterialClassification (this repo — `cli.py`, `backend/app/*`).
**Run each fix on its own branch** off the current tiled-output build (`feat/manifest-classify-knobs`).
**Status:** all issues below were reproduced **live** on 2026-07-12 by running the installed tool
(`C:\ClassificationApp`) through JARVIS in geocell/manifest mode. Everything needed to reproduce is
in this doc — no external context required.

This is a companion to `docs/Algos/... MC_MANIFEST_FUSED_OUTPUT_FIX.md` (which was resolved by the
"always tiled folder" decision). These are the issues that remain / were discovered on top of that.

---

## Environment / how to reproduce any of these

- Install under test: `C:\ClassificationApp` (has `cli.py`, `.venv`, `MaterialClassification_CLI.exe`).
  The exe is a thin launcher (`cli_launcher.py`) that runs the on-disk `cli.py` in `.venv` — so the
  exe already runs the current tiled/`[classify]` build.
- Tool venv: `C:\ClassificationApp\.venv\Scripts\python.exe` (has rasterio + torch 2.5.1+cu121).
- Minimal manifest (`geocell.toml`) — one tif layer, one cell, SAM3 off (KMeans naturals only, no
  GPU/PyTorch needed). Point `folder` at any dir of EPSG:4326 GeoTIFFs inside geocell N33E035:

```toml
[geocell]
south_lat = 33
west_lon  = 35

[[layers]]
folder   = 'C:\Users\User\Desktop\StuffForTests\_mc_e2e\imagery_ds'
priority = 1
glob     = "*.tif"
name     = "infrao_1"

[classify]
sam3 = false

[output]
path      = 'C:\some\out\N33E035.tif'
overwrite = false
```

- Run: `cd C:\ClassificationApp && MaterialClassification_CLI.exe --manifest <path>\geocell.toml`
- Result today: exit 0, prints `OK Saved (tiled folder): ...\out\N33E035_classified_tiles`, and the
  folder contains per-tile `.tif` + `.xml` + `.txr` + one `all_imgs.txs`. **The tool classifies
  correctly** (verified real SAND/SOIL/VEGETATION paint). The bugs are all in the **packaging /
  naming / overwrite / memory** behavior, not the classification itself.

---

## BUG 1 — `[output].overwrite=false` does not protect the tiled folder, and a re-run DUPLICATES tiles

**Severity: HIGH (produces a corrupt deliverable silently).**

### What happens
`overwrite=false` is supposed to refuse to clobber an existing output. In tiled mode the output is a
**directory** `<output_stem>_classified_tiles\`, but the overwrite guard checks the single
`[output].path` **file** (`<cell>.tif`) — which is **never created** in tiled mode. So the guard always
passes, and:

1. Running with `overwrite=false` into a directory that already contains a previous run's tiles does
   **not** error.
2. Because tile filenames embed the PID (see Bug 2), the second run's tiles have **different names**,
   so they do not overwrite the first run's tiles — they are **added alongside them**.

### Reproduced (live, through JARVIS)
- Run 1 into `...\out\` → `N33E035_classified_tiles\` had **64** `.tif` tiles (PID 18048).
- Re-ran the identical job into the same `...\out\` with `overwrite=false` → exit 0,
  `awaiting_verification`, and the folder now had **128** `.tif` tiles: **64 from PID 18048 + 64 from
  PID 12372**, all mixed in one directory. A consumer reading the folder now sees two overlapping tile
  sets for the same cell.

### Required behavior
- `overwrite=false`: if `<output_stem>_classified_tiles\` already exists (and is non-empty), **fail
  loudly** (non-zero exit, clear message) **before** classifying — same contract the file-mode path has.
- `overwrite=true`: **clean/replace** the target folder (remove the previous run's tiles) before
  writing, so a re-run yields exactly one tile set, never an accumulation.
- Never leave a directory containing tiles from two different runs.

### Pointers (anchor on function names, not line numbers)
- `cli.py::run_manifest` — the overwrite check currently tests `Path(output_path).exists()` on the
  `.tif`; extend it to the derived `<stem>_classified_tiles\` directory.
- `backend/app/core.py` (`_resolve_tile_output_dir` / the tile-writing path) — clean the dir on
  `overwrite=true`; refuse on `overwrite=false`.

### Acceptance
- Re-running a manifest into an existing tiled folder with `overwrite=false` exits non-zero and writes
  nothing new. With `overwrite=true` the folder ends with exactly one run's tiles.

---

## BUG 2 — Tile filenames embed the process PID and are dot-prefixed → non-reproducible + glob-hostile

**Severity: HIGH (breaks requeue reproducibility [ADR-009] and forces dotfile-aware globs).**

### What happens
Each tiled output file is named:

```
.N33E035_<PID>_mosaic.tmp_tile_r{row}_c{col}.tif   (+ .xml, + .txr)
```

- It embeds the **process PID** (`<PID>`), so **the same input produces different filenames on every
  run** (observed PIDs across three identical runs: 15620, 18048, 12372).
- It is **dot-prefixed** (leading `.`), so `*.tif` globs miss every tile — every consumer must use
  dotfile-aware globbing.
- The `_mosaic.tmp` fragment leaks an internal temp name into the deliverable.

Root of both: `run_manifest` names the intermediate mosaic `.{cell}_{PID}_mosaic.tmp.tif`, and the
tile writer derives tile names from that mosaic's `Path.stem` (`{stem}_tile_r{row}_c{col}{ext}`), so the
dot-prefix + PID + `.tmp` all propagate into the final tile names.

### Required behavior
Deterministic, clean, stable tile names independent of PID and of the temp mosaic name, e.g.:

```
N33E035_tile_r{row}_c{col}.tif   (+ .xml, + .txr)
```

- No leading dot, no PID, no `_mosaic.tmp`.
- **Same manifest + same imagery → byte-identical filenames across runs / requeue** (ADR-009).

### Pointers
- `cli.py::run_manifest` — stop encoding the PID in the temp mosaic filename, or derive the tile stem
  from the **cell name** (`N33E035`) rather than the temp mosaic's stem.
- `backend/app/core.py` — the `f"{stem}_tile_r{row}_c{col}{out_ext}"` tile naming; base it on the cell
  name.

### Acceptance
- Two runs of the same manifest (into cleaned output dirs) produce tiles with **identical names**
  (and identical bytes, given the deterministic sam3=false path). No dot-prefix; `*.tif` globs match.

---

## BUG 3 — Per-tile `.xml` material tables are not unified across the cell

**Severity: MEDIUM (blocks the CDB `rm`-layer consumer). Reported from design docs; not re-verified
live this session — please confirm while fixing Bug 2, since both touch tile output.**

### What happens
Each tile's `.xml` `Composite_Material_Table` indexes only the materials **present in that tile**, so a
given index `N` can mean different materials in different tiles. The cell has **no single consistent
material map**, which the downstream `cdb_build` rm-layer ingest needs.

### Required behavior
Emit **one unified per-cell material table** (stable material→index mapping across all tiles of the
cell), or make every per-tile `.xml` share the same cell-wide index space.

### Acceptance
- For a multi-material cell, index `N` denotes the same material in every tile's `.xml`, and/or a single
  cell-level material table is written.

---

## BUG 4 — Mosaic build is full-frame in RAM with no tiling fallback → OOM/thrash on constrained boxes

**Severity: MEDIUM–HIGH for portability (the classify stage is fine; the MOSAIC stage is the hazard).**

### What happens
For a full 1° geocell at native GSD, the mosaic is capped at 20000×20000 and built **entirely in RAM**.
Measured live: a single-cell native run held **~7.35 GB in one process** and drove free RAM to 2.33 GB;
on a box with ~7 GB free it **thrashed and never completed** in 7+ minutes. On true OOM the mosaic step
`sys.exit(1)`s (`FAIL: mosaic build failed`) with **no tiling fallback** — so output shape/feasibility
depends on the worker's free RAM. (The *classify* stage is already safely force-tiled at 512 px — only
the mosaic build is unbounded.)

Note for the JARVIS side: the drawn AOI polygon does **not** bound this — each geocell always mosaics its
whole 1° cell; the only levers are coarser input GSD or fewer cells. So on constrained workers the
mosaic build is the ceiling regardless of AOI size.

### Required behavior (design change — scope as its own task)
Build the cell mosaic in a **streaming / windowed / tiled** manner (e.g. GDAL VRT over the sources then
block-wise translate, or write the mosaic tile-by-tile) so peak RAM is bounded and independent of cell
size — mirroring how the classify stage is already tiled. On genuine resource exhaustion, fail with a
clear, actionable message (not a bare `exit 1`).

### Acceptance
- A full-cell native-GSD manifest completes on a worker with only a few GB free (no OOM, bounded peak
  RAM), producing the same tiles it would with ample RAM.

---

## NOT an MC bug — routed here only so it isn't mistaken for one

**Task cancel orphans the tool subprocess.** When JARVIS cancels a running task, the JARVIS **agent**
marks the task `cancelled` but does **not** terminate the `MaterialClassification_CLI.exe` subprocess —
it kept running at 7.3 GB until force-killed. This is a **JARVIS agent** issue (subprocess lifecycle on
cancel), **not** the MC tool. Filed separately on the JARVIS side; listed here for completeness because
it surfaced during the same run.

---

## Priority order suggested
1. **Bug 2** (deterministic tile names) — smallest, unblocks requeue and cleans globs.
2. **Bug 1** (overwrite/clean) — small, prevents corrupt duplicated folders.
3. **Bug 3** (unified material table) — needed by the cdb_build rm consumer.
4. **Bug 4** (streaming mosaic) — larger; needed for constrained/airgapped workers "for any boundary".

Bugs 1 & 2 are closely related (both stem from the temp-mosaic naming) and are best fixed together.

## When fixed
After the fixes are built into the install (`cli.py` / rebuilt exe) at `C:\ClassificationApp`, the
JARVIS geocell E2E can be re-run to confirm: deterministic tile names across a requeue, overwrite
refusal/clean, one unified material table per cell, and a native full-cell run that no longer OOMs.
The JARVIS-side harness + fabricated InfraO report used to find these live at
`C:\Users\User\Desktop\StuffForTests\_mc_e2e\` (report.json, footprint, downsampled imagery).
