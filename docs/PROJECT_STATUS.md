# Project Status — branches, PRs, open work

Snapshot of where the repository actually stands, so nobody has to reconstruct it from
`git log`. Start at [../ONBOARDING.md](../ONBOARDING.md) if you are new.

**As of 2026-07-27.** `main` @ `5ce6d4f` — *docs: add manifest/geocell bugs from 2026-07-12 E2E*.

---

## 1. Remotes

| Remote | URL | Role |
|--------|-----|------|
| `origin` | `github.com/Guy-Stern/classification` | The working fork. **All current development happens here.** |
| `upstream` | `github.com/ofekalmog5/classification` | The original repo. Read-only history; nothing has been pushed there since phase 6. |

---

## 2. Pull requests

### Merged

| PR | Branch | What it delivered | Merged |
|----|--------|-------------------|--------|
| [#1](https://github.com/Guy-Stern/classification/pull/1) | `feat/manifest-jp2-tiff-layers` | JPEG 2000 discovery alongside GeoTIFF in geocell layers; installer usage examples + corrected offline README | 2026-07-09 |
| [#2](https://github.com/Guy-Stern/classification/pull/2) | `feat/manifest-classify-knobs` | Optional `[classify]` manifest table (`sam3`, `water_mask`) for per-run knobs; tiled geocell output | 2026-07-12 |
| [#3](https://github.com/Guy-Stern/classification/pull/3) | `fix/manifest-tile-names-overwrite` | Deterministic tile names (`N33E035_tile_r{r}_c{c}` — no PID, no dot-prefix, no `_mosaic.tmp`); overwrite guard + clean on the tiled output folder | 2026-07-13 |

PRs #1–#3 are the three-step hardening of manifest/geocell mode. Together they closed
**Bug 1** and **Bug 2** from the 2026-07-12 live E2E (see §4).

### Open

#### PR [#4](https://github.com/Guy-Stern/classification/pull/4) — `feat/silent-managed-installer`

Opened 2026-07-16. Tip `f10889d`. **9 commits, ~3,726 insertions / 227 deletions across 33
files.** A clean fast-forward ahead of `main` (nothing on `main` is missing from the branch),
so it merges without conflict.

It bundles **two unrelated bodies of work**. Worth knowing before you review it — the
installer half and the performance half can be reasoned about independently.

**(a) JARVIS-managed silent installer**

| Path | What |
|------|------|
| `installer_silent/silent_install.ps1` | The headless installer. Everything `Setup.ps1` + `Post-Install.bat` do, unattended, with fatal exit codes. |
| `installer_silent/native_launcher.c` | Native MSVC stub. Native, not managed, because Windows cannot launch a *managed* exe carrying a >4 GB overlay. |
| `installer_silent/silent_extract.ps1` | Extraction stage, base64-embedded in the stub. |
| `installer_silent/build_silent_installer.ps1` | Compiles the stub, ZIPs the payload, appends it as an overlay. Gated by a build-time self-test. |
| `installer_silent/provision_shared.ps1` | Stages the ~15 GB shared data (wheels + weights) once per box. |
| `installer_silent/make_recipe.ps1` | Emits the Workshop `recipe.json`. |
| `installer_silent/verify_against_jarvis.py` | End-to-end checks against the real JARVIS agent/server code. |
| `publish.ps1` / `publish.bat` | One command to publish a version: build → stamp → package → recipe → assemble `Publishes\<version>\`. |
| `docs/AIRGAP_A4000_DEPLOY.md` | Air-gapped A4000 deploy runbook. |

Packaging is **not** a 7-Zip SFX — it is `[launcher][payload.zip][int64 len][magic MCSFX001]`.
The artifact is deliberately **not standalone**: ~170 MB of bundled Python + app code, which
requires the ~15 GB shared data provisioned separately per box. Three sizes not to conflate:
~170 MB downloaded per version, ~15 GB once per box, ~6.8 GB per installed version.

Known small gaps on this branch (not blockers, but review them):

- `-Gpu` is never passed by JARVIS — the recipe's `install_args` is only
  `-InstallDir {INSTALL_DIR}`, so every managed install runs CPU-only. Needs either
  auto-detect in the installer or `-Gpu` added to the recipe.
- The shared-dir preflight tests for *presence*, not *non-emptiness*, and only warns on a
  missing `sam3\`.
- `provision_shared.ps1` takes `-Dest` where the spec says `-SharedDir`; its verify loop
  skips `offline_packages_gpu` / `sam3_runtime` and prints no per-subfolder summary.

**(b) Tier 0–2 geocell mosaic performance overhaul**

| Commit | What |
|--------|------|
| `4aac611` | Tier 0 — windowed, parallel, area-average geocell join |
| `c2b4c92` | Tier 1 — overview-pinned reads + footprint cache + parallel discovery |
| `26b2f28` | Tier 2 — resumable tiled mosaic cache + VRT handoff to classify |
| `7e007c4` | Benchmarks captured at 4000 / 6000 / 12000 px (`benchmarks/results/*.json`) |

Touches `backend/app/mosaic_builder.py` (+506 lines), `mosaic_catalog.py`, `cli.py`, and
adds `benchmarks/` plus tests. **This is the fix for Bug 4** (§4) — it replaces the
full-frame in-RAM composite with a bounded, windowed one.

**Recommendation:** this is the single highest-value thing to merge. Bug 4 is the only
open issue that makes a run *fail* rather than produce awkward output, and the fix has
benchmarks behind it. Consider splitting (a) and (b) into two PRs if the review is slow —
they share no files except `cli.py`.

---

## 3. Branches

### On `origin` (the working fork)

| Branch | Tip | State |
|--------|-----|-------|
| `main` | `5ce6d4f` | Current. |
| `feat/silent-managed-installer` | `f10889d` | **Active** — PR #4, see above. |
| `backup/ofek-original` | `bb5c71f` | Frozen snapshot of the pre-fork codebase (*"Add shadow detection + dynamic max threads checkbox"*). Reference only; never merge. |

The three merged feature branches (`feat/manifest-jp2-tiff-layers`,
`feat/manifest-classify-knobs`, `fix/manifest-tile-names-overwrite`) have been deleted from
the remote — their commits are on `main`.

### On `upstream` (original repo — all stale)

| Branch | Tip | Note |
|--------|-----|------|
| `upstream/main` | `eb54513` | Behind `origin/main` by the entire phase-6+ line of work. |
| `upstream/6-Material-Downgrade` | `fd055ff` | The old active branch. Its work is on `origin/main`. |
| `upstream/claude/adoring-black` | `76da5ed` | Abandoned — OOM guard + float64 fix + tile-mode default. |
| `upstream/claude/condescending-ptolemy` | `02ff605` | Abandoned — vector-rasterization pixel fallback. |
| `upstream/feat/webapp-exe-packaging` | `9f41c10` | Abandoned — superseded by the phase-6 installer work. |
| `upstream/master` | `24ed187` | Ancient. |

Nothing on `upstream` needs merging. Treat it as archive.

---

## 4. Known open bugs

Tracked in full detail in
[MC_MANIFEST_BUGS_2026-07-12.md](MC_MANIFEST_BUGS_2026-07-12.md), reproduced live against
the installed tool through JARVIS on 2026-07-12.

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `[output].overwrite=false` didn't protect the tiled folder; re-runs **duplicated** tiles | HIGH | **Fixed** on `main` (`773682c`, PR #3) |
| 2 | Tile filenames embedded the PID and were dot-prefixed → non-reproducible, glob-hostile | HIGH | **Fixed** on `main` (`773682c`, PR #3) |
| 3 | Per-tile `.xml` material tables are not unified across the cell — index `N` can mean different materials in different tiles | MEDIUM | **Open.** Blocks the CDB `rm`-layer consumer. Nobody is on it. |
| 4 | Mosaic built full-frame in RAM (~7.35 GB measured for one cell) → OOM/thrash on constrained boxes, no tiling fallback | MEDIUM–HIGH | **Fix on PR #4** (Tier 0–2 overhaul). Not on `main`. |

Explicitly *not* an MC bug, recorded so it isn't re-filed: when JARVIS cancels a task it
marks the task cancelled but does not terminate the `MaterialClassification_CLI.exe`
subprocess. That is a JARVIS-side subprocess-lifecycle issue.

---

## 5. Working-tree and infrastructure notes

**Deleted wheels in `git status`.** Three files show as deleted and are *intentionally* so —
they were moved to `offline_installer/_quarantined_wheels/`:

- `offline_packages/torchvision-0.26.0+cpu-cp311-cp311-win_amd64.whl` — would hijack pip's
  unpinned `torchvision` resolution toward CPU.
- `offline_packages_gpu/nvidia_cuda_nvrtc_cu12-12.9.86-*.whl`
- `offline_packages_gpu/nvidia_cuda_runtime_cu12-12.9.79-*.whl` — the 12.9 series needs
  NVIDIA driver ≥ 575; the target A4000 box reports CUDA 12.4 (driver ~550), so these fail
  at runtime with `cudaErrorInsufficientDriver` and silently drop the pipeline to CPU.

The GPU pack is pinned to **CUDA 12.4** (`backend/requirements-gpu.txt`), which is
backward-compatible with newer drivers. See [GPU_ACCELERATION.md](GPU_ACCELERATION.md).

**No CI.** `.github/` contains only `copilot-instructions.md` — there are no workflows.
`tools/sync_mirrors.py --check` and `pytest backend/tests` are described as "CI mode"
throughout the docs, but nothing runs them automatically. Run both by hand before a PR.

**`pytest` is not installed in `.venv`.** Install it before running the suite.

**`graphify-out/` is absent.** The knowledge graph referenced by `CLAUDE.md` and
`ARCHITECTURE.md` was lost in the May-2026 deletion incident and `graphify` is not
installed. Read the source directly. Restoring it means installing graphify and running a
full `/graphify .` build (which has LLM/token cost).

**Recovery caches.** Three out-of-repo caches survived the May-2026 deletion and are useful
when investigating file history: `C:\Users\B\Desktop\workinginstaller2\offline_installer\app\`,
`C:\Users\B\Desktop\LatestInstaller\offline_installer\`, and `C:\ClassificationApp\`. The
last one also holds the HF-gated `facebook/sam3` checkpoint — **do not delete it** without
another copy of `sam3.pt`; no HF token exists on this machine to re-download it.

---

## 6. Suggested next steps

1. **Merge PR #4** (or split it and merge the mosaic half first). It closes Bug 4, the only
   open issue that makes runs fail outright, and it is a clean fast-forward.
2. **Fix Bug 3** — one unified per-cell material table, or a shared cell-wide index space
   across every tile's `.xml`. Needed by the downstream `cdb_build` rm-layer ingest.
3. **Add a minimal CI workflow** — `sync_mirrors.py --check` plus `pytest backend/tests` on
   PRs. Both already exist; nothing runs them. This is a few lines of YAML.
4. **Close the PR #4 review gaps** — the `-Gpu` flag and the preflight/`provision_shared.ps1`
   nits in §2.
