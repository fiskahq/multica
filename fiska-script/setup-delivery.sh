#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ "${1:-}" == --help ]]; then
  printf 'Usage: ./setup-delivery.sh\nEnable PR creation and feature/<Jira-or-Multica-key>-<description> naming for the existing development team. No restart.\n'
  exit 0
fi
if [[ $# -ne 0 ]]; then
  printf 'Unexpected arguments. Use --help for usage.\n' >&2
  exit 1
fi
require_docker
require_env
umask 077
# Each configurator takes the operation lock and verifies its native updates.
python3 "$MULTICA_SETUP_DIR/configure-delivery-pr.py"
python3 "$MULTICA_SETUP_DIR/configure-delivery-branches.py"
