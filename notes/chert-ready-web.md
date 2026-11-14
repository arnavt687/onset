# chert-ready 5/5: caregiver join page and dashboard actions (`chert-ready-web`)

Branch `piece/chert-ready-web`, based on `piece/chert-ready-config` (#17). The agent side
(agent.py, onset/flow.py, onset/caregiver.py) is built separately against the contract below.

## What's here

- `web/caregiver/` — the page the caregiver opens from Onset's iMessage after ESCALATE/UNCLEAR.
  Static: `index.html`, `style.css`, `app.js`, `link.js` (the fragment parser, shared with the
  Node test). livekit-client **2.23.0** from cdn.jsdelivr.net, pinned with SRI.
- `.github/workflows/pages.yml` — publishes only `web/caregiver/` to GitHub Pages as `caregiver/`,
  on pushes to main that touch it, or by hand (workflow_dispatch).
- `tools/dashboard.py` — `GET /caregiver-link` (local demo helper, see below).
- `web/dashboard.html` — Actions log (DRY RUN / LIVE badge per action), caregiver presence on the
  decision card (invited → joined → talking), "Open caregiver view" button.

## The link

    <ONSET_CAREGIVER_PAGE>#u=<urlencoded wss URL>&w=<watch JWT>&t=<talk JWT, optional>&n=<first name, optional>&e=<expiry unix s>

- Tokens are in the fragment: browsers never send it to the static host. `app.js` reads it, then
  `history.replaceState` wipes it from the address bar before the CDN script even loads.
- It is kept in `sessionStorage` (this tab only) so an iOS tab reload doesn't strand the caregiver;
  removed once expired.
- `e` missing → the watch JWT's `exp` is used. `u` must be `wss://` (`ws://` only for localhost).
- CSP: scripts only from self + jsdelivr, `connect-src` only `*.livekit.cloud` (+ localhost dev).
  No fetch/XHR/beacon/analytics/fonts in the page (tests/test_caregiver_page.py checks this).

## Rooms

- Main room, watch token (subscribe only). Caller = identity `chert-bridge-*`; agent = kind AGENT
  (or `isAgent`, or identity `agent-*`). Large video = agent's `onset-video` (falls back to the
  caller's camera if there is no agent video); small picture-in-picture = caller's camera.
  Plays agent + caller audio only; the agent's audio element is muted while `onset.relay` == "on".
- Agent attributes: `onset.summary` (shown as the big caption), `onset.outcome` (banner wording),
  `onset.patient` (name if the link has no `n`), `onset.relay`. Data packet on topic `onset`,
  `{"type":"summary","text","outcome"}` is the fallback when attributes are missing.
- Side room `<main>-cg`, talk token (publish only): after "Talk to <name>", mic (echo cancellation +
  noise suppression) and front camera, with Mute and Leave. Talk is offered only while the agent is
  in the room and the caller hasn't left.

States: missing/bad link, expired (checked at load and every 5 s while idle), connecting, waiting
for Onset (and "isn't in the call right now" after 20 s), watching, talking, reconnecting, caller
left, Onset left, ended (room deleted, removed, opened on another screen, connection dropped → Try
again, except after room deleted/removed).

## Dashboard

- `action` events `{kind: imessage|invite|call, live, status, target (masked), detail}` → last 4 in
  an Actions list; `live` must be literally `true` to show LIVE, anything else shows DRY RUN.
  `target` "" shows "no caregiver phone"; with no `detail`, `link` ("page"|"meet") says which link went out.
- `caregiver_join` events `{status: invited|joined|talking|left|expired|failed, detail}` → a chain
  on the decision card. Both kinds keep updating after `run_ended` (the caregiver can join later).
- `GET /caregiver-link` returns `runs/live/caregiver_link.txt` (written by the agent, 0600) as
  text/plain with `Cache-Control: no-store`, only to loopback peers whose Host is
  localhost/127.0.0.1/[::1] (stops DNS rebinding); 403 otherwise, 404 if missing, a symlink, over
  16 KB, or not an http(s) URL. The page polls it only after an escalate/unclear decision, invite
  action or caregiver_join event and opens it with `noopener`.

## Verified

- `pytest -q tests` green; link parser cases under Node (skipped if `node` isn't on PATH).
- Browser pane (375×812): bad fragment, expired fragment (screenshots in
  `docs/demo/screenshots/caregiver/`).
- Live, LiveKit Cloud: a script minted watch/talk tokens for a fresh private
  `onset-pagetest-<rand>` (+ `-cg`), wrote the link to a temp runs dir, and the dashboard's "Open
  caregiver view" opened it. Connected → "Waiting for Onset"; a fake `agent-pagetest` (attributes +
  data packet + `onset-video` + audio) and `chert-bridge-pagetest` (`facetime-remote-video`) → video,
  PiP, summary, "Watching Ana's call"; relay on → agent audio muted; caller left → warning, Talk
  hidden; rooms deleted → "The call has ended". Both rooms deleted afterwards.
- Not verified: actually talking (the browser pane blocks mic/camera; the blocked-mic message was
  checked) and iOS Safari on a real phone.

## For the owner

Turn on Pages: Settings → Pages → Source: **GitHub Actions**. Then run the `pages` workflow (or
merge to main). The page is at https://arnavt687.github.io/onset/caregiver/ — set
`ONSET_CAREGIVER_PAGE` to that.
