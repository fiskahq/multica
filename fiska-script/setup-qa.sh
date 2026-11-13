#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $# == 0 ]] || fail 'Usage: ./setup-qa.sh'
require_docker
require_env
lock_operation
set_build_metadata
umask 077
[[ -n "$(compose ps --status running -q runtime)" ]] || fail 'Start the runtime first.'

# The daemon resolves bind paths, so it needs the same workspace alias as its client.
workspace_alias=$(compose exec -T runtime printenv MULTICA_WORKSPACES_ROOT)
python3 - "$MULTICA_SETUP_DIR/.env" "$workspace_alias" <<'PY'
from pathlib import Path
import shlex
import sys
p = Path(sys.argv[1])
value = sys.argv[2].strip()
if not value.startswith('/') or '\n' in value:
    raise SystemExit('Invalid runtime workspace alias')
if value == '/workspaces':
    value = '/workspaces-legacy'
lines = [line for line in p.read_text().splitlines() if not line.startswith('QA_WORKSPACES_ALIAS=')]
lines.append('QA_WORKSPACES_ALIAS=' + shlex.quote(value))
p.write_text('\n'.join(lines) + '\n')
PY
compose --profile runtime --profile qa -f "$MULTICA_SETUP_DIR/compose.build.yml" build --pull qa-docker
compose --profile runtime --profile qa up -d --no-deps --no-build --pull never --wait --wait-timeout 180 qa-docker
docker_version=$(compose --profile runtime --profile qa -f "$MULTICA_SETUP_DIR/compose.build.yml" config --format json |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["qa-docker"]["build"]["args"]["DOCKER_VERSION"])')
python3 "$MULTICA_SETUP_DIR/qa-client.py" --install-client "docker:${docker_version}-cli"
printf '{"enabled":true}\n' > "$MULTICA_SETUP_DIR/qa-state.json"
"$MULTICA_SETUP_DIR/setup-qa-watchdog.sh"
printf 'QA Docker is enabled. The agent runtime was not restarted.\n'
