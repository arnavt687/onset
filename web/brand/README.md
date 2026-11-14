# Onset brand

| File | Use |
| --- | --- |
| `logo-mark.svg`, `logo-512.png` | Square app icon (dark tile). Also the favicon (inlined as a data URI in `web/dashboard.html` and `web/pitch/index.html`). |
| `logo-glyph.svg` | The mark alone on a transparent ground (inlined in the deck, dashboard and video cards). |
| `logo.svg`, `logo-wordmark.png` | Horizontal wordmark for dark backgrounds (transparent PNG, 1282x360). |
| `logo-light.svg` | Horizontal wordmark for light backgrounds (README on a light GitHub theme). |

The mark is an open ring (the daily one-minute check-in, the 90-day cycle) with a coral point in
the gap: the moment a change begins. Colours: teal `#67E0D2` to sky `#3FB6E8`, coral `#FF6B57`,
ink `#0A1322`. The wordmark is Inter Bold, outlined to paths (no font needed).

Rebuild:

```bash
~/onset/.venv/bin/python web/brand/build_logo.py          # SVGs (inside Ubuntu)
```
```powershell
# PNGs (Windows, Chrome via playwright; NODE_PATH as in tools/video/make.ps1)
node tools\video\shoot.cjs web\brand\logo-512.png file://.../web/brand/logo-mark.svg --w 512 --h 512 --transparent --wait 300
node tools\video\shoot.cjs web\brand\logo-wordmark.png file://.../web/brand/logo.svg --w 1282 --h 360 --transparent --wait 300
```
