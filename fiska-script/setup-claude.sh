#!/usr/bin/env bash
# Transfer authentication through stdin without exposing tokens.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

mode="${1:-}"
[[ $# -le 1 && ( -z "$mode" || "$mode" == --token ) ]] || fail 'Usage: ./setup-claude.sh [--token]'
umask 077
lock_operation
require_docker
require_env
command -v python3 >/dev/null || fail 'Python 3 is required.'
compose exec -T runtime claude --version >/dev/null || fail 'Start the runtime first.'
helper="$MULTICA_SETUP_DIR/claude-auth.py"
# Only helper source is in argv; credentials are passed over stdin.
remote_helper="$(cat "$helper")"
if [[ "$mode" == --token ]]; then
  printf 'Generate a token with claude setup-token in another terminal first. Do not paste it in chat.\n'
  python3 "$helper" prompt-token | compose exec -T runtime python3 -c "$remote_helper" install-token
else
  command -v claude >/dev/null || fail 'Install Claude Code and run claude auth login on the host first.'
  claude auth status >/dev/null || fail 'Run claude auth login on the host first.'
  python3 "$helper" export-login | compose exec -T runtime python3 -c "$remote_helper" install-login
fi
compose exec -T runtime claude auth status
printf '\nClaude authentication installed. Retry the failed task; no runtime restart is required.\n'
printf 'Auth status checks local configuration, not a model request.\n'
