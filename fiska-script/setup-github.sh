#!/usr/bin/env bash
# Import the host's GitHub login through stdin, never through command arguments.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

[[ $# == 0 ]] || fail 'Usage: ./setup-github.sh (imports the current host GitHub login)'
umask 077
lock_operation
require_docker
require_env
command -v gh >/dev/null || fail 'Install GitHub CLI on the host and run gh auth login first.'
gh auth status --hostname github.com >/dev/null 2>&1 || fail 'Run gh auth login --hostname github.com on the host first.'
compose exec -T runtime gh --version >/dev/null || fail 'Start a runtime image that includes GitHub CLI.'
gh auth token --hostname github.com |
  compose exec -T runtime sh -c 'umask 077; gh auth login --hostname github.com --git-protocol ssh --with-token'
printf 'Runtime GitHub account: '
compose exec -T runtime gh api user --jq .login
