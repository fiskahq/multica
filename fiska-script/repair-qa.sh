#!/usr/bin/env bash
set -euo pipefail
setup_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ $# == 0 ]] || { printf 'Usage: ./repair-qa.sh\n' >&2; exit 1; }
exec python3 "$setup_dir/repair-qa.py"
