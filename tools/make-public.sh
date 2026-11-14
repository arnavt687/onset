#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
gh api -X PATCH "repos/$(gh repo view --json nameWithOwner -q .nameWithOwner)" -f visibility=public -q '.html_url + " " + .visibility'
