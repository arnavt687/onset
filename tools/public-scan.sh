#!/usr/bin/env bash
# Before making the repo public: search the current tree AND all history for personal or secret data.
cd ~/onset
git fetch -q origin
pat='(\+?1?[ -]?\(?[0-9]{3}\)?[ -.]?[0-9]{3}[ -.]?[0-9]{4})|([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})|(AQ\.[A-Za-z0-9_-]{20,})|(AIza[0-9A-Za-z_-]{20,})|(sk_[A-Za-z0-9]{20,})|(sk-[A-Za-z0-9]{20,})|(ftk_[A-Za-z0-9]{8,})|(ftu_[A-Za-z0-9]{8,})|(gh[pousr]_[A-Za-z0-9]{20,})|(J9TBJP)|(join [A-Z0-9]{6})|(API[A-Za-z0-9]{10,})'
echo "=== current tree (origin/main): matches by file"
git grep -nIE "$pat" origin/main -- . ':!requirements.txt' | sed -E 's/^origin\/main://' | cut -c1-200 | head -60
echo
echo "=== distinct matches across ALL history (added lines)"
git log --all -p --no-color | grep -E '^\+' | grep -oE "$pat" | sort | uniq -c | sort -rn | head -40
