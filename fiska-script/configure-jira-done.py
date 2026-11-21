#!/usr/bin/env python3
"""Install Jira Done synchronization without restarting agents or changing existing Done tickets."""
import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = load("jira_done", ROOT / "jira-done/service.py")
provision = load("provision", ROOT / "provision-agents.py")


def docker(deployment, *args):
    result = subprocess.run(["bash", "-c", 'source "$1/common.sh"; shift; require_docker; require_env; compose "$@"',
                             "bash", str(deployment), *args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Docker operation failed; inspect service health. Credentials omitted.")
    return result.stdout


def relay_call(url, token, method, params, ident):
    data = worker.http(url, "POST", {"Authorization": "Bearer " + token,
                      "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-03-26"},
                       dict(jsonrpc="2.0", id=ident, method=method, params=params))
    if data.get("id") != ident or data.get("error") or data.get("result", {}).get("isError"):
        raise RuntimeError("Read-only Jira account verification failed")
    return data["result"]


def configure(deployment, assignee):
    relay = json.loads((deployment / "mcp-relay/config.json").read_text())
    endpoints = relay["http"]["endpoints"]
    reader = next(e for e in endpoints if e["name"] == "jira")
    writer = next(e for e in endpoints if e["name"] == "jira_triage")
    def token(endpoint):
        return (deployment / "mcp-relay/.secrets" / Path(endpoint["token"]["file"]).name).read_text().strip()
    address = docker(deployment, "port", "mcp-relay", "8080").strip()
    if not address.startswith("127.0.0.1:"):
        raise RuntimeError("Expected the relay to publish a loopback port")
    url = "http://" + address + reader["path"]
    relay_call(url, token(reader), "initialize", dict(protocolVersion="2025-03-26", capabilities={},
               clientInfo=dict(name="jira-done-setup", version="1")), 1)
    result = relay_call(url, token(reader), "tools/call", dict(name="jira__request", arguments=dict(
                        method="GET", path="/rest/api/3/user/search", query={"query": assignee})), 2)
    users = json.loads(result["content"][0]["text"])
    if isinstance(users, dict):
        users = users.get("data", users)
    matches = [u for u in users if u.get("active") and u.get("displayName", "").casefold() == assignee.casefold()]
    if len(matches) != 1:
        raise RuntimeError("Expected one active Jira account with the exact assignee name")
    credentials = json.loads(docker(deployment, "exec", "-T", "runtime", "cat", "/home/node/.multica/config.json"))
    private = deployment / "jira-done-private"
    private.mkdir(mode=0o700, exist_ok=True)
    (private / "data").mkdir(mode=0o700, exist_ok=True)
    path = private / "config.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    if old and old["workspace_id"] != credentials["workspace_id"]:
        raise RuntimeError("Existing sync state belongs to another workspace")
    config = dict(old, server_url="http://backend:8080", workspace_id=credentials["workspace_id"],
                  token=credentials["token"], relay_url="http://mcp-relay:8080" + writer["path"],
                  relay_token=token(writer), jira_site=relay["jira"]["site"],
                  assignee_id=matches[0]["accountId"], assignee_name=matches[0]["displayName"])
    config.setdefault("shared_close_overrides", {})
    provision.write_state(path, config)
    docker(deployment, "up", "-d", "--no-deps", "--force-recreate", "jira-done")
    print("Jira Done sync enabled. Assignee: " + config["assignee_name"] + ". Existing Done tickets are not backfilled.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="install", choices=["install", "status", "retry", "start", "stop", "allow-shared-close", "revoke-shared-close"])
    parser.add_argument("issue", nargs="?")
    parser.add_argument("--reason", help="User authorization for closing a shared Jira ticket before all linked work is Done")
    parser.add_argument("--assignee", default="David Bernard")
    parser.add_argument("--deployment-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    deployment = args.deployment_dir.resolve()
    os.umask(0o077)
    try:
        with (deployment / ".operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == "install":
                configure(deployment, args.assignee)
            elif args.action in ("allow-shared-close", "revoke-shared-close"):
                if not args.issue or (args.action == "allow-shared-close" and not args.reason):
                    raise RuntimeError("Provide the Multica issue and --reason with the user's authorization")
                issue = json.loads(docker(deployment, "exec", "-T", "-w", "/home/node", "runtime",
                                         "multica", "issue", "get", args.issue, "--output", "json"))
                path = deployment / "jira-done-private/config.json"
                config = json.loads(path.read_text())
                overrides = config.setdefault("shared_close_overrides", {})
                if args.action == "allow-shared-close":
                    overrides[issue["id"]] = dict(reason=args.reason, recorded_at=datetime.now(timezone.utc).isoformat())
                else:
                    overrides.pop(issue["id"], None)
                provision.write_state(path, config)
                docker(deployment, "up", "-d", "--no-deps", "--force-recreate", "jira-done")
                docker(deployment, "exec", "-T", "jira-done", "python3", "-B", "/app/service.py", "retry")
                print("Updated only this issue's shared-Jira exception.")
            elif args.action in ("start", "stop"):
                docker(deployment, args.action, "jira-done")
            else:
                print(docker(deployment, "exec", "-T", "jira-done", "python3", "-B", "/app/service.py", args.action))
    except Exception as error:
        print("Jira Done setup failed: " + (str(error) if isinstance(error, RuntimeError) else type(error).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
