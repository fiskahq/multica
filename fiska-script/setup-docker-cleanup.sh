#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ "${1:-}" == --help ]]; then
  printf 'Usage: ./setup-docker-cleanup.sh [--remove]\nInstalls daily cleanup using a Linux user timer or macOS LaunchAgent. Uses the current Docker context.\n'
  exit 0
fi
[[ $# == 0 || ( $# == 1 && "$1" == --remove ) ]] || fail 'Usage: ./setup-docker-cleanup.sh [--remove]'
command -v python3 >/dev/null || fail 'Python 3 is required.'
python3 "$MULTICA_SETUP_DIR/schedule-docker-cleanup.py" "$@"
