#!/usr/bin/env python3
"""Configure a separate Jira release tool for the selected collaboration roles."""
import argparse
import hashlib
import importlib.util
import json
import os
import secrets
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("provision", ROOT / "provision-agents.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def prepare(deployment):
    path = deployment / "mcp-relay/config.json"
    before = json.loads(path.read_text())
    after = json.loads(json.dumps(before))
    example = json.loads((ROOT / "mcp-relay/examples/jira-release.json").read_text())
    template = example["integration"]
    writers = [s for s in after.get("rest", []) if s["name"] == "jira_triage"]
    if len(writers) > 1:
        raise RuntimeError("Multiple Jira write integrations found.")
    if writers:
        template["auth"] = writers[0]["auth"]
        template["baseUrl"] = writers[0]["baseUrl"]
    elif after.get("jira"):
        jira = after["jira"]
        template["baseUrl"] = jira["site"]
        template["auth"] = {"type": "basic", "username": jira["email"], "password": jira["token"]}
    else:
        raise RuntimeError("Configure Jira in the relay wizard first.")
    if not (deployment / "mcp-relay/confluence-write/config.json").exists():
        raise RuntimeError("Run setup-confluence-writes.sh first to prepare the scoped Confluence writer.")
    releases = [s for s in after.get("rest", []) if s["name"] == "jira_release"]
    if releases and (len(releases) != 1 or releases[0] != template):
        raise RuntimeError("Existing Jira release integration differs; reconcile before applying.")
    if not releases:
        after.setdefault("rest", []).append(template)
    endpoints = after["http"].setdefault("endpoints", [])
    existing = [e for e in endpoints if e["name"] == "jira_release" or e["path"] == "/mcp/jira-release"]
    if existing and existing != [example["endpoint"]]:
        raise RuntimeError("Existing Jira release endpoint differs; reconcile before applying.")
    if not existing:
        endpoints.append(example["endpoint"])
    token_path = deployment / "mcp-relay/.secrets/endpoint-jira-release-token"
    if not token_path.exists():
        with token_path.open("x") as token_file:
            token_file.write(secrets.token_hex(32) + "\n")
        token_path.chmod(0o600)
    state_path = deployment / "collaboration-tools-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if before != after:
        backup_dir = deployment / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="collaboration-tools-", dir=backup_dir)) / "config.json"
        provision.write_state(backup, before)
        # Persist pending before changing the file, so interrupted setup still reloads on retry.
        state["pending_reload"] = True
        provision.write_state(state_path, state)
        provision.write_state(path, after)
    state["config_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    provision.write_state(state_path, state)
    print("reload" if state.get("pending_reload") else "unchanged")


def register(deployment):
    def load(name):
        spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        return result
    architect, bridge = load("configure-architect"), load("relay-stdio")
    architect.ROOT = deployment
    command = architect.command
    workspaces = {a["workspace_id"] for a in command("agent", "list")}
    if len(workspaces) != 1:
        raise RuntimeError("Select one workspace with configured agents.")
    workspace = workspaces.pop()
    path = deployment / "collaboration-tools-state.json"
    state = json.loads(path.read_text())
    server = architect.unique(command("workspace", "mcp", "list", workspace), "factory-jira-release")
    if state.get("workspace_id", workspace) != workspace:
        raise RuntimeError("Release registration belongs to another workspace.")
    if server and server["id"] != state.get("server_id"):
        raise RuntimeError("An untracked Jira release registration exists; reconcile it first.")
    if state.get("server_id") and not server:
        raise RuntimeError("Tracked Jira release registration is missing; reconcile it first.")
    token = (deployment / "mcp-relay/.secrets/endpoint-jira-release-token").read_text().strip()
    config = bridge.server_config("http://mcp-relay:8080/mcp/jira-release", {"Authorization": "Bearer " + token})
    args = ["update", server["id"], workspace] if server else ["add", "factory-jira-release", workspace]
    command("workspace", "mcp", *args, "--server-config-stdin", data=json.dumps(config))
    server = architect.unique(command("workspace", "mcp", "list", workspace), "factory-jira-release")
    state.update(workspace_id=workspace, server_id=server["id"])
    provision.write_state(path, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "reloaded", "register"])
    parser.add_argument("--deployment-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action == "prepare":
            prepare(args.deployment_dir)
        elif args.action == "register":
            register(args.deployment_dir)
        else:
            path = args.deployment_dir / "collaboration-tools-state.json"
            state = json.loads(path.read_text())
            if hashlib.sha256((args.deployment_dir / "mcp-relay/config.json").read_bytes()).hexdigest() != state["config_sha256"]:
                raise RuntimeError("Relay config changed during reload; rerun setup.")
            state["pending_reload"] = False
            provision.write_state(path, state)
    except (OSError, KeyError, ValueError, RuntimeError) as error:
        print(f"Collaboration tool setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
