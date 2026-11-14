#!/usr/bin/env bash
# What a branch adds over main: commits and changed files. Usage: tools/branch-summary.sh <branch>
cd ~/onset && git fetch -q origin
b="origin/$1"
git log --oneline main.."$b" | head -30
echo "--- files changed vs main"
git diff --stat main..."$b" | tail -40
