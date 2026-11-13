#!/usr/bin/env bash
# Interactive entry point, compatible with the Bash shipped by macOS.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
umask 077

ask() {
  local field=$1 prompt=$2 fallback=${3:-} answer_value
  while true; do
    printf '%s' "$prompt" >&2
    [[ -z "$fallback" ]] || printf ' [%s]' "$fallback" >&2
    printf ': ' >&2
    IFS= read -r answer_value || fail 'Input closed. No further installation steps were run.'
    answer_value=${answer_value:-$fallback}
    if [[ -n "$answer_value" ]]; then printf -v "$field" '%s' "$answer_value"; return; fi
    printf 'Enter a value.\n' >&2
  done
}

number() {
  local field=$1 prompt=$2 fallback=$3 minimum=$4 maximum=$5 reply
  while true; do
    ask reply "$prompt" "$fallback"
    if [[ "$reply" =~ ^[0-9]{1,5}$ ]] && ((10#$reply >= minimum && 10#$reply <= maximum)); then
      printf -v "$field" '%s' "$((10#$reply))"; return
    fi
    printf 'Enter a whole number from %s to %s.\n' "$minimum" "$maximum" >&2
  done
}

yesno() {
  local field=$1 prompt=$2 fallback=$3 reply
  while true; do
    ask reply "$prompt (yes/no)" "$fallback"
    case "$reply" in
      y|Y|yes|YES) printf -v "$field" true; return ;;
      n|N|no|NO) printf -v "$field" false; return ;;
    esac
  done
}

reference() {
  local field=$1 prompt=$2 reply
  while true; do
    ask reply "$prompt (op://vault/item/field)"
    if [[ "$reply" == op://*/*/* ]]; then printf -v "$field" '%s' "$reply"; return; fi
    printf 'Enter a 1Password reference, not an API key.\n' >&2
  done
}

env_value() { python3 "$MULTICA_SETUP_DIR/wizard-config.py" read "$1" "$2"; }

case "${1:-}" in
  '') mode=install ;;
  --configure-only) mode=configure ;;
  --resume) mode=resume ;;
  --help|-h)
    printf 'Usage: ./setup.sh [--configure-only | --resume]\nInteractive setup for a fresh Multica workspace and Claude/Kiro team.\n'
    exit 0 ;;
  *) fail 'Usage: ./setup.sh [--configure-only | --resume]' ;;
esac
[[ $# -le 1 ]] || fail 'Only one setup option is allowed.'
[[ -t 0 ]] || fail 'Run setup.sh in a terminal so it can ask questions and open provider login flows.'

printf 'Multica guided setup (%s, %s)\n' "$(uname -s)" "$(uname -m)"
if [[ $(uname -s) == Darwin ]] && command -v brew >/dev/null; then
  missing=false
  for dependency in python3 git docker; do command -v "$dependency" >/dev/null || missing=true; done
  if [[ "$missing" == true ]]; then
    yesno install_dependencies 'Install missing Python/Git/Docker Desktop prerequisites with Homebrew?' yes
    if [[ "$install_dependencies" == true ]]; then
      command -v python3 >/dev/null || brew install python
      command -v git >/dev/null || brew install git
      command -v docker >/dev/null || brew install --cask docker
    fi
  fi
fi
for dependency in python3 git docker; do
  if ! command -v "$dependency" >/dev/null; then
    if [[ $(uname -s) == Darwin ]]; then
      printf 'Install Docker Desktop, Python 3 and Git first. With Homebrew:\n  brew install python git\n  brew install --cask docker\n  open -a Docker\n' >&2
    fi
    fail "Missing prerequisite: $dependency. Install it and rerun this script."
  fi
done
if [[ $(uname -s) == Darwin ]] && ! docker info >/dev/null 2>&1; then
  open -a Docker
  ask ready 'Press Enter when Docker Desktop is running' ready
fi
require_docker
compose_version=$(docker compose version --short)
python3 -c 'import re,sys; m=re.search(r"(\d+)\.(\d+)",sys.argv[1]); sys.exit(0 if m and tuple(map(int,m.groups())) >= (2,17) else "Docker Compose 2.17 or newer is required.")' "$compose_version"
[[ -f "$MULTICA_SETUP_DIR/../server/internal/handler/worker_pool.go" &&
   -f "$MULTICA_SETUP_DIR/../server/internal/service/kiro_credential.go" ]] ||
  fail 'Use the supplied custom Multica checkout. An upstream-only clone lacks the required pool changes.'
if ! git -C "$MULTICA_SETUP_DIR/.." describe --tags --match 'v[0-9]*' >/dev/null 2>&1; then
  yesno fetch_tags 'Fetch upstream release tags needed to build this checkout?' yes
  [[ "$fetch_tags" == true ]] || fail 'Release tags are required before installation.'
  git -C "$MULTICA_SETUP_DIR/.." fetch --no-write-fetch-head https://github.com/multica-ai/multica.git 'refs/tags/v*:refs/tags/v*'
fi
set_build_metadata
if [[ -f "$MULTICA_SETUP_DIR/.env" && -n "$(compose ps --status running -q runtime)" ]]; then
  compose exec -T runtime curl -fsS http://127.0.0.1:19514/health | python3 -c 'import json,sys; h=json.load(sys.stdin); sys.exit(0 if h.get("active_task_count")==0 and h.get("pending_terminal_report_count")==0 else "Wait for agent tasks and pending reports to finish before running setup.")'
  yesno stop_runtime 'The runtime is idle. Stop it and QA Docker before applying setup settings?' yes
  [[ "$stop_runtime" == true ]] || fail 'Setup cancelled. The running runtime was not changed.'
  "$MULTICA_SETUP_DIR/sandbox.sh" stop runtime
fi

if [[ "$mode" != resume ]]; then
  capacity=$(docker info --format '{{.NCPU}} {{.MemTotal}}')
  read -r available_cpus available_bytes <<< "$capacity"
  available_gib=$((available_bytes / 1073741824))
  [[ "$available_cpus" -ge 1 && "$available_gib" -ge 6 ]] || fail 'Allocate at least 6 GiB to Docker before setup.'
  printf '\nDocker has %s CPUs and %s GiB RAM available. On Mac these are Docker Desktop VM limits.\n' "$available_cpus" "$available_gib"
  printf 'Leave about 4 GiB for the app/database in addition to the runtime and optional QA limits.\n'
  suggested_cpus=4; ((available_cpus >= 4)) || suggested_cpus=$available_cpus
  suggested_memory=6; ((available_gib >= 10)) || suggested_memory=$((available_gib - 4))
  while true; do
    number runtime_cpus 'Agent runtime CPUs' "$(env_value RUNTIME_CPUS "$suggested_cpus")" 1 "$available_cpus"
    memory_default=$(env_value RUNTIME_MEMORY_LIMIT "${suggested_memory}g")
    number runtime_memory 'Agent runtime RAM in GiB' "${memory_default%[gG]}" 2 2048
    number concurrency 'Maximum simultaneous agent tasks' "$(env_value RUNTIME_MAX_CONCURRENT_TASKS 2)" 1 64
    qa=false
    if [[ -f "$MULTICA_SETUP_DIR/qa-state.json" ]]; then
      qa=true; printf 'Keeping the already enabled QA Docker service.\n'
    else
      printf 'QA Docker lets agents build and run test containers in a separate daemon. Its outer container requires privileged mode.\n'
      yesno qa 'Enable QA Docker?' no
    fi
    qa_cpus=2; qa_memory=2
    if [[ "$qa" == true ]]; then
      number qa_cpus 'QA daemon CPU limit' "$(env_value QA_DOCKER_CPUS 2)" 1 "$available_cpus"
      memory_default=$(env_value QA_DOCKER_MEMORY_LIMIT 2g)
      number qa_memory 'QA daemon RAM in GiB' "${memory_default%[gG]}" 2 2048
    fi
    planned_memory=$((runtime_memory + 4))
    [[ "$qa" != true ]] || planned_memory=$((planned_memory + qa_memory))
    ((planned_memory <= available_gib)) && break
    printf 'Those limits plus app headroom need about %s GiB; Docker currently has %s GiB. Choose smaller limits or increase Docker memory and rerun.\n' "$planned_memory" "$available_gib"
  done
  while true; do
    number frontend_port 'Browser port' "$(env_value FRONTEND_PORT 3100)" 1024 65535
    number backend_port 'API port' "$(env_value BACKEND_PORT 8180)" 1024 65535
    [[ "$frontend_port" != "$backend_port" ]] && break
    printf 'Browser and API ports must be different.\n'
  done
  relay=false
  if [[ -f "$MULTICA_SETUP_DIR/relay-state.json" ]]; then
    relay=true; printf 'Keeping the already enabled MCP relay and its configuration.\n'
  else
    yesno relay 'Enable the custom MCP relay?' no
  fi
  relay_port=$(env_value RELAY_HTTP_PORT 8090)
  if [[ "$relay" == true ]]; then
    while true; do
      number relay_port 'MCP relay host port' "$relay_port" 1024 65535
      [[ "$relay_port" != "$frontend_port" && "$relay_port" != "$backend_port" ]] && break
      printf 'The MCP relay needs a distinct port.\n'
    done
  fi
  yesno github 'Configure GitHub CLI access in the runtime?' yes
  claude_method=claude_login; claude_ref=; codex_method=codex_login; codex_ref=
  squad_name='Coding squad'; main_name='Main Claude coordinator'
  worker_args=()
  if [[ -f "$MULTICA_SETUP_DIR/agents.json" ]]; then
    printf 'Preserving existing agents.json and its credential references.\n'
  else
    ask squad_name 'Squad name' "$squad_name"
    ask main_name 'Claude coordinator name' "$main_name"
    yesno subscription 'Use a Claude subscription login?' yes
    if [[ "$subscription" != true ]]; then
      claude_method=anthropic_api_key; reference claude_ref 'Claude API key reference'
    fi
    yesno subscription 'Use a Codex subscription login?' yes
    if [[ "$subscription" != true ]]; then
      codex_method=codex_api_key; reference codex_ref 'Codex API key reference'
    fi
    number workers 'Number of Kiro keys/workers' 1 1 64
    for ((i=1; i<=workers; i++)); do
      reference worker_ref "Kiro worker $i key reference"
      while true; do
        ask capabilities "Kiro worker $i capabilities (comma-separated lowercase labels)" code,review
        if python3 -c 'import re,sys; c=sys.argv[1].split(","); sys.exit(0 if len(c)<=32 and len(c)==len(set(c)) and all(re.fullmatch(r"[a-z][a-z0-9_-]{0,63}",x) for x in c) else 1)' "$capabilities"; then break; fi
        printf 'Use unique lowercase labels such as code,review,qa.\n'
      done
      worker_args+=("$worker_ref" "$capabilities")
    done
  fi
  plan=$(python3 - "$runtime_cpus" "$runtime_memory" "$concurrency" "$qa" "$qa_cpus" "$qa_memory" \
    "$frontend_port" "$backend_port" "$relay" "$relay_port" "$github" "$squad_name" "$main_name" \
    "$claude_method" "$claude_ref" "$codex_method" "$codex_ref" ${worker_args[@]+"${worker_args[@]}"} <<'PY'
import json,sys
a=sys.argv[1:]
keys=['runtime_cpus','runtime_memory','concurrency','qa','qa_cpus','qa_memory','frontend_port','backend_port','relay','relay_port','github']
d={k:(v=='true' if k in ['qa','relay','github'] else int(v)) for k,v in zip(keys,a)}
credentials=[{'type':a[13]}, {'type':a[15]}]
for entry,ref in zip(credentials,[a[14],a[16]]):
    if ref: entry['reference']=ref
d['agents']={'squad_name':a[11],'pool_name':'kiro','main':{'name':a[12],'credentials':credentials},'workers':[
    {'name':f'Kiro worker {i//2+1}','capabilities':a[18+i].split(','),
     'credential':{'type':'kiro_api_key','reference':a[17+i]}} for i in range(0,len(a)-17,2)]}
print(json.dumps(d))
PY
)
  printf '\nPlan: runtime %s CPUs / %s GiB / %s tasks; QA=%s; MCP relay=%s; browser=http://localhost:%s\n' \
    "$runtime_cpus" "$runtime_memory" "$concurrency" "$qa" "$relay" "$frontend_port"
  yesno proceed 'Save this configuration and continue?' yes
  [[ "$proceed" == true ]] || { printf 'Cancelled; configuration was not changed.\n'; exit 0; }
  lock_operation
  printf '%s\n' "$plan" | python3 "$MULTICA_SETUP_DIR/wizard-config.py" write
  unlock_operation
  if [[ "$mode" == configure ]]; then
    printf 'Configuration saved privately. Run ./setup.sh --resume to install.\n'; exit 0
  fi
else
  [[ -f "$MULTICA_SETUP_DIR/wizard-state.json" ]] || fail 'Run ./setup.sh first to choose your configuration.'
fi

preference() { python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))[sys.argv[2]]; print(str(v).lower() if isinstance(v,bool) else v)' "$MULTICA_SETUP_DIR/wizard-state.json" "$1"; }
printf '\nInstalling the app. If a step fails, fix it and rerun ./setup.sh --resume.\n'
if [[ -n "$(compose ps -a -q backend)" ]]; then
  "$MULTICA_SETUP_DIR/sandbox.sh" start
else
  "$MULTICA_SETUP_DIR/install.sh"
fi
url="http://localhost:$(preference frontend_port)"
printf '\nOpen %s, sign in and create your workspace. Create a personal access token in settings.\n' "$url"
printf 'For local email sign-in codes, use another terminal: ./sandbox.sh logs --tail 100 backend\n'
if [[ $(uname -s) == Darwin ]]; then open "$url"; fi
ask ready 'Press Enter when your workspace is ready' ready
"$MULTICA_SETUP_DIR/setup-agents.sh" "$MULTICA_SETUP_DIR/agents.json"
if [[ $(preference github) == true ]]; then
  if command -v gh >/dev/null && gh auth status --hostname github.com >/dev/null 2>&1; then
    yesno reuse_github 'Import your existing host GitHub CLI login into Docker?' yes
  else
    reuse_github=false
  fi
  if [[ "$reuse_github" == true ]]; then
    "$MULTICA_SETUP_DIR/setup-github.sh"
  else
    compose exec runtime gh auth login --hostname github.com --git-protocol https --web
  fi
  compose exec -T runtime gh auth setup-git --hostname github.com
fi
if [[ $(preference relay) == true ]]; then
  python3 "$MULTICA_SETUP_DIR/wizard-relay.py" prepare
  "$MULTICA_SETUP_DIR/setup-relay.sh"
  python3 "$MULTICA_SETUP_DIR/wizard-relay.py" register
fi
if [[ $(preference qa) == true ]]; then
  "$MULTICA_SETUP_DIR/setup-qa.sh"
  "$MULTICA_SETUP_DIR/qa-smoke.sh"
fi
"$MULTICA_SETUP_DIR/sandbox.sh" status
"$MULTICA_SETUP_DIR/configure-kiro-pool.sh" --status
printf '\nSetup complete: %s\nClaude coordinates; Codex is available as a tool; Kiro keys rotate across Kiro tasks.\n' "$url"
printf 'No paid model task was run. OpenCode is installed; run opencode auth login inside ./sandbox.sh shell if needed.\n'
