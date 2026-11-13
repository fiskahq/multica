#!/usr/bin/env bash
# Copy the host subscription login through stdin without exposing tokens.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"

[[ $# == 0 ]] || fail 'Usage: ./setup-claude.sh (imports the current host Claude login)'
umask 077
lock_operation
require_docker
require_env
command -v python3 >/dev/null || fail 'Python 3 is required.'
command -v claude >/dev/null || fail 'Install Claude Code and run claude auth login on the host first.'
claude auth status >/dev/null || fail 'Run claude auth login on the host first.'
compose exec -T runtime claude --version >/dev/null || fail 'Start the runtime first.'

python3 -c '
import json, os, pathlib, sys, time
directory = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
try:
    data = json.loads((directory / ".credentials.json").read_text())
    login = data.get("claudeAiOauth", {})
    valid = (login.get("accessToken") and login.get("refreshToken")
             and login.get("expiresAt", 0) > time.time() * 1000)
except (OSError, ValueError, TypeError, AttributeError):
    valid = False
if not valid:
    sys.exit("No unexpired subscription login found. Run claude auth login on the host first.")
json.dump(data, sys.stdout)
' | compose exec -T runtime python3 -c '
import json, os, pathlib, sys, tempfile, time
try:
    data = json.load(sys.stdin)
    login = data.get("claudeAiOauth", {})
    valid = (login.get("accessToken") and login.get("refreshToken")
             and login.get("expiresAt", 0) > time.time() * 1000)
except (ValueError, TypeError, AttributeError):
    valid = False
if not valid:
    sys.exit("No valid host login received; runtime credentials were not changed.")
directory = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix=".credentials-", dir=directory)
try:
    with os.fdopen(fd, "w") as output:
        json.dump(data, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, directory / ".credentials.json")
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
'
compose exec -T runtime claude auth status
printf '\nClaude login imported. Retry the failed task; no runtime restart is required.\n'
