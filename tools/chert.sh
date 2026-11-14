#!/usr/bin/env bash
# Run the Chert CLI from the Onset folder with WSL's Node: tools/chert.sh <args>
# CHERT_CLI_VERSION (environment or .env.local) pins another CLI version.
cd "$(dirname "$0")/.."
if [ -z "${CHERT_CLI_VERSION:-}" ] && [ -f .env.local ]; then
  CHERT_CLI_VERSION=$(sed -n 's/^CHERT_CLI_VERSION=//p' .env.local | tail -1 | tr -d '\r"'"'"' ')
fi
[ -s ~/.nvm/nvm.sh ] && source ~/.nvm/nvm.sh >/dev/null
exec npx --yes "@trychert/facetime@${CHERT_CLI_VERSION:-0.1.0-beta.20}" "$@"
