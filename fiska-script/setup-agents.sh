#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $# == 1 ]] || fail 'Usage: ./setup-agents.sh agents.json'
command -v python3 >/dev/null || fail 'Install Python 3 first.'
require_docker
require_env
lock_operation
umask 077
python3 "$MULTICA_SETUP_DIR/provision-agents.py" --validate "$1"
if ! compose run --rm -T --no-deps runtime multica workspace list --output json >/dev/null 2>&1; then
  [[ -t 0 ]] || fail 'Authenticate first with ./sandbox.sh login, then rerun setup-agents.sh.'
  printf 'Create a workspace and personal access token in the browser. Enter the token below.\n'
  compose run --rm --no-deps runtime multica login --token=
fi
start_runtime
python3 "$MULTICA_SETUP_DIR/provision-agents.py" "$1"
python3 "$MULTICA_SETUP_DIR/configure-kiro-pool.py" "$1"
