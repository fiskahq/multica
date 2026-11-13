#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

require_docker
require_env
action=${1:-status}
[[ $# == 0 ]] || shift
case "$action" in
  start) [[ $# == 0 ]] || fail 'Usage: ./sandbox.sh start'; lock_operation; start_app --pull never --no-build ;;
  status) compose --profile runtime --profile qa ps "$@" ;;
  shell)
    if [[ -n "$(compose ps --status running -q runtime)" ]]; then
      compose exec runtime bash "$@"
    else
      compose run --rm --no-deps runtime bash "$@"
    fi
    ;;
  cli) compose run --rm -T --no-deps runtime multica "$@" ;;
  login) compose run --rm --no-deps runtime multica login --token= "$@" ;;
  start-runtime) lock_operation; start_runtime "$@" ;;
  logs) compose logs "$@" ;;
  stop)
    lock_operation
    if [[ -f "$MULTICA_SETUP_DIR/qa-state.json" && "$*" == runtime ]]; then
      compose --profile runtime --profile qa stop qa-docker runtime
    else
      compose --profile runtime --profile qa stop "$@"
    fi
    ;;
  down) [[ $# == 0 ]] || fail 'Usage: ./sandbox.sh down (volumes are preserved)'; lock_operation; compose --profile runtime --profile qa down ;;
  *) fail 'Usage: ./sandbox.sh {start|status|shell|cli|login|start-runtime|logs|stop|down}' ;;
esac
