#!/usr/bin/env bash
# Show which settings in .env.local are filled in, without printing any values.
cd "$(dirname "$0")/.." || exit 1
grep -vE '^\s*(#|$)' .env.local | while IFS='=' read -r k v; do
  v="${v%$'\r'}"
  case "$v" in
    ""|YOUR_*|wss://YOUR_*) s="placeholder or empty" ;;
    *) s="set (${#v} chars)" ;;
  esac
  [ "$k" = LIVEKIT_URL ] && [[ "$v" =~ ^wss://[^/]+\.livekit\.cloud/?$ ]] && s="$s, looks like wss://<project>.livekit.cloud"
  echo "$k: $s"
done
