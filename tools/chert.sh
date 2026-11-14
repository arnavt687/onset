#!/usr/bin/env bash
# Run the Chert CLI from the Onset folder with WSL's (or the Mac's) Node: tools/chert.sh <args>
# CHERT_CLI_VERSION (environment or .env.local) pins another CLI version.
#
# `tools/chert.sh dev` adds what the CLI needs to find Onset (issue #28), unless you pass them:
#   --agent NAME       AGENT_NAME from the environment, else .env.local, else "onset" (as agent.py)
#   --env-file FILE    .env.local, for the LiveKit keys (the CLI does not read it on its own)
# Without them the CLI waits for its starter's "my-agent" and declines every call (agent_not_warm).
# CHERT_DRY_RUN=1 prints the command instead of running it.
cd "$(dirname "$0")/.." || exit 1

# The value of NAME in .env.local, the way python-dotenv reads it: last one wins, optional
# `export `, quotes stripped, a trailing ` # comment` dropped from an unquoted value.
env_local() {
  [ -f .env.local ] || return 0
  sed -n "s/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}$1[[:space:]]*=[[:space:]]*//p" .env.local | tail -1 |
    tr -d '\r' | sed -e 's/^"\(.*\)"[[:space:]]*$/\1/' -e "s/^'\(.*\)'[[:space:]]*\$/\1/" -e 's/[[:space:]]\{1,\}#.*$//' -e 's/[[:space:]]*$//'
}

if [ -z "${CHERT_CLI_VERSION:-}" ]; then
  CHERT_CLI_VERSION=$(env_local CHERT_CLI_VERSION)
fi

args=("$@")
if [ "${1:-}" = dev ]; then
  has() { local a; for a in "${args[@]}"; do case "$a" in "$1"|"$1"=*) return 0 ;; esac; done; return 1; }
  if ! has --agent; then
    name=${AGENT_NAME:-$(env_local AGENT_NAME)}
    args+=(--agent "${name:-onset}")
  fi
  if ! has --env-file && [ -f .env.local ]; then
    args+=(--env-file .env.local)
  fi
fi

if [ "${CHERT_DRY_RUN:-0}" = 1 ]; then
  printf '%s\n' "npx --yes @trychert/facetime@${CHERT_CLI_VERSION:-0.1.0-beta.20} ${args[*]}"
  exit 0
fi
[ -s ~/.nvm/nvm.sh ] && source ~/.nvm/nvm.sh >/dev/null
exec npx --yes "@trychert/facetime@${CHERT_CLI_VERSION:-0.1.0-beta.20}" "${args[@]}"
