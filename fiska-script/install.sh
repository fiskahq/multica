#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

[[ $# == 0 || $# == 2 && $1 == --agents ]] || fail 'Usage: ./install.sh [--agents agents.json]'
agents_file=${2:-}
umask 077
lock_operation
require_docker
if [[ ! -f "$MULTICA_SETUP_DIR/.env" ]]; then
  command -v openssl >/dev/null || fail 'Install openssl to generate secrets.'
  password=$(openssl rand -hex 32)
  jwt=$(openssl rand -hex 32)
  sed -e "s/POSTGRES_PASSWORD=GENERATE_ON_INSTALL/POSTGRES_PASSWORD=$password/" \
      -e "s/JWT_SECRET=GENERATE_ON_INSTALL/JWT_SECRET=$jwt/" \
      "$MULTICA_SETUP_DIR/.env.example" > "$MULTICA_SETUP_DIR/.env"
  unset password jwt
fi
compose config --quiet
# Installer arguments configure provisioning, not Compose.
# shellcheck disable=SC2119
build_images
# shellcheck disable=SC2119
start_app
printf '\nApp installed. Follow README.md to authenticate and start the agent runtime.\n'
if [[ -n "$agents_file" ]]; then
  # Provisioning takes the same operation lock after installation releases it.
  unlock_operation
  "$MULTICA_SETUP_DIR/setup-agents.sh" "$agents_file"
fi
