#!/usr/bin/env bash
# Internal bridge: keep secret JSON on stdin, never in command arguments.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
case "${1:-}" in
  health) compose exec -T runtime curl --fail --silent http://127.0.0.1:19514/health ;;
  kiro-api-mode)
    compose run --rm -T --no-deps runtime kiro-cli logout >/dev/null
    printf '{}\n'
    ;;
  auth-status)
    case "${2:-}" in
      claude_login) compose run --rm -T --no-deps runtime claude auth status >/dev/null 2>&1 ;;
      codex_login) compose run --rm -T --no-deps runtime codex login status >/dev/null 2>&1 ;;
      *) fail 'Unknown provider login type' ;;
    esac
    printf '{}\n'
    ;;
  interactive)
    shift
    compose run --rm --no-deps runtime "$@"
    ;;
  *) compose run --rm -T --no-deps runtime multica "$@" ;;
esac
