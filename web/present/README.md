# Onset presentation

A short presentation built around the live demo: seven scenes, the call in the middle.
It reuses the 3D designs from the pitch deck (`web/pitch`): the face and the figure made of
landmark points, the same palette and fonts. `web/pitch` is the longer 14-slide deck.

```bash
tools/demo.sh --present    # demo day: agent + Chert line + dashboard, and opens the presentation
tools/present.sh           # rehearsal: only the local server, no agent, no line
```

Both serve it at http://localhost:8765/present. Open it in Chrome and press `F` for full screen.

## Scenes

| # | Scene | What is interactive |
|---|---|---|
| 1 | Onset | The face follows the mouse; a scan line follows it up and down |
| 2 | The problem (2 steps) | The stat cards tilt; step 2 lights 90 days one by one |
| 3 | The idea (3 steps) | Smile, arms, speech: drag the slider (or `A`) to act a change and watch it cross the limit |
| 4 | **Live demo** (2 steps) | "Let's call it", then the live dashboard inside the presentation |
| 5 | When it changes | The escalation plays itself; click a step to jump to it, `R` replays |
| 6 | Under the hood | The pipeline and 90 days of trend lines, one flagged for a slow decline |
| 7 | Try it | QR code to the browser demo |

## The live demo scene

Served by the dashboard (`tools/dashboard.py`), the presentation is on the same origin as the
live call:

- The live view opens **by itself** when a call starts (a `run_started` event), and the status
  line follows the call: the caption Onset is showing, then the outcome.
- `L` shows the live dashboard, `B` the browser demo (`web/try`, webcam, nothing sent anywhere):
  the fallback if the line fails.
- The dashboard's "Click to enable sound" step is done for you once you have clicked or pressed
  a key in the presentation. If it still shows, click it once.
- `PageDown`, `Esc` or **Continue →** leaves the live view (arrow keys go to the dashboard while
  it has the focus).
- The number to call is shown when `ONSET_LINE_NUMBER` is set (`tools/demo.sh` sets it) or with
  `?line=+1...`; click it to blur it.

Opened from a plain static server or GitHub Pages there is no dashboard, so the scene shows the
browser demo.

## Voice

Press `V` (or click the microphone, top right) and the deck listens: say **"next slide"** and it
moves. It stays on for next time; `?voice` in the address turns it on too.

| Say | It does |
|---|---|
| "next slide", "move on", "next please" | next step or scene |
| "go back", "previous slide" | back |
| "start the demo", "let's call it" | jumps to the live demo |
| "open the live view", "open the browser demo" | the live dashboard, or the fallback |
| "show the smile", "show the arms", "show the speech" | that check, from anywhere |
| "show a change", "back to normal" | acts a change on the current check, or undoes it |
| "play it again" | replays the escalation |
| "first slide", "last slide", "voice off" | as they say |

Commands are whole phrases, so ordinary talking (a bare "next", "back" or "demo") does not move
the deck. It uses the browser's speech recognition, so it needs **Chrome or Edge, the microphone
and an internet connection**: the browser sends the microphone audio to its maker's speech
service while voice is on. Without those, the keys and the clicker still work. During the call the
microphone hears the room, so keep to the phrases above, or press `V` to switch it off.

## Keys

| Key | Does |
|---|---|
| `→` `Space` `PgDn` (clicker) | next step or scene |
| `←` `PgUp` | back |
| `D` | jump to the live demo |
| `F` | full screen |
| `N` | speaker notes and a timer (`T` resets it) |
| `M` | motion on / off (also follows the system's reduce-motion setting) |
| `V` | voice commands on / off |
| `?` | key help |
| The idea: `1` `2` `3`, `A` | pick a check, act a change |
| Live demo: `L`, `B` | live call, browser demo |
| When it changes: `R` | replay |

`#4` in the address opens scene 4, `#3.2` scene 3 step 2. `?still` forces the reduced-motion version.

## Files

`index.html` (the words), `style.css`, `deck.js` (scenes, keys, the checks, the live demo),
`world.js` (the three.js scene), `qr-try.svg`. three.js 0.186.1 and the fonts come from
`../pitch/vendor`, with jsDelivr as the fallback for three.js. Without WebGL the slides still work.
