#!/usr/bin/env bash
# One-screen overview of open PRs: files, size, checks, mergeability, and a safety/secret scan of each diff.
cd "$(dirname "$0")/.."
for n in "$@"; do
  echo "=== PR #$n"
  gh pr view "$n" --json title,headRefName,isDraft,mergeable,additions,deletions,statusCheckRollup \
    -q '"\(.title) [\(.headRefName)] draft=\(.isDraft) mergeable=\(.mergeable) +\(.additions)/-\(.deletions) checks=\([.statusCheckRollup[]? | "\(.name):\(.conclusion // .status)"] | join(","))"'
  gh pr diff "$n" --name-only | sed 's/^/   /'
  diff=$(gh pr diff "$n")
  echo "$diff" | grep -nE '^\+.*(AQ\.[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}|sk_[A-Za-z0-9]{20,}|ftk_[A-Za-z0-9]{10,}|xi-api-key"?\s*[:=]\s*"[A-Za-z0-9])' && echo "   !! possible secret"
  echo "$diff" | grep -niE "^\+[^#]*['\"][^'\"]*(you'?re fine|you are fine|all clear|you are healthy|no stroke)[^'\"]*['\"]" | grep -viE 'assert|not in|forbid|banned|never|must not' | head -3 | sed 's/^/   !! safety: /'
done
