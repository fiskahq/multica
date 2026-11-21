#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ "${1:-}" == --help ]]; then
  printf 'Usage: ./setup-qa-policy.sh [--dry-run]\nInstall complete QA reporting and planner handoff for the existing development team. No restart.\n'
  exit 0
fi
require_docker
require_env
umask 077
python3 "$MULTICA_SETUP_DIR/configure-qa-policy.py" "$@"
