"""Render docs/CLI_GUIDE.md to docs/CLI_GUIDE.pdf.

Markdown -> styled HTML (markdown-it-py, GFM tables) -> PDF via headless
Chrome/Edge ``--print-to-pdf``. No Python PDF library required; uses whichever
browser is installed (Chrome or Edge — both ship the same print-to-pdf path).

Run with the project venv's Python:
    .venv/Scripts/python.exe tools/build_cli_guide_pdf.py
On the dev tree (no venv) use the runnable install's interpreter, e.g.
    C:/ClassificationApp/.venv/Scripts/python.exe tools/build_cli_guide_pdf.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "docs" / "CLI_GUIDE.md"
PDF = ROOT / "docs" / "CLI_GUIDE.pdf"

# Candidate browsers, in preference order. Any Chromium-based one works.
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]

CSS = """
@page { size: A4; margin: 16mm 14mm; }
* { box-sizing: border-box; }
body {
  font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.45; color: #1b1f24; margin: 0;
}
h1 { font-size: 22pt; color: #0b3d62; border-bottom: 2px solid #0b3d62;
     padding-bottom: 6px; margin: 0 0 14px; }
h2 { font-size: 15pt; color: #0b3d62; margin: 22px 0 8px;
     border-bottom: 1px solid #cdd6df; padding-bottom: 3px; page-break-after: avoid; }
h3 { font-size: 12pt; color: #134a73; margin: 16px 0 6px; page-break-after: avoid; }
h4 { font-size: 10.8pt; color: #134a73; margin: 12px 0 4px; page-break-after: avoid; }
p { margin: 6px 0; }
ul, ol { margin: 6px 0 6px 20px; }
li { margin: 2px 0; }
a { color: #0b66c3; text-decoration: none; }
code {
  font-family: "Cascadia Code", "Consolas", monospace; font-size: 9.2pt;
  background: #f1f3f5; padding: 1px 4px; border-radius: 3px; color: #b1004e;
}
pre {
  background: #0f1722; color: #e6edf3; padding: 10px 12px; border-radius: 6px;
  overflow-x: auto; page-break-inside: avoid; font-size: 9pt; line-height: 1.4;
}
pre code { background: transparent; color: inherit; padding: 0; }
table {
  border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9.2pt;
  page-break-inside: auto;
}
th, td { border: 1px solid #c4ced8; padding: 5px 8px; text-align: left;
         vertical-align: top; }
th { background: #e8eef4; color: #0b3d62; font-weight: 600; }
tr:nth-child(even) td { background: #f7f9fb; }
tr { page-break-inside: avoid; }
blockquote {
  margin: 8px 0; padding: 6px 12px; border-left: 4px solid #4a90c2;
  background: #eef5fb; color: #2a3742;
}
blockquote p { margin: 3px 0; }
hr { border: none; border-top: 1px solid #d4dbe2; margin: 18px 0; }
em { color: #444; }
"""


def find_browser() -> str | None:
    for b in BROWSERS:
        if Path(b).exists():
            return b
    return None


def main() -> int:
    if not MD.exists():
        sys.exit(f"Source markdown not found: {MD}")
    try:
        from markdown_it import MarkdownIt
    except Exception as exc:  # pragma: no cover
        sys.exit(f"markdown-it-py not available ({exc}). Use the project venv.")

    # gfm-like gives us tables; linkify needs an extra module we don't ship.
    md = MarkdownIt("gfm-like", {"linkify": False})
    body = md.render(MD.read_text(encoding="utf-8"))
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )

    browser = find_browser()
    if not browser:
        sys.exit("No Chrome/Edge found for print-to-pdf. Install one or render "
                 "docs/CLI_GUIDE.md another way.")

    with tempfile.TemporaryDirectory(prefix="cliguide_pdf_") as tmp:
        tmp_path = Path(tmp)
        html_file = tmp_path / "guide.html"
        html_file.write_text(html, encoding="utf-8")
        user_data = tmp_path / "ud"

        base = [
            browser, "--disable-gpu", "--no-pdf-header-footer",
            "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={user_data}",
            f"--print-to-pdf={PDF}", html_file.as_uri(),
        ]
        # Try the modern headless flag first, fall back to the legacy one.
        last = None
        for headless in ("--headless=new", "--headless"):
            cmd = [base[0], headless, *base[1:]]
            last = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if last.returncode == 0 and PDF.exists() and PDF.stat().st_size > 0:
                print(f"OK  wrote {PDF}  ({PDF.stat().st_size:,} bytes)  via {Path(browser).name}")
                return 0
        print(last.stdout if last else "")
        print(last.stderr if last else "")
        sys.exit(f"PDF render failed (rc={last.returncode if last else '?'}).")


if __name__ == "__main__":
    raise SystemExit(main())
