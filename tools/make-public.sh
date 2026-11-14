#!/usr/bin/env bash
set -euo pipefail
gh api -X PATCH repos/arnavt687/onset -f visibility=public -q '.html_url + " " + .visibility'
