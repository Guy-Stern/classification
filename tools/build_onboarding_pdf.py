"""Render the visual onboarding deck to docs/ONBOARDING.pdf.

Unlike ``build_cli_guide_pdf.py`` (which renders an existing markdown file), this
script owns its own purpose-built HTML: a diagram-led, A4 landscape-of-ideas deck
meant to be read once, front to back, by someone who has never seen the project.
The prose reference lives in ``ONBOARDING.md``; this is the picture version.

Rendered via headless Chrome/Edge ``--print-to-pdf`` — no Python PDF library.

Run with the project venv's Python:
    .venv/Scripts/python.exe tools/build_onboarding_pdf.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "docs" / "ONBOARDING.pdf"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

# Live material palette — keep in sync with shared/mea_defaults.json.
MATERIALS = [
    ("BM_VEGETATION", "#006400", "kmeans", "Trees, grass, crops"),
    ("BM_SAND",       "#E6C882", "kmeans", "Beach, dune, bare pale ground"),
    ("BM_SOIL",       "#55371E", "kmeans", "Dirt, tilled earth"),
    ("BM_ASPHALT",    "#2D2D30", "mask",   "Roads, paved surfaces"),
    ("BM_CONCRETE",   "#B4B4B4", "mask",   "Buildings, rooftops"),
    ("BM_WATER",      "#1C6BA0", "mask",   "Rivers, lakes, pools"),
]

CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 10pt; line-height: 1.5; color: #1b1f24;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
.page {
  width: 210mm; height: 297mm; padding: 16mm 15mm 14mm;
  page-break-after: always; position: relative; overflow: hidden;
}
.page:last-child { page-break-after: auto; }

/* ── cover ─────────────────────────────────────────────────────────────── */
.cover { background: #0b3d62; color: #fff; padding: 0; }
.cover-inner { padding: 40mm 18mm 0; }
.cover h1 { font-size: 40pt; line-height: 1.05; font-weight: 700; letter-spacing: -1px; }
.cover .sub { font-size: 15pt; color: #8fc4e8; margin-top: 10px; font-weight: 300; }
.cover .rule { width: 60mm; height: 3px; background: #4a90c2; margin: 22px 0; }
.cover .blurb { font-size: 11.5pt; color: #cfe2f1; max-width: 128mm; line-height: 1.65; }
.cover .meta {
  position: absolute; bottom: 18mm; left: 18mm; right: 18mm;
  font-size: 9pt; color: #7ba9cc; border-top: 1px solid #1d5480; padding-top: 10px;
  display: flex; justify-content: space-between;
}
.cover-strip { position: absolute; top: 0; right: 0; width: 44mm; height: 297mm; opacity: .9; }

/* ── headings ──────────────────────────────────────────────────────────── */
.kicker { font-size: 8.5pt; letter-spacing: 2.2px; text-transform: uppercase;
          color: #4a90c2; font-weight: 700; margin-bottom: 4px; }
h2 { font-size: 22pt; color: #0b3d62; font-weight: 700; letter-spacing: -.4px;
     line-height: 1.15; margin-bottom: 4mm; }
h3 { font-size: 12pt; color: #0b3d62; font-weight: 600; margin: 6mm 0 2.5mm; }
h4 { font-size: 10pt; color: #134a73; font-weight: 600; margin: 4mm 0 1.5mm; }
p  { margin-bottom: 3mm; }
p.lead { font-size: 11pt; color: #3d4b57; line-height: 1.6; margin-bottom: 5mm; }
.pnum { position: absolute; bottom: 9mm; right: 15mm; font-size: 8pt; color: #9aa7b3; }
.ftag { position: absolute; bottom: 9mm; left: 15mm; font-size: 8pt; color: #9aa7b3; }

code { font-family: "Cascadia Code", Consolas, monospace; font-size: 8.8pt;
       background: #eef2f6; padding: 1px 5px; border-radius: 3px; color: #b1004e; }
pre { background: #0f1722; color: #e6edf3; padding: 4mm 5mm; border-radius: 5px;
      font-family: "Cascadia Code", Consolas, monospace; font-size: 8.4pt;
      line-height: 1.55; margin: 2.5mm 0; overflow: hidden; }
pre code { background: transparent; color: inherit; padding: 0; font-size: inherit; }
pre .c { color: #7d8fa3; }
pre .k { color: #7ee0a8; }

ul { margin: 0 0 3mm 4.5mm; }
li { margin-bottom: 1.6mm; }

/* ── components ───────────────────────────────────────────────────────── */
.card { border: 1px solid #d3dde6; border-radius: 6px; padding: 4mm 4.5mm; background: #fbfdfe; }
.card h4 { margin-top: 0; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; }
.grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 3.5mm; }
.grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3mm; }

.note { border-left: 4px solid #4a90c2; background: #eef5fb; padding: 3mm 4mm;
        border-radius: 0 4px 4px 0; margin: 3mm 0; font-size: 9.4pt; }
.warn { border-left: 4px solid #d9822b; background: #fdf3e8; padding: 3mm 4mm;
        border-radius: 0 4px 4px 0; margin: 3mm 0; font-size: 9.4pt; }
.bad  { border-left: 4px solid #c0392b; background: #fdeeec; padding: 3mm 4mm;
        border-radius: 0 4px 4px 0; margin: 3mm 0; font-size: 9.4pt; }

table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 2.5mm 0; }
th, td { border: 1px solid #ccd6df; padding: 2.2mm 3mm; text-align: left; vertical-align: top; }
th { background: #0b3d62; color: #fff; font-weight: 600; font-size: 8.6pt;
     letter-spacing: .3px; text-transform: uppercase; }
tr:nth-child(even) td { background: #f6f9fb; }

.swatch { display: inline-block; width: 11px; height: 11px; border-radius: 2px;
          border: 1px solid rgba(0,0,0,.25); vertical-align: -1px; margin-right: 5px; }
.pill { display: inline-block; font-size: 7.6pt; font-weight: 700; padding: 1px 7px;
        border-radius: 9px; letter-spacing: .4px; text-transform: uppercase; }
.pill-k { background: #d6ecd8; color: #1e6b2a; }
.pill-m { background: #dde5f0; color: #2b4d7e; }

.step { display: flex; gap: 3.5mm; margin-bottom: 3mm; align-items: flex-start; }
.step-n { flex: 0 0 7mm; height: 7mm; border-radius: 50%; background: #0b3d62;
          color: #fff; font-weight: 700; font-size: 9.5pt; text-align: center;
          line-height: 7mm; }
.step-b { flex: 1; }
.step-b b { color: #0b3d62; }

.chk { font-size: 9.4pt; }
.chk li { list-style: none; margin-left: -4.5mm; padding-left: 6mm; position: relative; }
.chk li:before { content: "☐"; position: absolute; left: 0; color: #4a90c2;
                 font-size: 11pt; line-height: 1; }

/* Reference page runs dense — two full tables plus a glossary on one sheet. */
.dense table { font-size: 8.5pt; }
.dense th, .dense td { padding: 1.5mm 2.4mm; }
.dense h3 { margin: 4mm 0 2mm; }
"""


def _cover_strip() -> str:
    """Decorative material-color strip down the cover's right edge."""
    bands, y, h = [], 0.0, 297.0 / len(MATERIALS)
    for _n, hexc, _s, _d in MATERIALS:
        bands.append(f'<rect x="0" y="{y:.1f}" width="44" height="{h:.1f}" fill="{hexc}"/>')
        y += h
    return (f'<svg class="cover-strip" viewBox="0 0 44 297" preserveAspectRatio="none">'
            f'{"".join(bands)}</svg>')


def _material_rows() -> str:
    out = []
    for name, hexc, source, desc in MATERIALS:
        pill = ('<span class="pill pill-k">kmeans</span>' if source == "kmeans"
                else '<span class="pill pill-m">mask</span>')
        out.append(
            f"<tr><td><span class='swatch' style='background:{hexc}'></span>"
            f"<code>{name}</code></td><td>{hexc}</td><td>{pill}</td><td>{desc}</td></tr>")
    return "".join(out)


# ── SVG diagrams ─────────────────────────────────────────────────────────────

SVG_WHAT = """
<svg viewBox="0 0 660 178" width="100%">
  <defs>
    <marker id="ar" markerWidth="9" markerHeight="9" refX="7" refY="3"
            orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#0b3d62"/></marker>
  </defs>
  <!-- input -->
  <rect x="4" y="18" width="200" height="140" rx="5" fill="#e8e2d4" stroke="#b9ad95"/>
  <rect x="4" y="18" width="200" height="60" fill="#9aa88c"/>
  <path d="M4 96 Q 70 84 130 100 T 204 96 L204 130 Q130 122 70 134 T4 128 Z" fill="#7ba3c4"/>
  <rect x="24" y="30" width="34" height="26" fill="#8a8a86"/>
  <rect x="72" y="26" width="42" height="30" fill="#94918b"/>
  <rect x="132" y="34" width="30" height="22" fill="#8d8a84"/>
  <path d="M4 140 L204 152" stroke="#5b5b58" stroke-width="9" fill="none"/>
  <circle cx="40" cy="88" r="9" fill="#4d6b3f"/><circle cx="62" cy="82" r="7" fill="#557345"/>
  <circle cx="168" cy="86" r="8" fill="#4d6b3f"/>
  <text x="104" y="172" text-anchor="middle" font-size="10" fill="#5a6672"
        font-family="Segoe UI">input orthophoto (.tif / .jp2)</text>

  <!-- pipeline -->
  <line x1="212" y1="88" x2="248" y2="88" stroke="#0b3d62" stroke-width="2" marker-end="url(#ar)"/>
  <rect x="254" y="52" width="150" height="72" rx="6" fill="#0b3d62"/>
  <text x="329" y="80" text-anchor="middle" font-size="12" fill="#fff"
        font-family="Segoe UI" font-weight="600">classify_v6</text>
  <text x="329" y="98" text-anchor="middle" font-size="9" fill="#8fc4e8"
        font-family="Segoe UI">KMeans + masks</text>
  <line x1="412" y1="88" x2="448" y2="88" stroke="#0b3d62" stroke-width="2" marker-end="url(#ar)"/>

  <!-- output -->
  <rect x="456" y="18" width="200" height="140" rx="5" fill="#55371E" stroke="#3b3b3b"/>
  <rect x="456" y="18" width="200" height="60" fill="#006400"/>
  <path d="M456 96 Q522 84 582 100 T656 96 L656 130 Q582 122 522 134 T456 128 Z" fill="#1C6BA0"/>
  <rect x="476" y="30" width="34" height="26" fill="#B4B4B4"/>
  <rect x="524" y="26" width="42" height="30" fill="#B4B4B4"/>
  <rect x="584" y="34" width="30" height="22" fill="#B4B4B4"/>
  <path d="M456 140 L656 152" stroke="#2D2D30" stroke-width="9" fill="none"/>
  <circle cx="492" cy="88" r="9" fill="#006400"/><circle cx="514" cy="82" r="7" fill="#006400"/>
  <circle cx="620" cy="86" r="8" fill="#006400"/>
  <text x="556" y="172" text-anchor="middle" font-size="10" fill="#5a6672"
        font-family="Segoe UI">classified.tif + .xml + .txr / .txs</text>
</svg>
"""

SVG_TWO_SOURCES = """
<svg viewBox="0 0 660 260" width="100%">
  <defs>
    <marker id="a2" markerWidth="9" markerHeight="9" refX="7" refY="3"
            orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#5a6672"/></marker>
  </defs>
  <rect x="252" y="6" width="156" height="34" rx="5" fill="#1b1f24"/>
  <text x="330" y="28" text-anchor="middle" font-size="11.5" fill="#fff"
        font-family="Segoe UI" font-weight="600">input raster RGB</text>

  <path d="M300 42 L170 74" stroke="#5a6672" stroke-width="1.6" marker-end="url(#a2)" fill="none"/>
  <path d="M360 42 L490 74" stroke="#5a6672" stroke-width="1.6" marker-end="url(#a2)" fill="none"/>

  <!-- kmeans column -->
  <rect x="14" y="80" width="300" height="168" rx="7" fill="#f2f8f3" stroke="#a8ceb0"/>
  <rect x="14" y="80" width="300" height="30" rx="7" fill="#2e7d4a"/>
  <rect x="14" y="100" width="300" height="10" fill="#2e7d4a"/>
  <text x="164" y="101" text-anchor="middle" font-size="11" fill="#fff"
        font-family="Segoe UI" font-weight="700">SOURCE = "kmeans"</text>
  <text x="164" y="130" text-anchor="middle" font-size="9.6" fill="#33503c"
        font-family="Segoe UI">Color clustering separates these cleanly.</text>
  <text x="164" y="145" text-anchor="middle" font-size="9.6" fill="#33503c"
        font-family="Segoe UI">One anchor color each — wide spectral gaps.</text>
  <rect x="34"  y="160" width="86" height="30" rx="4" fill="#006400"/>
  <text x="77"  y="179" text-anchor="middle" font-size="8.6" fill="#fff" font-family="Segoe UI">VEGETATION</text>
  <rect x="126" y="160" width="76" height="30" rx="4" fill="#E6C882"/>
  <text x="164" y="179" text-anchor="middle" font-size="8.6" fill="#4a3a13" font-family="Segoe UI">SAND</text>
  <rect x="208" y="160" width="76" height="30" rx="4" fill="#55371E"/>
  <text x="246" y="179" text-anchor="middle" font-size="8.6" fill="#fff" font-family="Segoe UI">SOIL</text>
  <text x="164" y="214" text-anchor="middle" font-size="9.4" fill="#1e6b2a"
        font-family="Segoe UI" font-weight="600">Hungarian 1:1 cluster → material</text>
  <text x="164" y="231" text-anchor="middle" font-size="9" fill="#5a6672"
        font-family="Segoe UI">no material can absorb two clusters</text>

  <!-- mask column -->
  <rect x="346" y="80" width="300" height="168" rx="7" fill="#f3f6fb" stroke="#adc0dc"/>
  <rect x="346" y="80" width="300" height="30" rx="7" fill="#2b4d7e"/>
  <rect x="346" y="100" width="300" height="10" fill="#2b4d7e"/>
  <text x="496" y="101" text-anchor="middle" font-size="11" fill="#fff"
        font-family="Segoe UI" font-weight="700">SOURCE = "mask"</text>
  <text x="496" y="130" text-anchor="middle" font-size="9.6" fill="#2b3d55"
        font-family="Segoe UI">Color CANNOT tell these apart — a grey roof</text>
  <text x="496" y="145" text-anchor="middle" font-size="9.6" fill="#2b3d55"
        font-family="Segoe UI">and a grey road are the same pixels.</text>
  <rect x="366" y="160" width="86" height="30" rx="4" fill="#2D2D30"/>
  <text x="409" y="179" text-anchor="middle" font-size="8.6" fill="#fff" font-family="Segoe UI">ASPHALT</text>
  <rect x="458" y="160" width="76" height="30" rx="4" fill="#B4B4B4"/>
  <text x="496" y="179" text-anchor="middle" font-size="8.6" fill="#26292c" font-family="Segoe UI">CONCRETE</text>
  <rect x="540" y="160" width="76" height="30" rx="4" fill="#1C6BA0"/>
  <text x="578" y="179" text-anchor="middle" font-size="8.6" fill="#fff" font-family="Segoe UI">WATER</text>
  <text x="496" y="214" text-anchor="middle" font-size="9.4" fill="#2b4d7e"
        font-family="Segoe UI" font-weight="600">SDE &gt; shapefile &gt; water raster &gt; SAM3</text>
  <text x="496" y="231" text-anchor="middle" font-size="9" fill="#5a6672"
        font-family="Segoe UI">masks are authoritative — they always paint</text>
</svg>
"""

SVG_V6 = """
<svg viewBox="0 0 660 300" width="100%">
  <defs>
    <marker id="a3" markerWidth="10" markerHeight="10" refX="8" refY="3.2"
            orient="auto"><path d="M0,0 L0,6.4 L9,3.2 z" fill="#0b3d62"/></marker>
  </defs>
  <!-- phase 1 -->
  <rect x="18" y="8" width="624" height="58" rx="6" fill="#eaf1f8" stroke="#a8c3dd"/>
  <circle cx="46" cy="37" r="13" fill="#2b4d7e"/>
  <text x="46" y="42" text-anchor="middle" font-size="13" fill="#fff" font-family="Segoe UI" font-weight="700">1</text>
  <text x="70" y="30" font-size="11.5" fill="#0b3d62" font-family="Segoe UI" font-weight="700">ACQUIRE MASKS</text>
  <text x="70" y="47" font-size="9.4" fill="#3d4b57" font-family="Segoe UI">water ← mask GeoTIFF (band 1 &gt; 0)  ·  roads ← SDE buffered / SAM3  ·  buildings ← SDE / SAM3</text>
  <text x="612" y="42" text-anchor="end" font-size="8.6" fill="#7d8fa3" font-family="Segoe UI">_acquire_mask()</text>
  <line x1="330" y1="66" x2="330" y2="82" stroke="#0b3d62" stroke-width="2" marker-end="url(#a3)"/>

  <!-- phase 2 -->
  <rect x="18" y="86" width="624" height="70" rx="6" fill="#eef7f0" stroke="#a8ceb0"/>
  <circle cx="46" cy="121" r="13" fill="#2e7d4a"/>
  <text x="46" y="126" text-anchor="middle" font-size="13" fill="#fff" font-family="Segoe UI" font-weight="700">2</text>
  <text x="70" y="108" font-size="11.5" fill="#1e6b2a" font-family="Segoe UI" font-weight="700">KMEANS — on the 3 kmeans-source classes ONLY</text>
  <text x="70" y="125" font-size="9.4" fill="#3d4b57" font-family="Segoe UI">_split_classes_by_source() drops the mask classes first — they have no spectral</text>
  <text x="70" y="140" font-size="9.4" fill="#3d4b57" font-family="Segoe UI">signature and would poison cluster assignment for everything else.</text>
  <text x="612" y="126" text-anchor="end" font-size="8.6" fill="#7d8fa3" font-family="Segoe UI">core.classify_and_export()</text>
  <line x1="330" y1="156" x2="330" y2="172" stroke="#0b3d62" stroke-width="2" marker-end="url(#a3)"/>

  <!-- phase 3 -->
  <rect x="18" y="176" width="624" height="70" rx="6" fill="#fbf2ea" stroke="#e0bf9a"/>
  <circle cx="46" cy="211" r="13" fill="#b5651d"/>
  <text x="46" y="216" text-anchor="middle" font-size="13" fill="#fff" font-family="Segoe UI" font-weight="700">3</text>
  <text x="70" y="198" font-size="11.5" fill="#8a4a12" font-family="Segoe UI" font-weight="700">FUSE — paint masks over the KMeans result</text>
  <text x="70" y="215" font-size="9.4" fill="#3d4b57" font-family="Segoe UI">order: water → roads → buildings.  Later masks win where they overlap.</text>
  <text x="70" y="230" font-size="9.4" fill="#3d4b57" font-family="Segoe UI">The soft-prior veto is deliberately disabled (thresholds &gt; 1.0) — masks always paint.</text>
  <rect x="452" y="192" width="52" height="20" rx="3" fill="#1C6BA0"/>
  <text x="478" y="206" text-anchor="middle" font-size="8" fill="#fff" font-family="Segoe UI">water</text>
  <rect x="508" y="192" width="52" height="20" rx="3" fill="#2D2D30"/>
  <text x="534" y="206" text-anchor="middle" font-size="8" fill="#fff" font-family="Segoe UI">roads</text>
  <rect x="564" y="192" width="60" height="20" rx="3" fill="#B4B4B4"/>
  <text x="594" y="206" text-anchor="middle" font-size="8" fill="#26292c" font-family="Segoe UI">buildings</text>
  <line x1="330" y1="246" x2="330" y2="262" stroke="#0b3d62" stroke-width="2" marker-end="url(#a3)"/>

  <!-- phase 4 -->
  <rect x="18" y="266" width="624" height="30" rx="6" fill="#0b3d62"/>
  <circle cx="46" cy="281" r="11" fill="#fff"/>
  <text x="46" y="286" text-anchor="middle" font-size="12" fill="#0b3d62" font-family="Segoe UI" font-weight="700">4</text>
  <text x="70" y="286" font-size="10.5" fill="#fff" font-family="Segoe UI" font-weight="600">REWRITE XML — all 6 materials in the table, though only 3 came from KMeans</text>
</svg>
"""

SVG_GEOCELL = """
<svg viewBox="0 0 660 250" width="100%">
  <defs>
    <marker id="a4" markerWidth="9" markerHeight="9" refX="7" refY="3"
            orient="auto"><path d="M0,0 L0,6 L8,3 z" fill="#5a6672"/></marker>
  </defs>
  <!-- layers -->
  <text x="14" y="14" font-size="9" fill="#0b3d62" font-family="Segoe UI" font-weight="700">PRIORITY LAYERS</text>
  <g opacity=".95">
    <rect x="26" y="66" width="132" height="78" rx="3" fill="#b8a98c" stroke="#8d7f66"/>
    <text x="98" y="137" text-anchor="middle" font-size="8.6" fill="#3f382b" font-family="Segoe UI">priority 2 — archive_2019</text>
    <rect x="14" y="42" width="132" height="72" rx="3" fill="#9db98c" stroke="#6f8a60"/>
    <text x="80" y="84" text-anchor="middle" font-size="8.6" fill="#2c3a26" font-family="Segoe UI">priority 1 — 2024_campaign</text>
  </g>
  <text x="80" y="164" text-anchor="middle" font-size="8.6" fill="#5a6672" font-family="Segoe UI">*.tif · *.tiff · *.jp2</text>
  <text x="80" y="178" text-anchor="middle" font-size="8.6" fill="#5a6672" font-family="Segoe UI">lower number = on top</text>

  <path d="M166 92 L206 92" stroke="#5a6672" stroke-width="1.6" marker-end="url(#a4)" fill="none"/>

  <!-- geocell grid -->
  <text x="216" y="14" font-size="9" fill="#0b3d62" font-family="Segoe UI" font-weight="700">GEOCELL N33E035</text>
  <rect x="216" y="34" width="122" height="122" fill="#f4f8fb" stroke="#0b3d62" stroke-width="1.6"/>
  <g stroke="#c3d3e0" stroke-width=".7">
    <line x1="246" y1="34" x2="246" y2="156"/><line x1="277" y1="34" x2="277" y2="156"/>
    <line x1="307" y1="34" x2="307" y2="156"/>
    <line x1="216" y1="64" x2="338" y2="64"/><line x1="216" y1="95" x2="338" y2="95"/>
    <line x1="216" y1="125" x2="338" y2="125"/>
  </g>
  <rect x="216" y="34" width="61" height="61" fill="#9db98c" opacity=".75"/>
  <rect x="277" y="64" width="61" height="61" fill="#b8a98c" opacity=".75"/>
  <text x="277" y="172" text-anchor="middle" font-size="8.6" fill="#5a6672" font-family="Segoe UI">1° tall · width by lat zone</text>
  <text x="277" y="186" text-anchor="middle" font-size="8.6" fill="#5a6672" font-family="Segoe UI">intersect → drop non-overlapping</text>

  <path d="M346 92 L386 92" stroke="#5a6672" stroke-width="1.6" marker-end="url(#a4)" fill="none"/>

  <!-- mosaic -->
  <text x="396" y="14" font-size="9" fill="#0b3d62" font-family="Segoe UI" font-weight="700">MOSAIC (EPSG:4326)</text>
  <rect x="396" y="34" width="122" height="122" rx="3" fill="#a5b58f" stroke="#6f8a60"/>
  <rect x="396" y="34" width="61" height="61" fill="#9db98c"/>
  <rect x="457" y="95" width="61" height="61" fill="#b8a98c"/>
  <text x="457" y="172" text-anchor="middle" font-size="8.6" fill="#5a6672" font-family="Segoe UI">priority LAST-WINS composite</text>
  <text x="457" y="186" text-anchor="middle" font-size="8.6" fill="#5a6672" font-family="Segoe UI">near-black borders excluded</text>

  <path d="M526 92 L566 92" stroke="#5a6672" stroke-width="1.6" marker-end="url(#a4)" fill="none"/>

  <!-- tiles -->
  <text x="576" y="14" font-size="9" fill="#0b3d62" font-family="Segoe UI" font-weight="700">TILED OUTPUT</text>
  <g>
    <rect x="576" y="34" width="28" height="28" fill="#006400"/><rect x="606" y="34" width="28" height="28" fill="#B4B4B4"/>
    <rect x="576" y="64" width="28" height="28" fill="#2D2D30"/><rect x="606" y="64" width="28" height="28" fill="#006400"/>
    <rect x="576" y="94" width="28" height="28" fill="#E6C882"/><rect x="606" y="94" width="28" height="28" fill="#1C6BA0"/>
    <rect x="576" y="124" width="28" height="28" fill="#55371E"/><rect x="606" y="124" width="28" height="28" fill="#006400"/>
  </g>
  <text x="605" y="172" text-anchor="middle" font-size="8.2" fill="#5a6672" font-family="Segoe UI">N33E035_tile_r{r}_c{c}.tif</text>
  <text x="605" y="186" text-anchor="middle" font-size="8.2" fill="#5a6672" font-family="Segoe UI">deterministic · always a folder</text>

  <rect x="14" y="204" width="632" height="38" rx="5" fill="#eef5fb" stroke="#a8c3dd"/>
  <text x="26" y="220" font-size="9.2" fill="#0b3d62" font-family="Segoe UI" font-weight="600">manifest.py → geocell.py → mosaic_catalog.py → mosaic_builder.py → pipeline.classify_v6()</text>
  <text x="26" y="234" font-size="8.8" fill="#5a6672" font-family="Segoe UI">Tiling is FORCED at 512 px so the deliverable shape is deterministic regardless of the worker's free RAM.</text>
</svg>
"""


def build_html() -> str:
    p = []  # pages

    # ── 1. cover ─────────────────────────────────────────────────────────────
    p.append(f"""
<div class="page cover">
  {_cover_strip()}
  <div class="cover-inner">
    <h1>Material<br/>Classification</h1>
    <div class="sub">Visual onboarding guide</div>
    <div class="rule"></div>
    <div class="blurb">
      Turn an orthophoto into a material map. Six classes, two very different sources of
      truth, and one orchestrator that fuses them. This deck is the picture version of
      <b>ONBOARDING.md</b> — read it front to back once, then keep the markdown open.
    </div>
  </div>
  <div class="meta">
    <span>React + TypeScript · FastAPI · PyTorch · rasterio</span>
    <span>main @ 5ce6d4f · 2026-07-27</span>
  </div>
</div>""")

    # ── 2. what it does ──────────────────────────────────────────────────────
    p.append(f"""
<div class="page">
  <div class="kicker">01 · The problem</div>
  <h2>What this thing does</h2>
  <p class="lead">You give it a georeferenced aerial image. It gives back the same scene with
  every pixel recolored by what it is <i>made of</i> — plus a sidecar material table that a
  simulation toolchain reads.</p>
  {SVG_WHAT}
  <h3>The six materials</h3>
  <table>
    <tr><th style="width:31%">Class</th><th style="width:13%">Color</th>
        <th style="width:16%">Source</th><th>Typical content</th></tr>
    {_material_rows()}
  </table>
  <div class="grid2" style="margin-top:4mm">
    <div class="card">
      <h4>What gets written</h4>
      <ul>
        <li><code>&lt;stem&gt;.tif</code> — the classified raster, EPSG:4326</li>
        <li><code>&lt;stem&gt;.xml</code> — <code>&lt;Composite_Material_Table&gt;</code>, ARGB colors</li>
        <li><code>&lt;stem&gt;.txr</code> / <code>.txs</code> — simulator sidecars</li>
      </ul>
    </div>
    <div class="card">
      <h4>Why it isn't just clustering</h4>
      <p style="margin:0">Color alone can't identify a material. A grey roof, a grey road
      and a shadow are the same pixels. That single fact drives the entire architecture —
      see the next page.</p>
    </div>
  </div>
  <div class="ftag">01 · The problem</div><div class="pnum">2</div>
</div>""")

    # ── 3. two sources ───────────────────────────────────────────────────────
    p.append(f"""
<div class="page">
  <div class="kicker">02 · The core idea</div>
  <h2>Two sources of truth</h2>
  <p class="lead">Every material declares a <code>source</code>. Natural ground cover separates
  cleanly in color space, so it comes from KMeans. Built surfaces don't, so they come from a
  mask. Internalise this one split and most of the codebase stops being surprising.</p>
  {SVG_TWO_SOURCES}
  <h3>Two consequences you will hit</h3>
  <div class="grid2">
    <div class="card" style="border-color:#e0a9a0;background:#fdf6f5">
      <h4 style="color:#a3372a">Never mix mask classes into a shared KMeans</h4>
      <p style="margin:0">They have no useful spectral signature and they poison cluster
      assignment for everything else. This was a real production bug (<code>240c4ca</code>):
      batch outputs came back almost entirely water.
      <code>_split_classes_by_source()</code> exists purely to enforce it.</p>
    </div>
    <div class="card" style="border-color:#adc0dc;background:#f5f8fc">
      <h4 style="color:#2b4d7e">Masks are authoritative — they always paint</h4>
      <p style="margin:0">A soft-prior "veto" system exists in <code>pipeline.py</code>, but
      every threshold is set above <code>1.0</code>, which disables it. If a shapefile says a
      polygon is a building, it is painted concrete even if the pixels look green.</p>
    </div>
  </div>
  <div class="note" style="margin-top:4mm"><b>Anchors.</b> Reference colors live in
  <code>shared/mea_defaults.json</code> — deliberately <b>one anchor per KMeans material</b>.
  Wide spectral separation makes a single anchor more robust than overlapping anchor clouds.
  Users can override them with the MEA Calibration Tool, which writes
  <code>%ProgramData%\\MaterialClassification\\mea_calibration_profile.json</code>.</div>
  <div class="ftag">02 · The core idea</div><div class="pnum">3</div>
</div>""")

    # ── 4. classify_v6 ───────────────────────────────────────────────────────
    p.append(f"""
<div class="page">
  <div class="kicker">03 · The pipeline</div>
  <h2>classify_v6 — the orchestrator</h2>
  <p class="lead">Every entry point — web app, CLI, manifest mode — funnels into this one
  function in <code>backend/app/pipeline.py</code>. Four phases, in order.</p>
  {SVG_V6}
  <div class="grid3" style="margin-top:5mm">
    <div class="card">
      <h4>Hungarian assignment</h4>
      <p style="margin:0;font-size:9.2pt">Phase 2 uses strict 1:1 cluster→material matching
      rather than nearest-anchor, so one material can never absorb multiple clusters and
      starve the others.</p>
    </div>
    <div class="card">
      <h4>Tiling</h4>
      <p style="margin:0;font-size:9.2pt">Big rasters are split into tiles and classified in
      a worker pool. <code>suggest_tile_size()</code> picks the largest power-of-2 side that
      fits current free RAM.</p>
    </div>
    <div class="card">
      <h4>Not padded</h4>
      <p style="margin:0;font-size:9.2pt">Outputs used to be padded to power-of-2
      <i>dimensions</i>. Removed in <code>fd055ff</code> — it produced a black L-shaped strip
      on every output.</p>
    </div>
  </div>

  <h3 style="margin-top:4mm">Phase 1 in detail — mask sources, in resolution order</h3>
  <table style="font-size:8.6pt">
    <tr><th style="width:20%">Source</th><th style="width:27%">Configured by</th><th>Notes</th></tr>
    <tr><td>Water-mask GeoTIFF</td><td><code>water_mask</code> in config, or <code>--water-mask</code></td>
        <td>Band 1 &gt; 0 = water. Painted directly, no vector resolve.</td></tr>
    <tr><td>Esri SDE</td><td><code>sde</code> block in config</td>
        <td>Buildings + roads via an <code>arcpy</code> subprocess under ArcGIS Pro's Python. Road
        lines buffered to polygons by a 3-tier width; bounds split into ≤5 km tiles to dodge
        the 2 GB shapefile cap.</td></tr>
    <tr><td>Shapefiles</td><td>path arrays in config</td>
        <td>Envelopes intersected with raster bounds (50 m buffer); only intersecting features are
        read, reprojected, unioned to a temp shp. Missing <code>.prj</code> → skipped.</td></tr>
    <tr><td>SAM3</td><td><code>--sam3-enabled</code> (default on)</td>
        <td>Text-prompted segmentation. Falls back to OWLv2 + SAM2 when SAM3 weights are absent.</td></tr>
  </table>
  <div class="ftag">03 · The pipeline</div><div class="pnum">4</div>
</div>""")

    # ── 5. entry points ──────────────────────────────────────────────────────
    p.append("""
<div class="page">
  <div class="kicker">04 · Ways in</div>
  <h2>Four entry points, one pipeline</h2>
  <p class="lead">They differ only in how you feed them work. All four end up in
  <code>classify_v6</code>.</p>

  <div class="grid2">
    <div class="card">
      <h4>1 · Web app <span class="pill pill-m">primary</span></h4>
      <p style="font-size:9.2pt">React + Vite frontend, FastAPI backend. Pick a raster, tune
      materials, watch SSE progress on a Leaflet map.</p>
      <pre><span class="c"># two terminals</span>
.venv/Scripts/python.exe -m uvicorn \\
  backend.app.main:app --port 8000
cd web_app &amp;&amp; npm run dev   <span class="c"># :5174</span></pre>
      <p style="font-size:8.8pt;margin:0"><b>Gotcha:</b> FastAPI also serves
      <code>web_app/dist/</code> at <code>:8000</code> — that's the <i>last built</i>
      frontend, not your live edits.</p>
    </div>

    <div class="card">
      <h4>2 · CLI — file / folder</h4>
      <p style="font-size:9.2pt">Same pipeline, scripted. The positional form auto-implies
      <code>--mea --sam3-enabled</code>.</p>
      <pre>cli.py &lt;input.tif&gt; &lt;output.tif&gt;
cli.py &lt;in_folder&gt;  &lt;out_folder&gt;</pre>
      <p style="font-size:8.8pt;margin:0">Folder→folder is <b>batch mode</b>: one shared
      KMeans model across the whole folder, so tile boundaries don't show as color
      discontinuities. Trained on kmeans-source classes only.</p>
    </div>

    <div class="card">
      <h4>3 · CLI — geocell manifest</h4>
      <p style="font-size:9.2pt">One TOML = one OGC CDB geocell = one run. Built for the
      JARVIS automation. Detailed on the next page.</p>
      <pre>cli.py --manifest geocell.toml</pre>
      <p style="font-size:8.8pt;margin:0">Runnable example ships in the repo at
      <code>installer_assets/examples/sample_data/geocell.toml</code>.</p>
    </div>

    <div class="card">
      <h4>4 · MEA Calibration Tool</h4>
      <p style="font-size:9.2pt">Separate FastAPI + React app on port 8100. Sample real
      pixels from a raster to derive reference colors, write a profile, and the main app
      picks it up automatically.</p>
      <p style="font-size:8.8pt;margin:0">The two apps are fully decoupled — the calibration
      tool only writes the profile JSON, the main app only reads it. No IPC.</p>
    </div>
  </div>

  <div class="warn" style="margin-top:4mm"><b><code>tkinter_app.py</code> also exists and is
  unmaintained.</b> It hasn't tracked the phase 4–6 MEA changes. Don't use it as a reference
  for current behaviour, and don't spend time on it.</div>

  <h3>What differs between them</h3>
  <table>
    <tr><th style="width:24%"></th><th style="width:19%">Web app</th><th style="width:19%">CLI file/folder</th>
        <th style="width:19%">CLI manifest</th><th>Calibration tool</th></tr>
    <tr><td><b>Input</b></td><td>one raster, picked in the UI</td><td>file or folder</td>
        <td>priority layers of a geocell</td><td>one raster, for sampling</td></tr>
    <tr><td><b>Output shape</b></td><td>single raster</td><td>raster, or one per input</td>
        <td><b>always</b> a tile folder</td><td>a profile JSON</td></tr>
    <tr><td><b>Shared KMeans</b></td><td>batch endpoint only</td><td>yes, in folder mode</td>
        <td>n/a — one mosaic</td><td>n/a</td></tr>
    <tr><td><b>Needs PyTorch</b></td><td>only for SAM3 / AI features</td><td>only with SAM3</td>
        <td>no, if <code>sam3 = false</code></td><td>no</td></tr>
  </table>
  <div class="ftag">04 · Ways in</div><div class="pnum">5</div>
</div>""")

    # ── 6. geocell mode ──────────────────────────────────────────────────────
    p.append(f"""
<div class="page">
  <div class="kicker">05 · Geocell mode</div>
  <h2>The manifest path</h2>
  <p class="lead">The newest and least obvious mode. Instead of one input raster you declare
  <b>priority layers</b> of source imagery for one map cell; the tool composites them, then
  classifies the composite.</p>
  {SVG_GEOCELL}
  <div class="grid2" style="margin-top:4mm">
    <div>
      <h4 style="margin-top:0">A minimal manifest</h4>
      <pre><span class="k">[geocell]</span>
south_lat = 33
west_lon  = 35

<span class="k">[[layers]]</span>
folder   = <span class="c">"D:/orthos/2024_campaign"</span>
priority = 1        <span class="c"># 1 = highest</span>

<span class="k">[[layers]]</span>
folder   = <span class="c">"D:/orthos/archive_2019"</span>
priority = 2

<span class="k">[classify]</span>          <span class="c"># optional</span>
sam3 = false        <span class="c"># no PyTorch needed</span>

<span class="k">[output]</span>
path      = <span class="c">"D:/out/N33E035.tif"</span>
overwrite = false</pre>
    </div>
    <div>
      <h4 style="margin-top:0">Design decisions that look like bugs</h4>
      <ul style="font-size:9.2pt">
        <li><b>Output is always a folder</b>, never the <code>[output].path</code> file.
        Tiling is forced so the shape is deterministic; the overwrite guard therefore checks
        the <code>_classified_tiles/</code> directory.</li>
        <li><b>Tile names derive from the cell name</b>, not the temp mosaic's stem — so the
        same manifest yields byte-identical filenames across runs.</li>
        <li><b>Lower priority number = on top</b>, matching QGIS / Photoshop. Flat
        <code>sources</code> entries composite <i>below</i> every layer.</li>
        <li><b>Unknown table = hard error.</b> Every model sets <code>extra="forbid"</code>,
        so a typo'd table name fails loudly instead of being silently ignored.</li>
      </ul>
      <div class="bad" style="font-size:8.8pt"><b>Known limit (Bug 4).</b> The mosaic is
      built full-frame in RAM — ~7.35 GB measured for one cell. On a constrained box that's
      the ceiling. The windowed rewrite sits on the open PR #4 branch.</div>
    </div>
  </div>
  <div class="ftag">05 · Geocell mode</div><div class="pnum">6</div>
</div>""")

    # ── 7. repo map ──────────────────────────────────────────────────────────
    p.append("""
<div class="page">
  <div class="kicker">06 · The codebase</div>
  <h2>Where things live &amp; what to read</h2>
  <p class="lead"><b>Do not start with <code>core.py</code>.</b> It is 4,538 lines and it is
  the implementation, not the design. Read in this order instead.</p>

  <table>
    <tr><th style="width:7%">#</th><th style="width:35%">File</th><th style="width:11%">Lines</th><th>Why</th></tr>
    <tr><td>1</td><td><code>backend/app/pipeline.py</code></td><td>765</td>
        <td>The orchestrator. Best single file for understanding the product.</td></tr>
    <tr><td>2</td><td><code>cli.py</code></td><td>955</td>
        <td>Every mode's entry logic, plus the embedded user guide (<code>--examples</code>).</td></tr>
    <tr><td>3</td><td><code>backend/app/main.py</code></td><td>1,334</td>
        <td>The HTTP surface + SSE progress / cancellation plumbing.</td></tr>
    <tr><td>4</td><td><code>backend/app/manifest.py</code></td><td>255</td>
        <td>Small and well-commented; shows the geocell design cleanly.</td></tr>
    <tr><td>5</td><td><code>backend/app/shapefile_resolver.py</code></td><td>371</td>
        <td>The "smart-trim" GIS resolver — a genuinely clever bit.</td></tr>
    <tr><td>6</td><td><code>backend/app/core.py</code></td><td>4,538</td>
        <td>Reference only. Grep it; don't read it front to back.</td></tr>
  </table>

  <h3 style="margin-top:5mm">Module map</h3>
  <div class="grid2">
    <div class="card" style="padding:3mm 3.5mm">
      <h4>backend/app/</h4>
      <ul style="font-size:8.3pt;line-height:1.42">
        <li><code>core.py</code> — KMeans engine, tiling, MEA export, XML/.txr/.txs writers</li>
        <li><code>pipeline.py</code> — <code>classify_v6</code></li>
        <li><code>main.py</code> — FastAPI endpoints, SSE, cancellation</li>
        <li><code>road_extraction.py</code> — OWLv2 + SAM2/3, <code>FEATURE_CONFIGS</code></li>
        <li><code>manifest.py</code> · <code>geocell.py</code> · <code>mosaic_catalog.py</code> ·
            <code>mosaic_builder.py</code> — geocell mode</li>
        <li><code>shapefile_config.py</code> · <code>shapefile_resolver.py</code> — GIS masks</li>
        <li><code>sde_extractor.py</code> · <code>sde_arcpy_worker.py</code> — Esri SDE</li>
        <li><code>config.py</code> · <code>mea_profile.py</code> — persisted settings</li>
      </ul>
    </div>
    <div class="card" style="padding:3mm 3.5mm">
      <h4>everything else</h4>
      <ul style="font-size:8.3pt;line-height:1.42">
        <li><code>web_app/src/</code> — <code>App.tsx</code>, <code>api/client.ts</code>,
            <code>store/index.tsx</code> (reducer = UI truth), <code>components/sidebar/</code></li>
        <li><code>shared/mea_defaults.json</code> — the live anchors</li>
        <li><code>tools/sync_mirrors.py</code> — mirror discipline</li>
        <li><code>offline_installer/</code> — air-gapped payload + a full app mirror</li>
        <li><code>installer_assets/</code> — files the pre-compiled installer exe predates</li>
        <li><code>backend/tests/</code> — 9 modules, 84 tests</li>
        <li><code>docs/</code> — topic deep-dives</li>
      </ul>
    </div>
  </div>

  <div class="bad" style="margin-top:2.5mm;font-size:9pt"><b>Mirror discipline — the #1 way to
  break this repo.</b> <code>offline_installer/app/{backend,web_app,shared}</code> is a
  <b>byte-identical mirror</b> of the dev tree, because the offline installer ships pre-built app
  files. Edit one side and not the other and the installed product silently differs from the code
  you tested. After editing a mirrored file run <code>tools/sync_mirrors.py</code>.</div>
  <div class="ftag">06 · The codebase</div><div class="pnum">7</div>
</div>""")

    # ── 8. day one ───────────────────────────────────────────────────────────
    p.append("""
<div class="page">
  <div class="kicker">07 · Day one</div>
  <h2>Get it running</h2>

  <div class="bad"><b>The single most important environment rule:</b> always use
  <code>.venv\\Scripts\\python.exe</code>. A bare <code>python</code> on a dev box here may
  resolve to a different install where <code>pkg_resources</code> is gone and the
  GroundingDINO / <code>triton-windows</code> chain breaks. Every command below spells out the
  interpreter on purpose.</div>

  <div class="step"><div class="step-n">1</div><div class="step-b">
    <b>Prerequisites.</b> Python <b>3.11.x</b> (not 3.12+, not 3.14) — 3.11.9 is what the
    installer ships. Node 16+ only if you want the web UI. An NVIDIA GPU is optional;
    everything works CPU-only.
  </div></div>

  <div class="step"><div class="step-n">2</div><div class="step-b">
    <b>Create the venv and install.</b>
    <pre>py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
<span class="c"># optional GPU pack (CuPy + CUDA 12.4-pinned runtime wheels)</span>
.venv/Scripts/python.exe -m pip install -r backend/requirements-gpu.txt
<span class="c"># frontend, only if you want the web UI</span>
cd web_app &amp;&amp; npm install</pre>
  </div></div>

  <div class="step"><div class="step-n">3</div><div class="step-b">
    <b>Check which compute backend you got.</b>
    <pre>.venv/Scripts/python.exe -c "from backend.app import core; print(core._ACCEL_ENGINE)"</pre>
    The probe order is <code>faiss-gpu → cupy → faiss-cpu → cuml → sklearn</code>.
    <code>faiss-cpu</code> or <code>sklearn</code> is a perfectly fine result — it just means
    no GPU pack. A broken CuPy is a warning, never a hard failure.
  </div></div>

  <div class="step"><div class="step-n">4</div><div class="step-b">
    <b>Run something real</b>, on the sample data that ships in the repo.
    <pre>.venv/Scripts/python.exe cli.py --examples      <span class="c"># embedded user guide</span>
.venv/Scripts/python.exe cli.py --manifest \\
  installer_assets/examples/sample_data/geocell.toml</pre>
  </div></div>

  <div class="step"><div class="step-n">5</div><div class="step-b">
    <b>Start the web app</b> — two terminals, or just <code>start_webapp.bat</code>.
    Backend <code>:8000</code> (Swagger at <code>/docs</code>), frontend <code>:5174</code>.
  </div></div>

  <h3>Before you open a PR</h3>
  <ul class="chk">
    <li><code>.venv/Scripts/python.exe -m pip install pytest</code> &nbsp;— it is <b>not</b> in
        <code>requirements.txt</code></li>
    <li><code>.venv/Scripts/python.exe -m pytest backend/tests -q</code></li>
    <li><code>.venv/Scripts/python.exe tools/sync_mirrors.py --check</code></li>
    <li>Branch → PR → merge. Conventional prefixes: <code>feat:</code> <code>fix:</code>
        <code>docs:</code> <code>perf:</code> <code>chore:</code></li>
  </ul>
  <div class="warn"><b>There is no CI.</b> <code>.github/</code> contains only
  <code>copilot-instructions.md</code> — no workflows. The mirror check and the test suite are
  described as "CI mode" throughout the docs, but nothing runs them automatically. Run both
  yourself.</div>
  <div class="ftag">07 · Day one</div><div class="pnum">8</div>
</div>""")

    # ── 9. state + gotchas ───────────────────────────────────────────────────
    p.append("""
<div class="page">
  <div class="kicker">08 · Where things stand</div>
  <h2>Current state &amp; landmines</h2>

  <h3>Merged — the manifest/geocell hardening line</h3>
  <table>
    <tr><th style="width:10%">PR</th><th>What it delivered</th><th style="width:18%">Merged</th></tr>
    <tr><td>#1</td><td>JPEG 2000 discovery alongside GeoTIFF in geocell layers</td><td>2026-07-09</td></tr>
    <tr><td>#2</td><td>Optional <code>[classify]</code> table + tiled geocell output</td><td>2026-07-12</td></tr>
    <tr><td>#3</td><td>Deterministic tile names + overwrite guard / clean</td><td>2026-07-13</td></tr>
  </table>

  <h3>Open — PR #4 <code>feat/silent-managed-installer</code></h3>
  <p style="font-size:9.4pt">Opened 2026-07-16, tip <code>f10889d</code>. 9 commits, ~3.7k added
  lines, 33 files. A clean fast-forward ahead of <code>main</code>. It bundles <b>two unrelated
  bodies of work</b>:</p>
  <div class="grid2">
    <div class="card"><h4>(a) JARVIS silent installer</h4>
      <p style="margin:0;font-size:9pt"><code>installer_silent/</code>, <code>publish.ps1</code>,
      <code>docs/AIRGAP_A4000_DEPLOY.md</code>. A headless installer built around a native MSVC
      stub — native because Windows can't launch a <i>managed</i> exe carrying a &gt;4 GB
      overlay. The ~170 MB artifact is deliberately not standalone; ~15 GB of shared data is
      provisioned once per box.</p></div>
    <div class="card"><h4>(b) Tier 0–2 mosaic overhaul</h4>
      <p style="margin:0;font-size:9pt">Windowed, parallel, area-average geocell join;
      overview-pinned reads; footprint cache; resumable tiled mosaic cache with VRT handoff.
      Plus <code>benchmarks/</code>. <b>This is the fix for Bug 4</b> — the only open issue
      that makes a run fail outright rather than produce awkward output.</p></div>
  </div>

  <h3>Known open bugs</h3>
  <table>
    <tr><th style="width:6%">#</th><th>Issue</th><th style="width:26%">Status</th></tr>
    <tr><td>3</td><td>Per-tile <code>.xml</code> material tables aren't unified across a cell —
        index <i>N</i> can mean different materials in different tiles</td>
        <td><b>Open.</b> Blocks the CDB rm-layer consumer. Nobody is on it.</td></tr>
    <tr><td>4</td><td>Mosaic built full-frame in RAM → OOM / thrash on constrained boxes, with
        no tiling fallback</td><td><b>Fix on PR #4</b>, not on <code>main</code>.</td></tr>
  </table>

  <h3>Things that are <i>supposed</i> to look wrong</h3>
  <ul style="font-size:9pt">
    <li><b><code>shared/mea_classes.json</code> lists 13 classes.</b> Leftover from before
    phase 4; <b>no code reads it</b>. Live schema = <code>MEA_CLASSES</code> in
    <code>core.py</code> + <code>shared/mea_defaults.json</code>. Don't wire it back in.</li>
    <li><b>Deleted wheels in <code>git status</code>.</b> The CUDA 12.9 wheels and a stray CPU
    torchvision were deliberately quarantined — 12.9 needs driver ≥ 575 and the target A4000
    box reports CUDA 12.4, so they'd silently drop the pipeline to CPU.</li>
    <li><b><code>graphify-out/</code> is referenced but absent</b> — lost in the May-2026 deletion
    incident, tool not installed. And <b><code>mea.ts</code> marks <code>BM_WATER</code> as
    <code>"kmeans"</code></b> where the backend says <code>"mask"</code> (cosmetic; that field only
    labels the calibration UI).</li>
  </ul>

  <div class="note" style="font-size:9pt"><b>Full detail:</b>
  <code>docs/PROJECT_STATUS.md</code> and <code>docs/MC_MANIFEST_BUGS_2026-07-12.md</code>.</div>
  <div class="ftag">08 · Where things stand</div><div class="pnum">9</div>
</div>""")

    # ── 10. map of the docs ──────────────────────────────────────────────────
    p.append("""
<div class="page dense">
  <div class="kicker">09 · Reference</div>
  <h2>Where to go next</h2>
  <p class="lead" style="margin-bottom:3mm">This deck is the overview. Everything below is the
  depth.</p>

  <div class="note" style="font-size:9pt;margin-top:0"><b>House style</b>, and it is enforced in
  review: think before coding and state your assumptions; simplicity first — minimum code, no
  speculative abstractions; surgical changes — touch only what you must, no drive-by refactors;
  aggressive dead-code removal over backwards-compat shims.</div>

  <table>
    <tr><th style="width:40%">You want to…</th><th>Read</th></tr>
    <tr><td>Follow the full onboarding path in prose</td><td><code>ONBOARDING.md</code></td></tr>
    <tr><td>Run something today</td><td><code>RUNNING_GUIDE.md</code></td></tr>
    <tr><td>Use the CLI properly</td><td><code>docs/CLI_GUIDE.md</code> · <code>cli.py --examples</code></td></tr>
    <tr><td>Understand the wiring</td><td><code>docs/ARCHITECTURE.md</code></td></tr>
    <tr><td>Call the HTTP API</td><td><code>docs/API_REFERENCE.md</code> · Swagger at <code>:8000/docs</code></td></tr>
    <tr><td>Know what's broken / in flight</td><td><code>docs/PROJECT_STATUS.md</code></td></tr>
    <tr><td>Tune the AI masks</td><td><code>docs/AI_FEATURE_EXTRACTION.md</code></td></tr>
    <tr><td>Get GPU working</td><td><code>docs/GPU_ACCELERATION.md</code></td></tr>
    <tr><td>Understand tile mode</td><td><code>docs/TILING.md</code></td></tr>
    <tr><td>Tune material colors</td><td><code>docs/MEA_CALIBRATION_TOOL.md</code></td></tr>
    <tr><td>Deploy to an offline station</td><td><code>STANDALONE_DEPLOYMENT.md</code></td></tr>
    <tr><td>Debug "0 pixels rasterized"</td><td><code>RASTERIZE_DEBUG.md</code> (Hebrew)</td></tr>
  </table>

  <h3>Glossary</h3>
  <div class="grid2" style="font-size:8.4pt">
    <div>
      <table style="font-size:8.4pt">
        <tr><th style="width:34%">Term</th><th>Meaning</th></tr>
        <tr><td><b>MEA</b></td><td>The material schema this targets. 6 classes, <code>BM_*</code>.</td></tr>
        <tr><td><b>Orthophoto</b></td><td>Top-down georeferenced aerial imagery, geometrically corrected.</td></tr>
        <tr><td><b>Geocell</b></td><td>OGC CDB unit: 1°-tall cell, widening toward the poles. <code>N33E035</code>.</td></tr>
        <tr><td><b>CDB</b></td><td>OGC Common DataBase — the terrain standard the output feeds.</td></tr>
        <tr><td><b>GSD</b></td><td>Ground Sample Distance — real-world size of one pixel.</td></tr>
        <tr><td><b>SAM3</b></td><td>Meta's Segment Anything 3. Supplies road / building masks.</td></tr>
      </table>
    </div>
    <div>
      <table style="font-size:8.4pt">
        <tr><th style="width:34%">Term</th><th>Meaning</th></tr>
        <tr><td><b>OWLv2</b></td><td>Open-vocab detector, paired with SAM2 as the SAM3 fallback.</td></tr>
        <tr><td><b>SDE</b></td><td>Esri enterprise geodatabase. Read via an <code>arcpy</code> subprocess.</td></tr>
        <tr><td><b>JARVIS</b></td><td>The external orchestrator that drives manifest mode.</td></tr>
        <tr><td><b>Manifest</b></td><td>The TOML describing one geocell run.</td></tr>
        <tr><td><b>Hungarian</b></td><td>Optimal 1:1 matching; maps clusters to materials.</td></tr>
        <tr><td><b>Mirror</b></td><td><code>offline_installer/app/</code> — byte-identical app copy.</td></tr>
      </table>
    </div>
  </div>

  <p style="margin-top:3.5mm;font-size:8.4pt;color:#9aa7b3;text-align:center">
    Regenerate this deck: <code>.venv/Scripts/python.exe tools/build_onboarding_pdf.py</code>
  </p>
  <div class="ftag">09 · Reference</div><div class="pnum">10</div>
</div>""")

    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Material Classification — Visual Onboarding</title>"
            f"<style>{CSS}</style></head><body>{''.join(p)}</body></html>")


def find_browser() -> str | None:
    for b in BROWSERS:
        if Path(b).exists():
            return b
    return None


def main() -> int:
    browser = find_browser()
    if not browser:
        sys.exit("No Chrome/Edge found for print-to-pdf. Install one and re-run.")

    html = build_html()
    with tempfile.TemporaryDirectory(prefix="onboarding_pdf_") as tmp:
        tmp_path = Path(tmp)
        html_file = tmp_path / "onboarding.html"
        html_file.write_text(html, encoding="utf-8")

        base = [
            browser, "--disable-gpu", "--no-pdf-header-footer",
            "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={tmp_path / 'ud'}",
            f"--print-to-pdf={PDF}", html_file.as_uri(),
        ]
        last = None
        for headless in ("--headless=new", "--headless"):
            cmd = [base[0], headless, *base[1:]]
            last = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if last.returncode == 0 and PDF.exists() and PDF.stat().st_size > 0:
                print(f"OK  wrote {PDF}  ({PDF.stat().st_size:,} bytes)  "
                      f"via {Path(browser).name}")
                return 0
        print(last.stdout if last else "")
        print(last.stderr if last else "")
        sys.exit(f"PDF render failed (rc={last.returncode if last else '?'}).")


if __name__ == "__main__":
    raise SystemExit(main())
