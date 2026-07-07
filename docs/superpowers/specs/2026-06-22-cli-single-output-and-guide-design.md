# Design — CLI single fused output + CLI guide PDF + safe perf

Date: 2026-06-22
Branch: `9-sde-road-type-fallback`
Status: Approved (design), pending implementation

## Problem

Running the CLI in its normal MEA flow (`cli.py input.tif output.tif`) leaves
**two** rasters on disk:

- `output.tif` — the *unfused* KMeans classification (3 kmeans-source materials),
  written by `core.classify_and_export` at `pipeline.py` Phase 1.
- `output_fused.tif` — the *fused* result (6 materials, roads/water/buildings
  painted on top), written by `_fuse_with_priors_and_veto`
  (`out_path = <stem>_fused<suffix>`).

The user wants **only the fused output**, landing exactly at the path passed
(`output.tif`), with no `_fused` suffix and no leftover unfused file. The work
also covers (1) a complete CLI usage/config PDF and (3) safe performance review.

## Goal 2 — CLI emits only the fused output

CLI-only, flag-gated, surgical. Mirrors the **existing tile-mode consolidation**
(`pipeline.py` Phase 2b, which already moves merged tiles back over the
classification tiles).

- Add `single_fused_output: bool = False` to `classify_v6` and
  `apply_v6_masks_to_classification` in `backend/app/pipeline.py`.
- `cli.py` `run_single` passes `single_fused_output=True` into `classify_v6`
  (the MEA branch). Web/backend callers keep the default `False`, so the web
  step1→step2 UI is untouched.
- In `apply_v6_masks_to_classification`, **before Phase 3 (XML rewrite)**, when
  the flag is on and fusion produced a distinct single-file result:

  ```python
  if single_fused_output and final_path != classification_path:
      fin = Path(final_path)
      if fin.is_file():
          fin.replace(Path(classification_path))   # atomic on same volume
          final_path = classification_path
  ```

  Phase 3 then writes the correct 6-material `<stem>.xml` to the single file.

### Why this is clean
- Sidecars need **no renaming**: `.txr` and `.xml` are stem-named off the
  classification path (`output.txr`, `output.xml`); `all_imgs.txs` is shared and
  stays valid. Phase 3 already rewrites the XML to 6 materials.
- Tile mode: Phase 2b already sets `final_path == classification_path`, so the
  new block is a no-op there.
- `--vector` + MEA path and legacy non-MEA path: unaffected (no `_fused` split).
- Bonus correctness fix: the current `_fused.tif` had no matching `.txr`; the
  consolidated single output does.

### Result
`cli.py input.tif output.tif` →
`output.tif` (fused, 6 materials) + `output.xml` + `output.txr` + `all_imgs.txs`.
Folder mode → one `<stem>_full.tif` per input (no `_fused` duplicate).

## Goal 3 — Performance (safe, verified wins only)

Engine is already well-optimized (GPU fallback chain, chunked-BLAS nearest
anchor, GDAL cache tuning, shared-model batch). Per the "safe only" decision:

- **Applied (free, from Goal 2):** one fewer raster written/left per run.
- **Audit only:** verify the `_cluster_semantic_scores` "called 3×" claim — the
  three call sites are mutually-exclusive tile/single/batch paths, so expected
  to be one call per run → **no change** unless a real redundancy is proven.
- **Deferred (risky, out of scope):** tile memory-margin `×8→×6` (OOM risk on
  the deployed A4000), GPU per-tile dispatch (major rewrite). Documented in the
  guide, not implemented.
- The PDF documents per-run tuning knobs (tile-size, workers, GPU pack).

No speculative changes. Any micro-opt must be provably safe by reading the code.

## Goal 1 — CLI guide PDF

- Source: `docs/CLI_GUIDE.md` (maintainable; "updates" = re-render).
- PDF: `docs/CLI_GUIDE.pdf`, generated via Edge/Chrome headless
  `--print-to-pdf` (both present on this machine; no Python PDF lib needed).
- Regen helper: `tools/build_cli_guide_pdf.py` (md → styled HTML → headless print).
- Contents: how it works (6 materials, KMeans vs mask sources, SAM3, SDE fusion,
  the new single-output behavior) · both invocation styles · every flag + examples
  · full `shapefile_config.json` schema incl. SDE road-width 3-tier fallback
  (`road_width_attr` → `Road_Type_Attr`/main/side → `road_width_fallback_m`) and
  `water_mask` · `app_config.json` · paths (configs, `models/`, install topology,
  where outputs land) · troubleshooting.

## Mirror & verification

- After editing `cli.py` / `backend/app/pipeline.py`, run
  `python tools/sync_mirrors.py` (mirror discipline; CI uses `--check`).
- Verify Goal 2 against the mock-SDE smoke harness (`tools/_sde_mock/`): exactly
  one `.tif` at the requested path + correct sidecars + 6-material XML; assert no
  `*_fused.tif` and no leftover unfused file.

## Files touched

- `backend/app/pipeline.py` (+ `offline_installer/app/backend/app/pipeline.py`)
- `cli.py` (+ `offline_installer/app/cli.py`)
- new: `docs/CLI_GUIDE.md`, `docs/CLI_GUIDE.pdf`, `tools/build_cli_guide_pdf.py`

## Acceptance criteria

1. `cli.py input.tif output.tif` (MEA/SDE) produces exactly `output.tif` (fused,
   6-material XML) + `output.txr` + shared `all_imgs.txs`; no `output_fused.tif`,
   no unfused leftover.
2. Web/backend behavior unchanged (flag defaults `False`).
3. Mirror check passes (`tools/sync_mirrors.py --check`).
4. `docs/CLI_GUIDE.pdf` exists, renders the full guide, and is reproducible via
   `tools/build_cli_guide_pdf.py`.
