#!/usr/bin/env bash
set -euo pipefail
setup_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$setup_dir/schedule-qa-watchdog.py" "$@"
