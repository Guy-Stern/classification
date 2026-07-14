# -*- coding: utf-8 -*-
"""Benchmark the geocell discovery + mosaic-join phases in isolation.

Times `mosaic_catalog.build_catalog` (discovery) and `mosaic_builder.build_mosaic`
(join) on a real layer set, samples peak RSS, and emits a JSON record. No torch —
this exercises exactly the two single-threaded phases the perf work targets, so
the dev loop is seconds/minutes, not a full classify run.

Usage (from repo root, project venv):
    .venv/Scripts/python.exe benchmarks/bench_mosaic.py \
        --layer "C:/.../RadiometricOrtho/data:1" \
        --south 33 --west 35 --max-side 6000 \
        --label baseline_s6000 --json-out benchmarks/results/baseline_s6000.json

Repeat --layer DIR:PRIORITY for a multi-layer scenario. --warm reuses the
footprint cache (Tier 1) if present; the default is a cold run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

import psutil

# import the app package (backend/ on path)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
from app import mosaic_builder as mb           # noqa: E402
from app import mosaic_catalog as mc           # noqa: E402
from app.geocell import GeoCell                # noqa: E402
from app.manifest import LayerConfig           # noqa: E402


class PeakRSS:
    """Sample this process' RSS in a background thread; report the peak (GB)."""

    def __init__(self, interval: float = 0.05):
        self._proc = psutil.Process()
        self._interval = interval
        self._peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                rss = self._proc.memory_info().rss
                if rss > self._peak:
                    self._peak = rss
            except Exception:
                pass
            self._stop.wait(self._interval)

    def __enter__(self):
        self._peak = self._proc.memory_info().rss
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=1.0)

    @property
    def peak_gb(self) -> float:
        return round(self._peak / 1e9, 3)


def parse_layer(spec: str) -> LayerConfig:
    # "DIR:PRIORITY"  (DIR may contain a drive-letter colon on Windows)
    dir_part, _, prio = spec.rpartition(":")
    if not dir_part:
        raise SystemExit(f"--layer must be DIR:PRIORITY, got {spec!r}")
    return LayerConfig(folder=Path(dir_part), priority=int(prio))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layer", action="append", required=True,
                    help="DIR:PRIORITY (repeatable). 1 = highest priority.")
    ap.add_argument("--south", type=int, required=True)
    ap.add_argument("--west", type=int, required=True)
    ap.add_argument("--max-side", type=int, default=mb.MAX_MOSAIC_SIDE_PX)
    ap.add_argument("--label", default="run")
    ap.add_argument("--json-out", default=None)
    ap.add_argument("--keep-mosaic", action="store_true",
                    help="keep the output mosaic instead of unlinking it")
    args = ap.parse_args()

    layers = [parse_layer(s) for s in args.layer]
    cell = GeoCell(south_lat=args.south, west_lon=args.west)
    bounds = cell.bounds_wgs84()

    scratch = Path(os.environ.get("BENCH_SCRATCH", Path(__file__).parent / "_scratch"))
    scratch.mkdir(parents=True, exist_ok=True)
    out_path = scratch / f"{cell.name}_{args.label}_mosaic.tif"

    rec: dict = {
        "label": args.label,
        "geocell": cell.name,
        "bounds_wsen": [round(b, 5) for b in bounds],
        "max_side_px": args.max_side,
        "layers": [f"{l.folder}:{l.priority}" for l in layers],
    }

    # ── Discovery ────────────────────────────────────────────────────────────
    with PeakRSS() as peak_d:
        t0 = time.perf_counter()
        entries = mc.build_catalog(layers, None, bounds)
        t_disc = time.perf_counter() - t0
    rec["discovery_s"] = round(t_disc, 3)
    rec["discovery_peak_gb"] = peak_d.peak_gb
    rec["n_kept"] = len(entries)

    # ── Join ─────────────────────────────────────────────────────────────────
    with PeakRSS() as peak_m:
        t0 = time.perf_counter()
        info = mb.build_mosaic(entries, bounds, out_path, max_side_px=args.max_side)
        t_join = time.perf_counter() - t0
    rec["join_s"] = round(t_join, 3)
    rec["join_peak_gb"] = peak_m.peak_gb
    rec["mosaic_wh"] = [info["width"], info["height"]]
    rec["gsd_deg"] = info["gsd_deg"]
    rec["n_sources"] = info["n_sources"]
    rec["missing_fraction"] = round(info["missing_fraction"], 4)
    rec["total_s"] = round(t_disc + t_join, 3)

    if not args.keep_mosaic:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass

    print(json.dumps(rec, indent=2))
    if args.json_out:
        jp = Path(args.json_out)
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        print(f"\n[bench] wrote {jp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
