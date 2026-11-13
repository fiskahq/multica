#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
require_env
lock_operation
python3 "$MULTICA_SETUP_DIR/configure-kiro-pool.py" "$@"
