#!/usr/bin/env bash
# Run the Chert CLI from the Onset folder with WSL's Node: tools/chert.sh <args>
cd "$(dirname "$0")/.."
source ~/.nvm/nvm.sh >/dev/null
exec npx --yes @trychert/facetime@0.1.0-beta.20 "$@"
