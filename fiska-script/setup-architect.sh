#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ "${1:-}" == --help ]]; then
  printf 'Usage: ./setup-architect.sh [--mcp-name NAME ...]\nCreates or updates Architect on the running Claude runtime. No restart or model call.\n'
  exit 0
fi
require_docker
require_env
lock_operation
umask 077
python3 "$MULTICA_SETUP_DIR/configure-architect.py" "$@"
