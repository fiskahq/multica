#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $# == 0 ]] || fail 'Usage: ./setup-relay.sh'
umask 077
lock_operation
require_docker
require_env
[[ -f "$MULTICA_SETUP_DIR/mcp-relay/config.json" && -d "$MULTICA_SETUP_DIR/mcp-relay/.secrets" && -d "$MULTICA_SETUP_DIR/mcp-relay/data" ]] ||
  fail 'Prepare mcp-relay/config.json, .secrets/ and data/ first; see README.md.'
set_build_metadata
compose --profile relay -f "$MULTICA_SETUP_DIR/compose.build.yml" build mcp-relay
compose --profile relay up -d --no-deps --force-recreate --no-build --pull never --wait --wait-timeout 120 mcp-relay
printf '{"enabled":true}\n' > "$MULTICA_SETUP_DIR/relay-state.json"
printf 'Managed MCP relay is ready at http://mcp-relay:8080 inside Docker.\n'
