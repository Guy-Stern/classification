# Geocell Manifest (`--manifest`) — Input Contract Specification

**Audience:** an implementer of a *different* material-classification algorithm who must
accept **exactly the same input** as MaterialClassification (MC), so that both tools are
drop-in interchangeable behind the same orchestrator (JARVIS).

**Status:** describes the behaviour of the code as of 2026-07-27 (branch `main`).
**Source of truth files** (read these if this doc and the code disagree — the code wins):

| Concern | File |
|---|---|
| TOML schema + validation | `backend/app/manifest.py` |
| CDB geocell math | `backend/app/geocell.py` |
| Source discovery + footprint filtering | `backend/app/mosaic_catalog.py` |
| Priority mosaic compositing | `backend/app/mosaic_builder.py` |
| CLI entry point / orchestration | `cli.py` → `run_manifest()` |
| Classifier entry | `backend/app/pipeline.py` → `classify_v6()` |
| Tiling / sidecars / XML | `backend/app/core.py` |
| Side-channel config | `backend/app/shapefile_config.py` |
| Known defects | `docs/MC_MANIFEST_BUGS_2026-07-12.md` |

A byte-identical mirror of the backend lives under `offline_installer/app/` (kept in sync by
`tools/sync_mirrors.py`); it is not a second implementation.

---

## 0. One-paragraph summary

A manifest is a single TOML file that describes **one OGC CDB geocell** and **N priority
layers of source orthophotos**. The tool validates it, discovers every `.tif`/`.tiff`/`.jp2`
under each layer folder, throws away the files that do not intersect the geocell, reprojects
the survivors onto **one EPSG:4326 RGB uint8 grid covering the whole cell** at the finest
surviving source's resolution, composites them **priority-last-wins** (priority 1 on top),
and hands that single mosaic raster to the classifier. The classifier output is a **folder of
512×512 georeferenced classified tiles**, deterministically named after the cell.

Everything from "validate" to "hand the mosaic to the classifier" is the shared input
contract. If your algorithm reproduces §1–§6 exactly, it consumes the same input.

---

## 1. Invocation contract

```bash
MaterialClassification_CLI.exe --manifest "C:\path\to\geocell.toml"
```

or in dev:

```bash
.venv/Scripts/python.exe cli.py --manifest geocell.toml
```

Rules enforced by `cli.py::main`:

* `--manifest` is **mutually exclusive** with `INPUT`, `OUTPUT`, `--input`, `--output`,
  `--classes`. Any combination → `argparse` error, exit code **2**.
* The manifest fully describes the run. There are **no other CLI flags that modify a manifest
  run** — knobs that exist as flags in the positional form (`--no-sam3`, `--water-mask`) are
  expressed inside the manifest's `[classify]` table instead.
* Exit codes: **0** = success, **1** = any failure (invalid manifest, no intersecting source,
  catalog failure, mosaic failure, classifier failure, overwrite refusal), **2** = argparse
  misuse.
* Failure messages are printed to stdout prefixed `FAIL:` and are the only machine-readable
  failure signal besides the exit code. Success prints
  `OK Saved (tiled folder): <path>`.
* The process is **halt-on-first-error** by design: a bad source path, a missing CRS, or a
  glob matching nothing aborts the whole run rather than silently producing a smaller mosaic.
  (Exception: individual *classification tiles* may fail and are reported as warnings — see
  §7.4.)

---

## 2. TOML schema

Parsed with stdlib `tomllib`, validated with `pydantic` v2. **Every model sets
`extra="forbid"` and `frozen=True`** — an unknown table or key anywhere is a hard validation
error, not a warning. This is deliberate: SDE/water configuration lives in
`shapefile_config.json`, so a stray `[sde]` or `[water]` table in the manifest must fail
loudly instead of being silently ignored.

### 2.1 Complete annotated example

```toml
# ── Which CDB geocell to produce ──────────────────────────────────────────────
[geocell]
south_lat = 33            # int, required, [-90, 89]   — SOUTH edge of the cell
west_lon  = 35            # int, required, [-180, 179] — WEST edge; must snap (see §3)

# ── Priority layers of source imagery (at least one) ──────────────────────────
[[layers]]
folder   = "D:/imagery/2024_jp2"    # required; dir to walk. Relative → see §2.4
priority = 1                        # required, int >= 1. LOWER = ON TOP (wins overlap)
glob     = "**/*.jp2"               # optional; str or list[str]. Default: see §2.3
name     = "2024_jp2"               # optional; logging label only, no behaviour

[[layers]]
folder   = "D:/imagery/2019_geotiff"
priority = 2                        # composited UNDER priority 1

# ── Optional flat files, composited BELOW every [[layers]] entry ──────────────
# Top-level array → must appear BEFORE the first table header if written literally.
sources = ["D:/imagery/one_off_patch.tif", "D:/patches/*.tif"]

# ── Optional per-run classifier knobs ─────────────────────────────────────────
[classify]
sam3       = true                       # default true
water_mask = "D:/data/water_mask.tif"   # default unset → falls back to config

# ── Optional, reserved ────────────────────────────────────────────────────────
[parallelism]
workers = "auto"          # int or "auto"; currently READ BUT UNUSED (see §2.6)

# ── Where the result goes ─────────────────────────────────────────────────────
[output]
path      = "D:/cdb_out/N33E035_material.tif"   # required
overwrite  = false                              # default false
```

### 2.2 Field reference

| Table | Key | Type | Required | Default | Constraints |
|---|---|---|---|---|---|
| `[geocell]` | `south_lat` | int | ✅ | — | `-90 ≤ v ≤ 89` |
| `[geocell]` | `west_lon` | int | ✅ | — | `-180 ≤ v ≤ 179`, **and** must be a multiple of the lat-zone width (§3) |
| `[[layers]]` | `folder` | path | ✅ | — | Not checked for existence at parse time; checked at catalog time |
| `[[layers]]` | `priority` | int | ✅ | — | `≥ 1`; **must be unique across all layers** |
| `[[layers]]` | `glob` | str \| list[str] | ❌ | `["**/*.tif","**/*.tiff","**/*.jp2"]` | Non-empty list; each pattern a non-empty string |
| `[[layers]]` | `name` | str | ❌ | `null` | Logging only |
| *(top level)* | `sources` | list[path] | ❌ | `null` | Entries may be literal paths or shell-style globs (§2.5) |
| `[classify]` | `sam3` | bool | ❌ | `true` | — |
| `[classify]` | `water_mask` | path | ❌ | `null` | `null` → fall back to `shapefile_config.json` |
| `[parallelism]` | `workers` | int \| str | ❌ | `"auto"` | Accepted, currently unused |
| `[output]` | `path` | path | ✅ | — | See §7.1 — the `.tif` file is **never written**; only its stem/parent matter |
| `[output]` | `overwrite` | bool | ❌ | `false` | — |

At least **one** `[[layers]]` entry is required (`min_length=1`). `sources` alone is not a
valid manifest.

### 2.3 Default layer glob

`DEFAULT_LAYER_GLOBS = ("**/*.tif", "**/*.tiff", "**/*.jp2")`

* Recursive (`**`), so a layer folder may be a whole nested tree.
* GeoTIFF and JPEG 2000 are discovered together — a single layer may hold a mix, and
  different layers may use different formats in the same manifest.
* On Windows targets these match case-insensitively (`.TIF`, `.JP2` are picked up).
* Raw `.j2k` / `.jpc` codestreams are **intentionally excluded** — they cannot carry a CRS,
  so they would be rejected downstream anyway.
* Matches from multiple patterns are **unioned** (a file matching two patterns appears once)
  and **sorted** for deterministic ordering.
* Globs are evaluated with `pathlib.Path.glob` **relative to `folder`**.

### 2.4 Path anchoring (important for reproducibility)

Every path field is anchored **against the manifest file's own directory**, docker-compose
style — *not* the process working directory:

* Applies to: each `layers[].folder`, every `sources[]` entry, `classify.water_mask`,
  `output.path`.
* Absolute paths pass through unchanged.
* The base is `Path(manifest_path).resolve().parent`.
* Consequence: `cli.py --manifest D:/cfg/geocell.toml` behaves identically from any CWD. Your
  implementation must do the same or relative manifests will resolve differently.

### 2.5 `sources` glob expansion

Each `sources` entry is inspected for the characters `*`, `?`, `[`:

* **Contains one of them** → expanded with `glob.glob(pattern, recursive=True)`, results
  sorted. **A pattern matching zero files is a hard error** (`ValueError`) — silently
  dropping it would yield a smaller mosaic with no explanation.
* **Otherwise** → passed through as a literal path. Existence is *not* checked at parse time
  (so a manifest referencing a removable drive still validates); it is checked at catalog
  build time, where a missing file raises.

Expansion happens **inside** the model validator, so the parsed manifest object already
carries the fully expanded, absolute file list.

### 2.6 `[parallelism]`

Accepted and validated, but **not used**: the v1 mosaic build is single-threaded, and the
classify stage does its own RAM-aware worker sizing. It exists so an orchestrator can carry
the knob without a schema migration later. Accept it; ignore it.

### 2.7 Validation errors you must reproduce

| Condition | Behaviour |
|---|---|
| File does not exist | `FileNotFoundError: manifest not found: <p>` |
| Unknown top-level table (e.g. `[sde]`, `[water]`) | pydantic `ValidationError` (extra forbidden) |
| Unknown key inside any table | pydantic `ValidationError` |
| `[[layers]]` absent or empty | `ValidationError` (min_length=1) |
| Two layers with the same `priority` | `ValueError: duplicate [[layers]] priority value(s) [...]` |
| `glob = []` or a blank pattern | `ValueError` |
| `sources` glob matching nothing | `ValueError` |
| `west_lon` not snapped to zone width | `ValueError` — **raised by `to_geocell()`, i.e. after model validation**, not by the schema |

All of these are caught in `run_manifest` and reported as
`FAIL: invalid manifest <path>: <error>` with exit 1.

---

## 3. Geocell math (OGC CDB 1.1)

A geocell is a **1°-tall** geographic cell whose **longitudinal width grows toward the
poles** so cell area stays roughly constant. Table from OGC CDB 15-113 §7 / Volume 1
Clause 7, evaluated at the cell's **mid-latitude** (`south_lat + 0.5`):

```
|lat| >= 80  →  12°
|lat| >= 75  →   6°
|lat| >= 70  →   4°
|lat| >= 50  →   2°
otherwise    →   1°
```

**Snap validation:** `west_lon % width != 0` → hard error. Example: `south_lat=60`,
`west_lon=7` is invalid (the 50–70 band steps by 2°). This mirrors the upstream IER manifest
so output drops into a CDB build at the correct grid position.

**Cell name:** `f"{'N' if south_lat>=0 else 'S'}{abs(south_lat):02d}"` +
`f"{'E' if west_lon>=0 else 'W'}{abs(west_lon):03d}"` → e.g. `N45E006`, `N33E035`, `S07W120`.
This name is the tile-filename stem (§7.2) — get the zero-padding right (2 digits lat,
3 digits lon).

**Bounds (WGS-84 degrees), the tuple everything downstream uses:**

```
west  = float(west_lon)
south = float(south_lat)
east  = west  + lon_width_deg
north = south + 1.0
```

Ordering convention throughout the codebase is **`(west, south, east, north)`** — rasterio
order. There is **no LOD / tile / UREF-RREF pyramid math**: a manifest run produces one
raster for the whole cell, not a CDB tile pyramid.

---

## 4. Source catalog (discovery + filtering)

`mosaic_catalog.build_catalog(layers, flat_sources, geocell_bounds)`.

### 4.1 Discovery

For each layer, in manifest order:

1. Union the matches of all its glob patterns under `folder`, sorted.
2. **Zero matches → hard error**:
   `layer priority=<p> folder=<f> glob=<g> matched no files`.
3. Log: `[catalog] layer priority=<p> folder=<f> matched <n> file(s)`.

### 4.2 Footprint read (headers only — no pixel I/O)

For each discovered file, open with rasterio/GDAL and read metadata only:

* **`src.crs is None` → hard error**:
  `source has no CRS; assign one with gdal_edit before ingest: <path>`.
* Transform the native bounds to EPSG:4326 with
  `rasterio.warp.transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)`.
  The `densify_pts=21` matters for curved reprojections — a 4-corner transform gives a
  different (too-small) footprint for large/oblique extents.
* Compute a per-source pixel size in **degrees per pixel**:

  ```
  pixel_size_deg = max((east - west) / width, (north - south) / height)
  ```

  Deliberately `max`, not `min`: conservative, so a source's resolution is never overstated.
* Any exception is re-raised as `RuntimeError: failed to read footprint for <path>: <exc>`
  (with `KeyboardInterrupt`/`SystemExit` allowed to propagate).

JPEG 2000 goes through the identical rasterio path — there is no format-specific branch. It
does require a GDAL JP2 driver (JP2OpenJPEG on the standard build); a `.jp2` GDAL cannot open
fails loudly here with its path attached.

### 4.3 Priority assignment for flat `sources`

```
flat_priority = max(layer.priority for layer in layers) + 1
```

So flat sources always occupy the single lowest band, beneath every `[[layers]]` entry. Each
flat source must exist (`sources entry does not exist: <p>` otherwise) and is footprint-read
the same way.

### 4.4 Geocell intersection filter

Axis-aligned bbox overlap against the cell bounds, **strict** (a shared edge with zero
overlap area counts as *non*-intersecting, because the resampler would read no pixels):

```
a_west < b_east and a_east > b_west and a_south < b_north and a_north > b_south
```

Dropped counts are logged. **If nothing survives**, `run_manifest` exits 1 with
`FAIL: no source intersects geocell <name> (W,S,E,N)`.

Paths of survivors are stored `.resolve()`d.

### 4.5 Composite order (the determinism rule)

```python
sorted(entries, key=lambda e: (-e.priority, str(e.path)))
```

Highest priority number first, **priority 1 LAST**. The builder applies each source in this
order with last-wins semantics, so priority 1 is the final writer per pixel. Ties inside a
priority group break **lexicographically by resolved path string** — deterministic across
runs and machines. Reproduce this exactly or overlapping same-priority sources will composite
differently.

---

## 5. Mosaic construction — the raster your algorithm actually receives

`mosaic_builder.build_mosaic(entries, geocell_bounds, out_path)`.

### 5.1 Target grid

```
gsd_deg = min(e.pixel_size_deg for e in entries if e.pixel_size_deg > 0)   # FINEST source
width   = max(1, round((east - west)  / gsd_deg))
height  = max(1, round((north - south)/ gsd_deg))
```

**Cap:** `MAX_MOSAIC_SIDE_PX = 20_000`. If `max(width, height) > cap`:

```
factor   = longest / cap
gsd_used = gsd_deg * factor
width    = max(1, ceil(lon_extent / gsd_used))     # note: ceil here, round above
height   = max(1, ceil(lat_extent / gsd_used))
```

and a `[mosaic] WARNING: ...coarsening GSD...` line is printed. The resolution drop is never
silent.

Transform: `rasterio.transform.from_bounds(west, south, east, north, width, height)` — i.e.
the grid **exactly covers the cell bounds**, north-up, no padding, no snapping to a global
grid.

> Historical note: an earlier build padded outputs to the next power of two, producing a
> black L-shaped strip. That was removed. Do **not** pad.

### 5.2 Per-source resampling

Each source is read through a `WarpedVRT` onto the *target* grid:

```python
WarpedVRT(src,
          crs="EPSG:4326",
          transform=target_transform,
          width=width, height=height,
          resampling=Resampling.bilinear,
          add_alpha=not src_has_alpha)
```

* **Bilinear** resampling.
* Sources with `< 3` bands are a hard error:
  `source must have at least 3 bands (RGB); <path> has <n>`.
* Bands 1,2,3 are taken as R,G,B; the **last** VRT band is the alpha/validity band. If the
  source already has an alpha band, `add_alpha=True` would error, so it is added only when
  missing. If the last band is still not alpha → `RuntimeError`.
* Non-`uint8` RGB is `np.clip(rgb, 0, 255).astype(np.uint8)`.
* `masked=True` is **not** used and must not be: a source with `nodata=None` returns no mask
  and out-of-extent zeros would read as real data.

### 5.3 Validity mask (the near-black rule)

```
valid = (alpha > 0) AND (mean(R,G,B) >= NEAR_BLACK_RGB_MEAN)      # NEAR_BLACK_RGB_MEAN = 8.0
```

The second term drops near-black nodata/border pixels that are **not** flagged as raster
nodata, so a high-priority source's black collar cannot stomp a real pixel from a
lower-priority layer underneath. `8.0` mirrors `core.NEAR_BLACK_RGB_MEAN` (duplicated as a
local constant on purpose, to keep `mosaic_builder` importable without the sklearn/skimage/
geopandas chain).

### 5.4 Compositing

```python
result        = np.zeros((3, H, W), dtype=np.uint8)   # black fill
overall_valid = np.zeros((H, W), dtype=bool)
for entry in composite_order(entries):                # priority 1 LAST
    rgb, valid = read_into_grid(entry)
    result[:, valid] = rgb[:, valid]
    overall_valid |= valid
missing_fraction = float((~overall_valid).mean())
```

Full-frame in RAM. `20000 × 20000 × 3 uint8 ≈ 1.15 GB` for the buffer alone; measured peak
for a live native-GSD single-cell run was **~7.35 GB in one process** — see §9 Bug 4.

### 5.5 Mosaic output profile (exact)

```python
{"driver": "GTiff", "height": H, "width": W, "count": 3, "dtype": "uint8",
 "crs": "EPSG:4326", "transform": <from_bounds>,
 "tiled": True, "blockxsize": 512, "blockysize": 512,
 "compress": "deflate", "zlevel": 1, "predictor": 2, "interleave": "band"}
```

**No `nodata` tag is set.** The `(0,0,0)` fill is what the classifier's own near-black
detection already treats as border/nodata, so the mosaic looks exactly like a real ortho
mosaic to it. Written to a `.tmp` sibling then atomically `replace()`d.

### 5.6 Temp mosaic path & lifecycle

```
<output.path>.parent / f".{cell_name}_{os.getpid()}_mosaic.tmp.tif"
```

Dot-prefixed and PID-stamped so concurrent runs (scheduler double-trigger, re-run while a
prior run classifies) cannot collide. **Deleted in a `finally` block** whether classification
succeeds or fails. The PID must **not** leak into any deliverable name — see §7.2 and Bug 2.

`build_mosaic` returns
`{path, width, height, gsd_deg, n_sources, missing_fraction}`, logged as:

```
[cli] mosaic: <n> source(s) -> <W>x<H> px @ <gsd> deg/px; <p>% of the cell uncovered (black fill)
```

### 5.7 ⚑ The handoff point

**This is the interface.** Everything above is input preparation your implementation must
match. What crosses the boundary is:

* one **EPSG:4326, 3-band, uint8, north-up GeoTIFF** covering exactly the geocell bounds,
* at the finest surviving source's GSD (or the capped GSD),
* with uncovered area as `(0,0,0)` and no nodata tag,
* plus the two knobs from `[classify]` and the side-channel config in §6.

If your algorithm consumes that raster, it has the same input. Sections 6–8 describe what MC
then does with it — match them too if you also need output interchangeability.

---

## 6. Side-channel configuration (NOT in the manifest)

`shapefile_config.json` lives next to `app_config.json` (the frozen-exe directory, or the
repo root in dev; `shapefile_config.config_path()` prints the resolved location). It is
loaded once and cached.

```jsonc
{
  "water_mask": "",              // GeoTIFF path; band 1 > 0 = water. "" = none
  "sde": {
    "enabled": false,
    "connection_file": "",       // .sde connection file
    "arcpy_python": "",          // ArcGIS Pro python.exe (the one that has arcpy)
    "tile_size_metres": 5000,
    "timeout_seconds": 1800,
    "road_width_attr": "",              // tier 1: per-feature full width, metres
    "Road_Type_Attr": "",               // tier 2: road type/class field
    "Road_Type_Key_MainRoad": "",
    "Road_Type_Width_MainRoad_m": 0.0,
    "Road_Type_Key_SideRoad": "",
    "Road_Type_Width_SideRoad_m": 0.0,
    "road_width_fallback_m": 2.0,       // tier 3; buffer radius = this / 2
    "layers": { "buildings": "", "roads": "" }
  }
}
```

Precedence rules:

* **Water**: `[classify].water_mask` in the manifest **overrides** `shapefile_config.water_mask`.
  Unset in both → no water painted.
* **Roads / buildings (SDE)**: always from `shapefile_config.json`. There is **no** manifest
  field for them, and adding one (`[sde]`) is a validation error by design.
* Road LINE features are buffered to polygons using the 3-tier width above
  (`shapefile_resolver._road_width_m`).

---

## 7. Output contract

### 7.1 Output is always a **folder**, never `[output].path`

`run_manifest` forces `tile_mode=True`, so the single `.tif` at `[output].path` is **never
created**. The deliverables are:

```
<output.path>.parent /
    <output.path>.stem + "_classified_tiles"   /   ← always
    <output.path>.stem + "_with_vectors_tiles" /   ← when vector masks are present
    <output.path>.stem + "_rm.tif"                 ← always (CDB Raster Material, §7.8)
    <output.path>.stem + "_rm.xml"                 ← always (its cell-wide CMT, §7.8)
```

Tiling is forced so the deliverable shape is deterministic regardless of the worker's free
RAM (a RAM-dependent output shape broke the downstream consumer).

**Overwrite semantics** — the guard tests those *directories*, not the `.tif`:

* `overwrite = false` and either dir exists and is non-empty →
  `FAIL: output already exists (set [output] overwrite = true to replace): <dir>`, exit 1,
  **before** any classification work.
* `overwrite = true` → both dirs are `shutil.rmtree`'d up front, so the folder ends with
  exactly one run's tile set even if the tile grid changed between runs.
* `output.path.parent` is `mkdir(parents=True, exist_ok=True)`'d.

### 7.2 Tile naming (deterministic — this is a hard requirement)

```
{cell_name}_tile_r{row}_c{col}.tif
e.g.  N33E035_tile_r0_c512.tif
```

* `row`/`col` are the tile window's **pixel offsets** in the mosaic, not indices.
* The stem is the **cell name**, passed explicitly as `tile_name_stem=geocell.name`. It must
  **not** be derived from the temp mosaic's stem — that would leak the leading dot, the PID
  and `_mosaic.tmp` into deliverables and break requeue reproducibility (ADR-009). See Bug 2.
* Same manifest + same imagery ⇒ identical filenames across runs and machines.

### 7.3 Tile grid

```
tile_size = clamp(int(sqrt(tile_max_pixels)), min=128, max=max(H, W))   # tile_max_pixels = 512**2 → 512
overlap   = 0
windows   = [(row, col, min(tile, H-row), min(tile, W-col))
             for row in range(0, H, tile) for col in range(0, W, tile)]
```

Edge tiles are **partial** (not padded). Row-major order.

### 7.4 Per-tile sidecars

Each tile writes, next to itself:

* `<tile>.tif` — the classified raster.
* `<tile>.xml` — `Composite_Material_Table` (see §7.5).
* `<tile>.txr` — WGS-84 bbox sidecar in `csm`/`EndMapTokens` format:

  ```
  csm=Geographic
  datumId=4326
  dcType=NONE
  dcSelectorId=0
  EndMapTokens
  Top: <f:.12f>
  Bottom: <f:.12f>
  Left: <f:.12f>
  Right: <f:.12f>
  ```

Once per folder: `all_imgs.txs` — a batch-load script for the GIS toolchain, one record block
per tile under the library `Source Data Library;Geospecific Imagery;Year-Round`.

A tile that fails classification is logged (`[ERROR] Tile <name> failed — skipping and
continuing`), counted in a `[WARN] n/N tiles failed` summary, and **does not** fail the run.

### 7.5 Material XML

```xml
<Composite_Material_Table>
  <Composite_Material index="1">
    <Name>GENVEGETATION</Name>
    <Color>#ff228b22</Color>
    <Primary_Substrate>
      <Thickness>1</Thickness>
      <Material>
        <Name>BM_VEGETATION</Name>
        <Weight>100</Weight>
      </Material>
    </Primary_Substrate>
  </Composite_Material>
  ...
</Composite_Material_Table>
```

2-space indent, no XML declaration, colours ARGB lower-case (`#ff` + RRGGBB), `index` is
1-based over the class list. Every per-tile table carries the **full** class list at fixed
indices 1..6, so index *N* means the same material in every tile of every cell (all per-tile
`.xml` files in a run are byte-identical) — Bug 3 is resolved, see §9.

### 7.6 The 6-material schema (`core.MEA_CLASSES`)

| index | name | hex | source | composite name |
|---|---|---|---|---|
| 1 | `BM_ASPHALT` | `#2D2D30` | mask | `ASPHALT` |
| 2 | `BM_CONCRETE` | `#B4B4B4` | mask | `CONCRETE` |
| 3 | `BM_VEGETATION` | `#228B22` | kmeans | `GENVEGETATION` |
| 4 | `BM_WATER` | `#1C6BA0` | mask | `WATER` |
| 5 | `BM_SAND` | `#EDC9AF` | kmeans | `SAND` |
| 6 | `BM_SOIL` | `#654321` | kmeans | `SOIL` |

"mask" = painted from SAM3 detections / SDE vectors / the water mask. "kmeans" = assigned by
colour clustering. Only the kmeans-source classes are trained on; mask-source materials have
no useful spectral signature and pollute cluster assignment if included.

### 7.7 CDB Raster Material pair (`_rm.tif` + `_rm.xml`)

Every run also emits the pair that the downstream OGC CDB builder (`cdb-build`, the IER
repo) takes in its `[raster_material]` manifest section. Implemented in
`backend/app/rm_export.py`; wired at `cli.py::_emit_rm_pair`.

```toml
# ...straight into cdb-build:
[raster_material]
source_tiff    = "D:/cdb_out/N33E035_rm.tif"
source_cmt_xml = "D:/cdb_out/N33E035_rm.xml"
```

**`<stem>_rm.tif`** — the CMIX index raster:

| Property | Value |
|---|---|
| Bands / dtype | **1** band, `uint8` |
| CRS / grid | EPSG:4326, union of the classified tiles (i.e. the whole geocell) |
| Values | `0..6` — see the index space below |
| `nodata` tag | **none.** CMIX 0 is a *defined material*, not absent data; tagging it would make cdb-build treat real pixels as holes |
| Profile | tiled 512², deflate z1, predictor 2 |

**`<stem>_rm.xml`** — one **cell-wide** `Composite_Material_Table`, same grammar as §7.5,
with a `DEFAULT` entry prepended at **index 0**:

| CMIX | Composite name | Base material |
|---|---|---|
| **0** | `DEFAULT` | `BM_SOIL` @ 100 |
| 1..6 | as §7.6 | as §7.6 |

Index 0 is not optional: `cdb-build` fills every pixel its source raster does not cover with
CMIX 0 and **hard-fails** (`ValueError: ... uses CMIX indices [0] that are NOT defined in the
source CMT XML`) if the index has no entry. `BM_SOIL` means an uncovered gap renders as bare
ground.

**How the indices are derived.** The CMIX raster is the exact inverse of the paint step:
`core._apply_color_table` maps label 0 to black and label *N* to class *N*'s exact colour,
mask fusion (`pipeline._fuse_with_priors_and_veto`) hard-assigns those same palette colours,
and every reprojection on the path is nearest-neighbour. So `rm_export.rgb_to_cmix` reads the
label back out of the colour losslessly. A colour that is neither black nor an exact palette
entry means something interpolated or blended a classified raster upstream — that is logged
as a `WARNING` naming the offending colours (and the pixels become CMIX 0); `strict=True`
raises `PaletteError` instead, which the tests use.

**Source of the pair.** Always `_classified_tiles/`, never `_with_vectors_tiles/` — the
vector overlay paints deliberately non-palette colours, which are visualisation, not
materials.

**Failure policy.** The RM pair is an *additional* deliverable. If it cannot be produced the
CLI prints `[RM] WARNING: ...` and still exits **0** — a classification that succeeded is
never downgraded to a failure by its export step.

The same pair is emitted by the positional/normal-conversion form
(`cli.py <in.tif> <out.tif>`), named from the output path's stem, derived from the v6 result
*before* any vector overlay.

### 7.8 Classifier invocation (for reference)

```python
classify_v6(
    raster_path       = str(tmp_mosaic),
    classes           = MEA_CLASSES,
    smoothing         = "none",
    feature_flags     = {"spectral": True, "texture": True, "indices": False},
    output_path       = str(out_path),
    sam3_enabled      = manifest.classify.sam3,
    water_mask        = str(manifest.classify.water_mask) or None,
    single_fused_output = False,
    tile_mode         = True,
    tile_max_pixels   = 512 ** 2,
    tile_overlap      = 0,
    tile_output_dir   = str(out_path),
    tile_name_stem    = geocell.name,
    tile_workers      = max(1, os.cpu_count() or 1),
    detect_shadows    = False,
    max_threads       = None,
)
```

`sam3 = false` ⇒ KMeans naturals only, no PyTorch required — the cheapest deterministic path
for conformance testing.

---

## 8. Reference pseudocode (port target)

```python
def run_manifest(manifest_path):
    m    = load_manifest(manifest_path)          # §2: parse, anchor paths, validate
    cell = m.geocell.to_geocell()                # §3: snap check, name, bounds
    bounds = cell.bounds_wgs84()                 # (W, S, E, N)

    out = Path(m.output.path)
    dirs = [out.with_name(out.stem + s) for s in ("_classified_tiles", "_with_vectors_tiles")]
    if any(d.is_dir() and any(d.iterdir()) for d in dirs) and not m.output.overwrite:
        fail("output already exists")            # §7.1
    if m.output.overwrite:
        for d in dirs: rmtree(d, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)

    entries = build_catalog(m.layers, m.sources, bounds)     # §4
    if not entries: fail("no source intersects geocell")

    tmp = out.parent / f".{cell.name}_{os.getpid()}_mosaic.tmp.tif"
    try:
        info = build_mosaic(entries, bounds, tmp)            # §5
        result = your_classifier(                            # §5.7 handoff
            raster       = tmp,
            sam3_enabled = m.classify.sam3,
            water_mask   = m.classify.water_mask or config_water_mask(),
            out_dir      = out,
            tile_stem    = cell.name,
            tile_size    = 512,
        )
    finally:
        tmp.unlink(missing_ok=True)

    exit(0 if result.ok else 1)
```

Import ordering note: MC imports the light dependencies (`tomllib`, `pydantic`, `rasterio`)
up front and defers the heavy classifier imports (torch, sklearn) until **after** the mosaic
is built, so a bad manifest fails fast without paying the model-import cost. Worth copying.

---

## 9. Known defects to be aware of (`docs/MC_MANIFEST_BUGS_2026-07-12.md`)

Bugs 1 and 2 (overwrite guard on the tiled folder; PID/dot-prefix in tile names) are **fixed**
in the code described above — the doc records their history and the required behaviour, which
is what §7.1/§7.2 specify. Still open:

* ~~**Bug 3 — per-tile material tables are not unified across the cell.**~~ **Fixed.**
  `_classify_tile_worker` passes the full class list to `_write_composite_material_xml`, so
  every per-tile table carries all 6 materials at fixed indices 1..6 and all the `.xml` files
  in a run are byte-identical. The cell-wide index space the CDB RM ingest needs is emitted
  explicitly as `<stem>_rm.xml` (§7.7), with `DEFAULT` at index 0. Verified end-to-end
  against `cdb-build` in `tests/test_rm_export_integration.py`.
* **Bug 4 — mosaic build is full-frame in RAM with no tiling fallback.** A native-GSD 1° cell
  measured ~7.35 GB peak; on a box with ~7 GB free it thrashed and never completed. On true
  OOM the mosaic step exits 1. Note the AOI polygon does **not** bound this — each geocell
  always mosaics its whole 1° cell; the only levers are coarser input GSD or fewer cells. A
  windowed/streaming builder (VRT + block-wise translate) is the intended fix, and a new
  implementation should start there. The *classify* stage is already safely tiled at 512 px;
  only the mosaic build is unbounded.

---

## 10. Conformance checklist

Run these against a reference manifest and diff against MC's output.

**Schema**
- [ ] `[sde]` / `[water]` / any unknown table or key → non-zero exit
- [ ] Duplicate `[[layers]].priority` → non-zero exit
- [ ] Missing `[[layers]]` → non-zero exit
- [ ] `glob = []` → non-zero exit
- [ ] `sources` glob matching nothing → non-zero exit
- [ ] `south_lat=60, west_lon=7` → non-zero exit (2° zone)
- [ ] Relative `folder`/`output.path` resolve against the manifest dir from **two different CWDs**, identically
- [ ] Omitted `glob` discovers `.tif` + `.tiff` + `.jp2` recursively
- [ ] `--manifest` combined with `--input` → exit 2

**Catalog / mosaic**
- [ ] A source with no CRS aborts the run with its path in the message
- [ ] A source with 2 bands aborts the run
- [ ] Sources entirely outside the cell are dropped; count logged
- [ ] A source sharing only an edge with the cell is dropped
- [ ] Mosaic is EPSG:4326, 3-band uint8, bounds == cell bounds, no nodata tag
- [ ] Mosaic W/H match `round(extent / min_pixel_size)` (or the ceil-based capped values)
- [ ] Overlap region takes pixels from the **lower** priority number
- [ ] A black collar on the priority-1 source does **not** overwrite priority-2 real pixels
- [ ] A cell needing > 20000 px/side coarsens with a printed WARNING rather than exploding
- [ ] Same-priority overlap resolves by lexicographic path, reproducibly
- [ ] Temp mosaic is deleted on both success and failure paths

**Output**
- [ ] `[output].path` `.tif` is never created
- [ ] `overwrite=false` into a populated `_classified_tiles/` → exit 1, nothing written
- [ ] `overwrite=true` → folder holds exactly one run's tiles, never an accumulation
- [ ] Tile names are `N33E035_tile_r{row}_c{col}.tif` — no leading dot, no PID, no `_mosaic.tmp`
- [ ] Two runs of the same manifest produce identical filenames (and, with `sam3=false`, identical bytes)
- [ ] Each tile has `.xml` + `.txr`; the folder has one `all_imgs.txs`
- [ ] `*.tif` (non-dotfile-aware) glob matches every tile

**CDB Raster Material pair (§7.7)**
- [ ] `<stem>_rm.tif` + `<stem>_rm.xml` exist beside the tile folder after every run
- [ ] `_rm.tif` is 1-band uint8, EPSG:4326, no `nodata` tag, spanning the whole geocell
- [ ] Every value in `_rm.tif` is in `0..6` and defined in `_rm.xml`
- [ ] `_rm.xml` has a `DEFAULT` entry at index 0; removing it makes `cdb-build build` fail
- [ ] `_rm.tif` reproduces the classified tiles pixel-for-pixel (same CMIX histogram)
- [ ] `_rm.xml` is byte-identical across two runs of the same manifest
- [ ] `cdb-build build` + `cdb-build validate` on the pair exit 0 with no CRITICAL/HIGH findings
- [ ] A forced RM-export failure still exits 0 with the classification intact

---

## 11. Worked examples

**Minimal — one layer, no SAM3 (deterministic, no GPU/PyTorch):**

```toml
[geocell]
south_lat = 33
west_lon  = 35

[[layers]]
folder   = 'C:\imagery\infrao_ds'
priority = 1
glob     = "*.tif"
name     = "infrao_1"

[classify]
sam3 = false

[output]
path      = 'C:\out\N33E035.tif'
overwrite = false
```

Produces `C:\out\N33E035_classified_tiles\` containing `N33E035_tile_r*_c*.tif` + `.xml` +
`.txr`, plus one `all_imgs.txs`.

**Layered, mixed format, relative paths** (`installer_assets/examples/sample_data/geocell.toml`
— folders resolve next to the TOML):

```toml
[geocell]
south_lat = 33
west_lon  = 35

[[layers]]
folder   = "upper_jp2"    # JPEG 2000 — on top, wins the overlap
priority = 1
name     = "upper_jp2"

[[layers]]
folder   = "lower_tif"    # GeoTIFF — underneath
priority = 2
name     = "lower_tif"

[output]
path      = "out/demo_material.tif"
overwrite = true
```

**Full — layers + flat sources + both classify knobs:**

```toml
sources = ["D:/patches/*.tif", "D:/one_off.tif"]   # lowest band, priority 3 here

[geocell]
south_lat = 45
west_lon  = 6            # 45 is in the 1° zone, so any integer is valid

[[layers]]
folder   = "D:/orthos/2024_campaign"
priority = 1
glob     = ["**/*.tif", "**/*.jp2"]
name     = "2024"

[[layers]]
folder   = "D:/orthos/archive_2019"
priority = 2

[classify]
sam3       = true
water_mask = "D:/data/water_mask.tif"

[output]
path      = "D:/cdb_out/N45E006_material.tif"
overwrite = true
```

---

## 12. Constants quick-reference

| Constant | Value | Where |
|---|---|---|
| `DEFAULT_LAYER_GLOBS` | `("**/*.tif", "**/*.tiff", "**/*.jp2")` | `manifest.py` |
| CDB zone widths | `12/6/4/2/1°` at `\|lat\|` ≥ `80/75/70/50`/else | `geocell.py` |
| Cell latitude extent | `1.0°` | `geocell.py` |
| Bounds order | `(west, south, east, north)` | everywhere |
| Reprojection densify | `densify_pts=21` | `mosaic_catalog.py` |
| `pixel_size_deg` | `max(x_res, y_res)` (conservative) | `mosaic_catalog.py` |
| Target GSD | `min(pixel_size_deg)` (finest) | `mosaic_builder.py` |
| `NEAR_BLACK_RGB_MEAN` | `8.0` | `mosaic_builder.py` (mirrors `core.py`) |
| `MAX_MOSAIC_SIDE_PX` | `20_000` | `mosaic_builder.py` |
| Resampling | `Resampling.bilinear` | `mosaic_builder.py` |
| Mosaic profile | uint8, 3-band, deflate z1, predictor 2, 512² blocks, band-interleaved, **no nodata** | `mosaic_builder.py` |
| Classify tile size | `512` (`tile_max_pixels = 512**2`), overlap `0` | `cli.py` |
| Tile name pattern | `{cell}_tile_r{row}_c{col}{ext}` | `core.py` |
| Tile dir suffixes | `_classified_tiles`, `_with_vectors_tiles` | `cli.py` / `core.py` |
| Material count | 6 (`MEA_CLASSES`) | `core.py` |
| RM pair suffixes | `_rm.tif`, `_rm.xml` | `rm_export.py` |
| RM default CMIX | `0` → `DEFAULT` / `BM_SOIL` @ 100 | `rm_export.py` |
| RM profile | 1-band uint8, deflate z1, predictor 2, 512² blocks, **no nodata** | `rm_export.py` |
