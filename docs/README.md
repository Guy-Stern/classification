# Documentation Index

Topic-specific deep-dives for the classification project.

**New to the project? Start at [../ONBOARDING.md](../ONBOARDING.md)** — day-1 setup, the
mental model behind the pipeline, and a suggested reading order through the code. A visual,
printable version is at [ONBOARDING.pdf](ONBOARDING.pdf).

For the high-level feature overview see [../README.md](../README.md).

| Doc | What's inside |
|-----|---------------|
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | Branches, merged + open PRs, known open bugs, infrastructure gaps, suggested next steps |
| [CLI_GUIDE.md](CLI_GUIDE.md) ([PDF](CLI_GUIDE.pdf)) | Full CLI reference manual — both invocation styles, output files, config schemas, troubleshooting |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Module map, process diagram, `classify_v6` flow, geocell/manifest path, batch mode, AI extraction flow, web-app state, extension points |
| [API_REFERENCE.md](API_REFERENCE.md) | Every FastAPI endpoint (main + MEA calibration tool) |
| [AI_FEATURE_EXTRACTION.md](AI_FEATURE_EXTRACTION.md) | OWLv2 + SAM 2/3, prompts, pre-filters, output organisation, model fallback chain |
| [MEA_CALIBRATION_TOOL.md](MEA_CALIBRATION_TOOL.md) | Profile schema (v2), workflow, profile path, merge rules |
| [GPU_ACCELERATION.md](GPU_ACCELERATION.md) | Engine probe order, CuPy install, FAISS, CUDA 12.4 pinning, frozen-EXE notes |
| [TILING.md](TILING.md) | Tile mode, `suggest_tile_size`, worker pool, memory math |
| [MC_MANIFEST_BUGS_2026-07-12.md](MC_MANIFEST_BUGS_2026-07-12.md) | Live JARVIS E2E bug report against manifest/geocell mode. Bugs 1–2 fixed; 3–4 open |

Operational / deployment docs live at the project root:

- [../ONBOARDING.md](../ONBOARDING.md) — onboarding path (start here)
- [../README.md](../README.md) — project overview
- [../RUNNING_GUIDE.md](../RUNNING_GUIDE.md) — running, troubleshooting
- [../STANDALONE_DEPLOYMENT.md](../STANDALONE_DEPLOYMENT.md) — three deployment methods
- [../SHADOW_DETECTION_FEATURE.md](../SHADOW_DETECTION_FEATURE.md) — shadow → adjacent material inference
- [../CLAUDE.md](../CLAUDE.md) — AI-assistant working notes; also the densest single summary of conventions

Per-component READMEs:

- [../backend/README.md](../backend/README.md)
- [../web_app/README.md](../web_app/README.md)
- [../mea_calibration_tool/README.md](../mea_calibration_tool/README.md)
- [../offline_installer/README.md](../offline_installer/README.md)

Historical (kept for context, marked as such at the top of each):

- [../REFACTORING_SUMMARY.md](../REFACTORING_SUMMARY.md) — original two-step split
- [../FIXES_APPLIED.md](../FIXES_APPLIED.md) — March 2026 PROJ / CRS / dead-code fixes
- [../RASTERIZE_DEBUG.md](../RASTERIZE_DEBUG.md) — Hebrew checklist for "0 pixels rasterized"

## Regenerating the PDFs

```bash
.venv/Scripts/python.exe tools/build_cli_guide_pdf.py    # -> CLI_GUIDE.pdf
.venv/Scripts/python.exe tools/build_onboarding_pdf.py   # -> ONBOARDING.pdf
```

Both render through headless Chrome/Edge `--print-to-pdf`; no Python PDF library is needed.
