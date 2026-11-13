#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ "${1:-}" == --help ]]; then
  printf 'Usage: ./setup-advisor.sh\nInstalls the Kiro Opus 5.5 planning advisor and native consultation hooks. No restart or model call.\n'
  exit 0
fi
if [[ $# -ne 0 ]]; then
  printf 'Unexpected arguments. Use --help for usage.\n' >&2
  exit 1
fi
require_docker
require_env
lock_operation
umask 077
python3 "$MULTICA_SETUP_DIR/configure-advisor.py"
