#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $# == 0 ]] || fail 'Usage: ./setup-confluence-writes.sh'
require_docker
require_env
lock_operation
umask 077
python3 "$MULTICA_SETUP_DIR/configure-confluence-writes.py" prepare
compose --profile confluence-write up -d --no-deps --no-build --pull never --wait --wait-timeout 120 confluence-write
python3 "$MULTICA_SETUP_DIR/configure-confluence-writes.py" register
printf '{"enabled":true}\n' > "$MULTICA_SETUP_DIR/confluence-write-state.json"
printf 'Dedicated Confluence write relay is ready; existing containers were not restarted.\n'
