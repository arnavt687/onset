#!/usr/bin/env bash
# Show recent CI runs on a branch (default main) and the failing log of the newest failed one.
cd "$(dirname "$0")/.."
gh run list --branch "${1:-main}" --limit 4 --json databaseId,headSha,status,conclusion,displayTitle \
  -q '.[] | "\(.databaseId) \(.headSha[0:7]) \(.status) \(.conclusion // "-") \(.displayTitle)"'
id=$(gh run list --branch "${1:-main}" --limit 10 --json databaseId,conclusion -q '[.[] | select(.conclusion=="failure")][0].databaseId')
[ -n "$id" ] || { echo "no failed run"; exit 0; }
echo "=== failed run $id"
gh run view "$id" --log-failed 2>/dev/null | sed -E 's/^[^\t]*\t[^\t]*\t//' | grep -vE '^\s*$' | grep -iE -B3 -A15 'error|failed|Traceback|assert' | head -90
