#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
require_docker
require_env
[[ -f "$MULTICA_SETUP_DIR/qa-state.json" ]] || fail 'Run ./setup-qa.sh first.'
compose exec -T runtime bash -s <<'SH'
set -euo pipefail
scratch=$(mktemp -d /workspaces/qa-docker-smoke.XXXXXX)
project="qa-smoke-${scratch##*.}"
project=${project,,}
cleanup() {
  result=$?
  if [[ "$result" != 0 ]]; then
    docker compose --project-name "$project" -f "$scratch/compose.json" logs --tail 30 >&2 || true
  fi
  docker compose --project-name "$project" -f "$scratch/compose.json" down --volumes --rmi local >/dev/null 2>&1 || true
  rm -rf -- "$scratch"
}
trap cleanup EXIT
printf 'qa-docker-ok\n' > "$scratch/index.html"
cat > "$scratch/Dockerfile" <<'DOCKERFILE'
FROM alpine:3.23
RUN apk add --no-cache busybox-extras
COPY index.html /www/index.html
CMD ["sh", "-ec", "cmp /data/index.html /www/index.html; exec httpd -f -p 8080 -h /www"]
DOCKERFILE
python3 - "$scratch" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
config = {"services": {"web": {
    "build": {"context": str(root)},
    "ports": ["127.0.0.1::8080"],
    "volumes": [{"type": "bind", "source": str(root), "target": "/data", "read_only": True}],
    "healthcheck": {"test": ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080"],
                    "interval": "1s", "timeout": "2s", "retries": 20}}}}
(root / 'compose.json').write_text(json.dumps(config))
PY
docker compose --project-name "$project" -f "$scratch/compose.json" up -d --build --wait --wait-timeout 120
address=$(docker compose --project-name "$project" -f "$scratch/compose.json" port web 8080)
[[ $(curl --fail --silent --retry 3 "http://$address") == qa-docker-ok ]]
[[ $(docker run --rm --network host alpine:3.23 wget -q -O - "http://$address") == qa-docker-ok ]]
printf 'PASS: build, Compose, workspace bind mount, runtime localhost, and nested host-network access\n'
docker compose --project-name "$project" -f "$scratch/compose.json" down --volumes --rmi local
[[ -z $(docker ps -aq --filter "label=com.docker.compose.project=$project") ]]
printf 'PASS: test containers removed; unrelated containers unchanged\n'
SH
