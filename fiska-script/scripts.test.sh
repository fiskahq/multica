#!/usr/bin/env bash
set -euo pipefail
test_dir=$(mktemp -d)
trap 'rm -rf -- "$test_dir"' EXIT
mkdir -p "$test_dir/multica/fiska-script" "$test_dir/multica" "$test_dir/bin"
cp "$(dirname -- "${BASH_SOURCE[0]}")/"*.sh "$test_dir/multica/fiska-script/"
cp "$(dirname -- "${BASH_SOURCE[0]}")/.env.example" "$test_dir/multica/fiska-script/"
touch "$test_dir/multica/docker-compose.selfhost.yml"
mkdir -p "$test_dir/multica/server/internal/handler"
touch "$test_dir/multica/server/internal/handler/worker_pool.go"
cat > "$test_dir/bin/git" <<'GIT'
#!/usr/bin/env bash
case " $* " in
  *' describe '*) [[ ${TEST_VERSION_MISSING:-0} == 0 ]] || exit 1; echo v0.4.33-12-gabcdef0-dirty ;;
  *) echo test-commit ;;
esac
GIT
chmod +x "$test_dir/bin/git"
export TEST_DOCKER_LOG="$test_dir/docker.log"
export PATH="$test_dir/bin:$PATH"
cat > "$test_dir/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_DOCKER_LOG"
if [[ " $* " == *' build '* ]]; then
  [[ ${MULTICA_SOURCE_VERSION:-} == v0.4.33-12-gabcdef0-dirty ]]
  [[ ${MULTICA_SOURCE_COMMIT:-} == test-commit ]]
fi
case " $* " in
  *' ps --status running -q qa-docker '*) [[ ${TEST_QA_RUNNING:-0} == 0 ]] || echo qa-container-id ;;
  *' ps --status running '*) [[ ${TEST_RUNTIME_RUNNING:-0} == 0 ]] || echo container-id ;;
  *' exec -T postgres '*) [[ ${TEST_BACKUP_FAIL:-0} == 0 ]] || exit 1; echo database-dump ;;
  *' port frontend '*) echo 127.0.0.1:3100 ;;
  *' port backend '*) echo 127.0.0.1:8180 ;;
esac
exit 0
DOCKER
chmod +x "$test_dir/bin/docker"
cd "$test_dir/multica/fiska-script"
bash install.sh >/dev/null
python3 -c 'import os; assert os.stat(".env").st_mode & 0o777 == 0o600'
if grep -q GENERATE_ON_INSTALL .env; then exit 1; fi
cp .env "$test_dir/original.env"
bash install.sh >/dev/null
cmp .env "$test_dir/original.env"
printf 'PASS: install generates private secrets and preserves existing configuration\n'

: > "$TEST_DOCKER_LOG"
if TEST_VERSION_MISSING=1 bash update.sh > "$test_dir/result" 2>&1; then
  echo 'FAIL: update accepted an unknown source version' >&2; exit 1
fi
if grep -Eq ' (pull|build|up) ' "$TEST_DOCKER_LOG"; then exit 1; fi
printf 'PASS: builds require and export the source version and commit\n'

: > "$TEST_DOCKER_LOG"
if TEST_BACKUP_FAIL=1 bash update.sh > "$test_dir/result" 2>&1; then
  echo 'FAIL: update accepted failed backup' >&2; exit 1
fi
if grep -q ' up -d ' "$TEST_DOCKER_LOG"; then exit 1; fi
[[ -z $(find backups -type f -print -quit) ]]
printf 'PASS: failed backup prevents replacement and removes partial dump\n'

: > "$TEST_DOCKER_LOG"
if TEST_RUNTIME_RUNNING=1 bash update.sh > "$test_dir/result" 2>&1; then
  echo 'FAIL: update accepted running runtime' >&2; exit 1
fi
if grep -Eq ' (pull|build|up) ' "$TEST_DOCKER_LOG"; then exit 1; fi
printf 'PASS: running runtime blocks updates\n'

bash update.sh >/dev/null
grep -q 'database-dump' backups/*/database.dump
cmp .env "$test_dir/original.env"
printf 'PASS: successful update preserves configuration and saves backup\n'

(
  source ./common.sh
  lock_operation
  if bash install.sh > "$test_dir/result" 2>&1; then
    echo 'FAIL: concurrent install was allowed' >&2; exit 1
  fi
  grep -q 'Another install or update' "$test_dir/result"
)
printf 'PASS: concurrent operations are rejected\n'

: > "$TEST_DOCKER_LOG"
bash sandbox.sh start >/dev/null
grep -q -- '--pull never --no-build' "$TEST_DOCKER_LOG"
printf 'PASS: app restart uses installed images\n'

printf '{"enabled":true}\n' > qa-state.json
cat > qa-client.py <<'PY'
import os
with open(os.environ['TEST_DOCKER_LOG'], 'a') as log:
    log.write('qa-client-refreshed\n')
PY
: > "$TEST_DOCKER_LOG"
if TEST_QA_RUNNING=1 bash update.sh > "$test_dir/result" 2>&1; then
  echo 'FAIL: update accepted a running QA daemon' >&2; exit 1
fi
if grep -Eq ' (pull|build|up) ' "$TEST_DOCKER_LOG"; then exit 1; fi
printf 'PASS: running QA daemon blocks updates\n'

: > "$TEST_DOCKER_LOG"
bash sandbox.sh stop runtime
grep -q 'stop qa-docker runtime' "$TEST_DOCKER_LOG"
bash sandbox.sh start-runtime
grep -q 'runtime qa-docker' "$TEST_DOCKER_LOG"
grep -q 'qa-client-refreshed' "$TEST_DOCKER_LOG"
printf 'PASS: runtime lifecycle includes QA and refreshes its connection\n'

: > "$TEST_DOCKER_LOG"
TEST_RUNTIME_RUNNING=1 bash sandbox.sh shell -c 'docker info'
grep -q 'exec runtime bash -c docker info' "$TEST_DOCKER_LOG"
if grep -q 'run --rm' "$TEST_DOCKER_LOG"; then exit 1; fi
printf 'PASS: shell enters the running runtime network namespace\n'

printf '{"enabled":true}\n' > relay-state.json
: > "$TEST_DOCKER_LOG"
bash sandbox.sh start >/dev/null
grep -q -- '--profile relay up -d --no-deps --no-build --pull never --wait --wait-timeout 120 mcp-relay' "$TEST_DOCKER_LOG"
bash sandbox.sh stop >/dev/null
grep -q -- '--profile relay --profile runtime --profile qa stop' "$TEST_DOCKER_LOG"
: > "$TEST_DOCKER_LOG"
bash update.sh >/dev/null
grep -q 'build --pull --no-cache backend runtime qa-docker mcp-relay' "$TEST_DOCKER_LOG"
printf 'PASS: enabled relay starts with the app, stops with the stack, and is rebuilt on update\n'
