#!/usr/bin/env bash
# Removes only regenerable Docker data; persistent volumes and containers are untouched.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
mode=${1:---report}
[[ $# -le 1 ]] || fail 'Usage: ./cleanup-docker.sh [--report|--apply|--scheduled]'
case "$mode" in --report|--apply|--scheduled) ;; *) fail 'Usage: ./cleanup-docker.sh [--report|--apply|--scheduled]' ;; esac
context=${CLEANUP_DOCKER_CONTEXT:-default}
age=${CLEANUP_MIN_AGE:-48h}
host_budget=${CLEANUP_HOST_CACHE:-20GB}
qa_budget=${CLEANUP_QA_CACHE:-10GB}
[[ "$age" =~ ^[0-9]+h$ ]] || fail 'CLEANUP_MIN_AGE must be an integer number of hours.'
[[ ${age%h} -ge 48 ]] || fail 'Keep at least 48 hours of recent images and cache.'
for budget in "$host_budget" "$qa_budget"; do
  [[ "$budget" =~ ^[1-9][0-9]*GB$ ]] || fail 'Cache budgets must be positive whole GB values.'
done
command -v docker >/dev/null || fail 'Docker CLI is required.'
# Explicit context prevents a shell DOCKER_HOST or selected builder from changing the target.
unset DOCKER_HOST DOCKER_CONTEXT BUILDX_BUILDER
host_docker() { docker --context "$context" "$@"; }
if ! host_docker info >/dev/null 2>&1; then
  if [[ "$mode" == --scheduled ]]; then
    printf 'Docker context %s is unavailable; skipping scheduled cleanup.\n' "$context"
    exit 0
  fi
  fail "Docker context $context is unavailable."
fi
qa_id=$(host_docker ps --filter "label=com.docker.compose.project=${MULTICA_PROJECT_NAME:-multica-sandbox}" \
  --filter label=com.docker.compose.service=qa-docker --format '{{.ID}}')
[[ "$qa_id" != *$'\n'* ]] || fail 'More than one QA Docker service matched.'
qa_docker() { host_docker exec "$qa_id" docker --host=unix:///run/user/1000/docker.sock "$@"; }
if [[ "$mode" == --report ]]; then
  printf 'Host Docker (%s):\n' "$context"
  host_docker system df
  if [[ -n "$qa_id" ]]; then
    printf '\nQA Docker:\n'
    qa_docker system df
  fi
  printf '\nApply: remove dangling images older than %s, then trim old unused build cache toward %s host / %s QA.\n' "$age" "$host_budget" "$qa_budget"
  printf 'No container, volume, tagged image, repository, credential or backup deletion.\n'
  exit 0
fi
lock_operation
umask 077
cleanup_log=$(mktemp)
trap 'rm -f -- "$cleanup_log"' EXIT
summarize() {
  if "$@" >"$cleanup_log" 2>&1; then
    # Avoid thousands of cache IDs in timer logs. Retain the reclaimed-space summary.
    tail -n 1 "$cleanup_log"
  else
    tail -n 8 "$cleanup_log" >&2
    return 1
  fi
}
clean() {
  local endpoint=$1 budget=$2 help_text
  local builder_args=()
  printf '\n%s: dangling images older than %s\n' "$endpoint" "$age"
  summarize "$endpoint" image prune --force --filter "until=$age"
  help_text=$("$endpoint" builder prune --help)
  if [[ "$help_text" == *--builder* ]]; then
    if [[ "$endpoint" == host_docker ]]; then
      builder_args=(--builder "$context")
    else
      builder_args=(--builder default)
    fi
  fi
  printf '%s: old unused build cache (target %s)\n' "$endpoint" "$budget"
  if [[ "$help_text" == *--max-used-space* ]]; then
    summarize "$endpoint" builder prune ${builder_args[@]+"${builder_args[@]}"} --force --filter "until=$age" --max-used-space "$budget"
  elif [[ "$help_text" == *--keep-storage* ]]; then
    summarize "$endpoint" builder prune ${builder_args[@]+"${builder_args[@]}"} --force --filter "until=$age" --keep-storage "$budget"
  else
    fail 'This Docker version has no supported cache-retention flag; skipped cache pruning.'
  fi
}
clean host_docker "$host_budget"
if [[ -n "$qa_id" ]]; then
  # If QA is restarting, its data remains untouched until the next maintenance run.
  if qa_docker info >/dev/null 2>&1; then
    clean qa_docker "$qa_budget"
  else
    printf 'QA Docker is unavailable; skipping its cleanup.\n'
  fi
fi
printf '\nCleanup complete. Containers and persistent data were preserved.\n'
