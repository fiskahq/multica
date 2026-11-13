#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

[[ $# == 0 ]] || fail 'Usage: ./update.sh (builds the current modified checkout)'
umask 077
lock_operation
require_docker
require_env
compose config --quiet

runtime_running=$(compose ps --status running -q runtime)
if [[ -n "$runtime_running" ]]; then
  fail 'Stop the runtime after active tasks finish: ./sandbox.sh stop runtime. Then retry the update.'
fi
if [[ -f "$MULTICA_SETUP_DIR/qa-state.json" ]] &&
   [[ -n "$(compose --profile runtime --profile qa ps --status running -q qa-docker)" ]]; then
  fail 'Stop the QA daemon with ./sandbox.sh stop runtime before updating.'
fi
# Fetch and build before interrupting any running service.
build_images --no-cache

mkdir -p "$MULTICA_SETUP_DIR/backups"
backup_dir=$(mktemp -d "$MULTICA_SETUP_DIR/backups/database-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
backup="$backup_dir/database.dump"
# Expand database credentials inside the container.
# shellcheck disable=SC2016
if ! compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup"; then
  rm -f -- "$backup"
  rmdir -- "$backup_dir"
  fail 'Database backup failed. No containers were replaced.'
fi
[[ -s "$backup" ]] || fail 'Database backup is empty. No containers were replaced.'
printf 'Database backup: %s\n' "$backup"
start_app "$@"
printf '\nUpdate complete. Start a configured runtime with ./sandbox.sh start-runtime.\n'
