#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ "${1:-}" == --help ]]; then
  printf 'Usage: ./setup-factory-policy.sh [--dry-run]\nInstall evidence, size and complete-round review policy. Does not start tasks or restart agents.\n'
  exit 0
fi
require_docker
require_env
umask 077
python3 "$MULTICA_SETUP_DIR/configure-factory-policy.py" "$@"
if [[ "${1:-}" != --dry-run ]]; then
  compose exec -T -w /home/node runtime sh -c '
    set -eu
    mkdir -p /home/node/.local/bin
    target=$(mktemp /home/node/.local/bin/.factory-mysql.XXXXXX)
    trap '\''rm -f "$target"'\'' EXIT
    cat > "$target"
    chmod 755 "$target"
    mv "$target" /home/node/.local/bin/factory-mysql
  ' < "$MULTICA_SETUP_DIR/factory-mysql.py"
fi
