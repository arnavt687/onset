#!/usr/bin/env bash
# Text the caregiver's join link from this Mac's own Messages app (macOS only).
#
# A fallback for when Chert's caregiver text is refused (recipient_not_verified, a closed reply
# window on the free trial, or a line whose inbound iMessage is not working). The Mac sends an
# ordinary iMessage from whatever Apple ID is signed in to Messages, so Chert's joining rules do
# not apply. It sends two messages: the run's caregiver summary, then the link ON ITS OWN, so
# Messages shows it as a tappable link (inside a sentence, a link this long arrived as plain text
# that had to be copied). The link is also copied to the clipboard, as a second fallback.
#
#   tools/text_link_mac.sh --dry-run      print what would be sent for the newest escalation
#   tools/text_link_mac.sh --send-now     send the newest link once, now
#   tools/text_link_mac.sh                watch: send each new link as it is written (Ctrl+C stops)
#
# Options: --to +1...  (default ONSET_CAREGIVER_PHONE from .env.local)
#
# Sending needs ONSET_LIVE_ACTIONS=1 in .env.local, the same safety gate as every other real
# message. The first run asks macOS for permission to control Messages.
# The link holds join tokens: this script never prints it, except in --dry-run, shortened.
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT=$PWD
LINK=$ROOT/runs/live/caregiver_link.txt
PY=${PY:-$ROOT/.venv/bin/python}; [ -x "$PY" ] || PY=$HOME/onset/.venv/bin/python
ENV=$ROOT/.env.local

die() { echo "[text-link] $*" >&2; exit 1; }
say() { echo "[text-link] $(date +%H:%M:%S) $*"; }
envval() { [ -f "$ENV" ] && sed -n "s/^$1=//p" "$ENV" | tail -1 | tr -d '\r"'"'"; }

[ "$(uname -s)" = Darwin ] || die "macOS only: it drives the Messages app"

MODE=watch
TO=$(envval ONSET_CAREGIVER_PHONE)
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) MODE=dry ;;
    --send-now) MODE=now ;;
    --to) TO=${2:-}; shift ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done
[ -n "$TO" ] || die "no recipient: set ONSET_CAREGIVER_PHONE in .env.local or pass --to"

compose() {  # THIS run's caregiver summary, then the link
  # Only runs/live, the run the link belongs to: never a summary from an older run (that would
  # tell the caregiver about the wrong check-in). The agent writes the summary just after the
  # link, once Chert has answered its own attempt, so wait a few seconds for it; without it the
  # link goes alone.
  "$PY" -I - "$ROOT/runs/live/events.jsonl" "$LINK" <<'PY'
import json, sys, time
events, link_path = sys.argv[1], sys.argv[2]
link = open(link_path).read().strip()
summary, end = "", time.monotonic() + 8.0
while not summary and time.monotonic() < end:
    try:
        for line in open(events, encoding="utf-8"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "caregiver" and e.get("text"):
                summary = e["text"]
    except OSError:
        pass
    if not summary:
        time.sleep(0.5)
print((summary + "\n\n" if summary else "") + "Join the call now to see and talk to them: tap the link in the next message.")
PY
}

send() {  # send $1 to $TO from the first enabled iMessage account
  osascript - "$TO" "$1" <<'OSA'
on run argv
  set num to item 1 of argv
  set msg to item 2 of argv
  tell application "Messages"
    set svc to missing value
    repeat with a in accounts
      try
        if (service type of a as text) is "iMessage" and enabled of a then
          set svc to a
          exit repeat
        end if
      end try
    end repeat
    if svc is missing value then error "no enabled iMessage account in Messages"
    send msg to participant num of svc
  end tell
end run
OSA
}

live_ok() { [ "$(envval ONSET_LIVE_ACTIONS)" = 1 ] || die "ONSET_LIVE_ACTIONS is not 1 in .env.local: refusing to send a real message"; }

send_current() {
  [ -s "$LINK" ] || { say "no caregiver link yet ($LINK)"; return 1; }
  pbcopy < "$LINK" 2>/dev/null
  # The link alone in its own message: Messages makes a message that is just a URL a tappable link.
  if send "$(compose)" >/dev/null 2>&1 && sleep 0.6 && send "$(tr -d '[:space:]' < "$LINK")" >/dev/null 2>&1; then
    say "sent to ${TO:0:2}…${TO: -2} (the summary, then the link on its own) and copied the link to the clipboard"
  else
    say "SEND FAILED; the link is on the clipboard: paste it to the caregiver"
    return 1
  fi
}

case $MODE in
  dry)
    [ -s "$LINK" ] || die "no caregiver link yet: it appears after an escalation"
    echo "to: $TO   (from this Mac's iMessage account)"
    echo "-----"
    compose
    echo "----- (second message, the link alone)"
    tr -d '[:space:]' < "$LINK" | sed -E 's/(#.{0,30}).*/\1…/'; echo
    ;;
  now)
    live_ok; send_current
    ;;
  watch)
    live_ok
    say "watching for new caregiver links; sending to ${TO:0:2}…${TO: -2}. Ctrl+C stops."
    prev=""; [ -f "$LINK" ] && prev=$(stat -f %m "$LINK" 2>/dev/null)
    while :; do
      if [ -f "$LINK" ]; then
        cur=$(stat -f %m "$LINK" 2>/dev/null)
        if [ -n "$cur" ] && [ "$cur" != "$prev" ]; then
          prev=$cur
          send_current
        fi
      fi
      sleep 1
    done
    ;;
esac
