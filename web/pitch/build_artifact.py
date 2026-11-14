"""Make the claude.ai Artifact version of the deck from index.html.

    python3 web/pitch/build_artifact.py OUT.html

The Artifact host wraps the page in its own <html>/<head>/<body>, only allows scripts from
pinned CDNs and stylesheets from Google Fonts, and refuses the camera. So this:
  * drops the doctype/html/head/body wrappers,
  * swaps the vendored @font-face block for the Google Fonts stylesheet,
  * sets ARTIFACT = true (the deck then loads three.js from jsDelivr, hides the camera run and
    the replay slot, and keeps the simulated caller).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(__file__).with_name("index.html")
FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..800'
    '&family=Inter:wght@400..700&family=JetBrains+Mono:wght@400..600&display=swap">'
)


def build(html: str) -> str:
    html = re.sub(r"<!--LOCAL-FONTS-->.*?<!--/LOCAL-FONTS-->", FONTS, html, flags=re.S)
    html = re.sub(r"<!doctype html>\s*", "", html, flags=re.I)
    for tag in ("html", "head", "body"):
        html = re.sub(rf"</?{tag}\b[^>]*>\s*", "", html)
    html = re.sub(r'<meta charset="utf-8">\s*', "", html)
    html = re.sub(r'<meta name="viewport"[^>]*>\s*', "", html)
    html = html.replace("const ARTIFACT = false;", "const ARTIFACT = true;")
    assert "const ARTIFACT = true;" in html
    # the title first, so the host finds it in the first 8 KB
    m = re.search(r"<title>.*?</title>\s*", html)
    html = m.group(0) + html[: m.start()] + html[m.end():]
    return html


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.write_text(build(SRC.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
