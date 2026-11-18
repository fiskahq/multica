#!/usr/bin/env bash
set -euo pipefail
collaboration_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
collaboration_deployment=$collaboration_source
if [[ $# == 2 && $1 == --deployment-dir ]]; then
  collaboration_deployment=$(cd -- "$2" && pwd)
elif [[ $# != 0 ]]; then
  printf 'Usage: bash setup-collaboration-tools.sh [--deployment-dir DIR]\n' >&2
  exit 1
fi
source "$collaboration_deployment/common.sh"
require_docker
require_env
lock_operation
umask 077
reload=$(python3 "$collaboration_source/configure-collaboration-tools.py" prepare --deployment-dir "$collaboration_deployment")
if [[ $reload == reload ]]; then
  compose --profile relay up -d --no-deps --force-recreate --no-build --pull never --wait --wait-timeout 120 mcp-relay
  python3 "$collaboration_source/configure-collaboration-tools.py" reloaded --deployment-dir "$collaboration_deployment"
fi
python3 "$collaboration_source/configure-collaboration-tools.py" register --deployment-dir "$collaboration_deployment"
unlock_operation
python3 "$collaboration_source/setup-relay-stdio.py" --deployment-dir "$collaboration_deployment"
python3 "$collaboration_source/configure-jira-policy.py" --deployment-dir "$collaboration_deployment"
printf 'Jira ticket/release and Confluence page access configured. Agent runtime was not restarted.\n'
