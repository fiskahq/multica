#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
test_dir=$(mktemp -d)
trap 'rm -rf -- "$test_dir"' EXIT
mkdir -p "$test_dir/bin" "$test_dir/setup"
cp "$root/cleanup-docker.sh" "$root/common.sh" "$test_dir/setup/"
export TEST_DOCKER_LOG="$test_dir/docker.log"
export PATH="$test_dir/bin:$PATH"
cat > "$test_dir/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_DOCKER_LOG"
case " $* " in
  *' info '*) [[ ${TEST_DOCKER_DOWN:-0} == 0 ]] ;;
  *' ps '*) [[ ${TEST_NO_QA:-0} == 1 ]] || echo qa-id ;;
  *' builder prune --help '*)
    if [[ ${TEST_LEGACY:-0} == 1 ]]; then echo '--keep-storage'; else echo '--max-used-space --builder'; fi ;;
  *' prune '*) echo 'Total: 1GB' ;;
  *' system df '*) echo 'usage report' ;;
esac
DOCKER
chmod +x "$test_dir/bin/docker"
script="$test_dir/setup/cleanup-docker.sh"
: > "$TEST_DOCKER_LOG"
bash "$script" --report >/dev/null
! grep -q prune "$TEST_DOCKER_LOG"
echo 'PASS: report never prunes'
: > "$TEST_DOCKER_LOG"
CLEANUP_DOCKER_CONTEXT=desktop-linux bash "$script" --apply >/dev/null
grep -q -- '--context desktop-linux builder prune --builder desktop-linux --force --filter until=48h --max-used-space 20GB' "$TEST_DOCKER_LOG"
grep -q -- 'exec qa-id docker --host=unix:///run/user/1000/docker.sock builder prune --builder default --force --filter until=48h --max-used-space 10GB' "$TEST_DOCKER_LOG"
! grep -Eq 'volume prune|container prune|system prune|image prune.*--all| rm | rmi ' "$TEST_DOCKER_LOG"
echo 'PASS: explicit host and QA targets; only old dangling images and cache are pruned'
: > "$TEST_DOCKER_LOG"
TEST_LEGACY=1 TEST_NO_QA=1 bash "$script" --apply >/dev/null
grep -q -- '--keep-storage 20GB' "$TEST_DOCKER_LOG"
! grep -q 'exec qa-id' "$TEST_DOCKER_LOG"
echo 'PASS: older Docker retention flag and absent QA service'
: > "$TEST_DOCKER_LOG"
TEST_DOCKER_DOWN=1 bash "$script" --scheduled >/dev/null
! grep -q prune "$TEST_DOCKER_LOG"
echo 'PASS: scheduled maintenance skips unavailable Docker'
: > "$TEST_DOCKER_LOG"
if CLEANUP_MIN_AGE=1h bash "$script" --apply >/dev/null 2>&1; then exit 1; fi
[[ ! -s "$TEST_DOCKER_LOG" ]]
echo 'PASS: unsafe retention rejected before Docker access'
(
  source "$test_dir/setup/common.sh"
  lock_operation
  if bash "$script" --apply >"$test_dir/error" 2>&1; then exit 1; fi
  grep -q 'Another install or update' "$test_dir/error"
)
echo 'PASS: maintenance cannot overlap managed install/update operations'
