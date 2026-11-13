#!/usr/bin/env bash

set -euo pipefail
export MULTICA_SETUP_DIR
MULTICA_SETUP_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
UPSTREAM_COMPOSE="$MULTICA_SETUP_DIR/../docker-compose.selfhost.yml"

fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

require_docker() {
  command -v docker >/dev/null || fail 'Install Docker Engine and Docker Compose first.'
  docker compose version >/dev/null || fail 'Docker Compose v2 or newer is required.'
  docker info >/dev/null 2>&1 || fail 'Docker is not running or this user cannot access it.'
  [[ -f "$UPSTREAM_COMPOSE" ]] || fail "Missing upstream Compose file: $UPSTREAM_COMPOSE"
}

lock_operation() {
  command -v python3 >/dev/null || fail 'Install Python 3 first.'
  exec 9>"$MULTICA_SETUP_DIR/.operation.lock"
  python3 -c 'import fcntl,sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("Another install or update is in progress.")' || fail 'Could not lock the setup directory.'
}

unlock_operation() { exec 9>&-; }

require_env() {
  [[ -f "$MULTICA_SETUP_DIR/.env" ]] || fail 'Run ./install.sh first.'
}

compose() {
  local extra=() profiles=()
  if [[ -f "$MULTICA_SETUP_DIR/relay-state.json" ]]; then
    profiles=(--profile relay)
  fi
  if [[ -f "$MULTICA_SETUP_DIR/confluence-write-state.json" ]]; then
    profiles+=(--profile confluence-write)
  fi
  if [[ -f "$MULTICA_SETUP_DIR/compose.local.yml" ]]; then
    extra=(-f "$MULTICA_SETUP_DIR/compose.local.yml")
  fi
  if [[ -f "$MULTICA_SETUP_DIR/learner-private/config.json" ]]; then
    extra+=(-f "$MULTICA_SETUP_DIR/compose.learner.yml")
    profiles+=(--profile learner)
  fi
  LEARNER_UID="${LEARNER_UID:-$(id -u)}" LEARNER_GID="${LEARNER_GID:-$(id -g)}" \
  docker compose --project-name "${MULTICA_PROJECT_NAME:-multica-sandbox}" \
    --project-directory "$MULTICA_SETUP_DIR" --env-file "$MULTICA_SETUP_DIR/.env" \
    -f "$UPSTREAM_COMPOSE" -f "$MULTICA_SETUP_DIR/compose.sandbox.yml" ${extra[@]+"${extra[@]}"} ${profiles[@]+"${profiles[@]}"} "$@"
}

start_relay() {
  if [[ -f "$MULTICA_SETUP_DIR/confluence-write-state.json" ]]; then
    compose up -d --no-deps --no-build --pull never --wait --wait-timeout 120 confluence-write
  fi
  if [[ -f "$MULTICA_SETUP_DIR/relay-state.json" ]]; then
    compose up -d --no-deps --no-build --pull never --wait --wait-timeout 120 mcp-relay
  fi
}

start_app() {
  start_relay
  compose up -d --wait --wait-timeout 360 "$@" postgres backend frontend
  printf '\nFrontend: http://%s\n' "$(compose port frontend 3000)"
  printf 'Backend:  http://%s\n' "$(compose port backend 8080)"
}

start_runtime() {
  start_relay
  if [[ -f "$MULTICA_SETUP_DIR/qa-state.json" ]]; then
    compose --profile runtime --profile qa up -d --wait --wait-timeout 360 "$@" runtime qa-docker
    python3 "$MULTICA_SETUP_DIR/qa-client.py"
  else
    compose up -d --wait --wait-timeout 360 runtime "$@"
  fi
  if [[ -f "$MULTICA_SETUP_DIR/learner-private/config.json" ]]; then
    compose up -d --no-deps learner
  fi
}

set_build_metadata() {
  MULTICA_SOURCE_COMMIT=$(git -C "$MULTICA_SETUP_DIR/.." rev-parse HEAD)
  MULTICA_SOURCE_VERSION=$(git -C "$MULTICA_SETUP_DIR/.." describe --tags --match 'v[0-9]*' --dirty) ||
    fail 'Source release tags are missing. Fetch the upstream v* tags before building; see README.md.'
  [[ "$MULTICA_SOURCE_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+($|-) ]] ||
    fail "Source version is not recognizable: $MULTICA_SOURCE_VERSION"
  export MULTICA_SOURCE_COMMIT MULTICA_SOURCE_VERSION
}

build_images() {
  [[ -f "$MULTICA_SETUP_DIR/../server/internal/handler/worker_pool.go" ]] ||
    fail 'The Multica checkout is missing the worker-pool extension.'
  set_build_metadata
  local relay=()
  if [[ -f "$MULTICA_SETUP_DIR/relay-state.json" ]]; then
    relay=(mcp-relay)
  fi
  compose pull postgres frontend
  if [[ -f "$MULTICA_SETUP_DIR/qa-state.json" ]]; then
    compose --profile runtime --profile qa -f "$MULTICA_SETUP_DIR/compose.build.yml" build --pull "$@" backend runtime qa-docker ${relay[@]+"${relay[@]}"}
  else
    compose -f "$MULTICA_SETUP_DIR/compose.build.yml" build --pull "$@" backend runtime ${relay[@]+"${relay[@]}"}
  fi
}
