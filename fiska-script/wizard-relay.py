#!/usr/bin/env python3
"""Configure optional scoped MCP services and register them through Multica's CLI."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("wizard", ROOT / "wizard-config.py")
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)
provision = wizard.provision_module()


def prompt(text, default=""):
    while True:
        value = input(f"{text}" + (f" [{default}]" if default else "") + ": ").strip() or default
        if value:
            return value


def yesno(text, default="no"):
    while True:
        answer = prompt(text + " (yes/no)", default).lower()
        if answer in ("yes", "y", "no", "n"):
            return answer in ("yes", "y")


def prepare():
    root = ROOT / "mcp-relay"
    if (root / "config.json").exists():
        print("Preserving existing relay configuration, credentials and data.")
        return
    config = {"http": {"host": "0.0.0.0", "port": 8080,
                       "token": {"file": "/run/relay-secrets/relay-token"}, "endpoints": []},
              "dataDir": "/data", "upstreams": [], "commands": []}
    secret_values = {"relay-token": secrets.token_hex(32)}
    if yesno("Configure Jira and Confluence now?"):
        while True:
            site = prompt("Jira HTTPS origin, for example https://your-team.atlassian.net").rstrip("/")
            url = urlparse(site)
            if url.scheme == "https" and url.hostname and not any([url.username, url.password, url.path, url.query, url.fragment]):
                break
            print("Enter an HTTPS origin without a path, query or credentials.")
        email = prompt("Jira account email")
        while True:
            reference = prompt("Jira API token 1Password reference (op://vault/item/field)")
            if re.fullmatch(r"op://[^/]+/[^/]+/.+", reference):
                break
        secret_values["jira-email"] = email
        secret_values["jira-token"] = provision.resolve_secret(reference, {})
        writes = yesno("Allow Jira write operations with this account?")
        config["jira"] = {"site": site, "email": {"file": "/run/relay-secrets/jira-email"},
                          "token": {"file": "/run/relay-secrets/jira-token"}, "allowWrites": writes}
        config["rest"] = [{"name": "confluence", "description": "Confluence API",
                           "baseUrl": site, "pathPrefixes": ["/wiki"], "allowWrites": False,
                           "auth": {"type": "basic", "username": config["jira"]["email"],
                                    "password": config["jira"]["token"]}}]
        for name in ["jira", "confluence"]:
            filename = f"endpoint-{name}-token"
            secret_values[filename] = secrets.token_hex(32)
            config["http"]["endpoints"].append({"name": name, "path": f"/mcp/{name}",
                "token": {"file": f"/run/relay-secrets/{filename}"}, "allowTools": [f"{name}__request"]})
    for directory in [root / ".secrets", root / "data"]:
        directory.mkdir(mode=0o700, exist_ok=True)
    for name, value in secret_values.items():
        path = root / ".secrets" / name
        if path.exists():
            raise RuntimeError("Partial relay setup exists. Inspect .secrets before retrying; nothing was overwritten.")
    for name, value in secret_values.items():
        wizard.atomic_write(root / ".secrets" / name, value + "\n")
    wizard.atomic_write(root / "config.json", json.dumps(config, indent=2) + "\n")
    print("Relay configuration saved privately. Slack, Gmail and other services can be configured later; they are not enabled by this wizard.")


def register():
    config = json.loads((ROOT / "mcp-relay/config.json").read_text())
    endpoints = config["http"].get("endpoints", [])
    if not endpoints:
        print("Relay is running without provider endpoints; no MCP tools were assigned to agents.")
        return
    if not yesno("Register the relay endpoints in this Multica workspace?", "yes"):
        return
    agents = json.loads((ROOT / "agents.json").read_text())
    team = json.loads((ROOT / "agent-state.json").read_text())
    state_path = ROOT / "wizard-relay-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"workspace_id": team["workspace_id"], "servers": {}}
    if state["workspace_id"] != team["workspace_id"]:
        raise RuntimeError("Relay registration state belongs to another workspace.")
    while True:
        access = prompt("Give relay access to coordinator, all team agents, or none", "coordinator")
        if access in ("coordinator", "all", "none"):
            break
    selected = [] if access == "none" else [agents["main"]["name"]]
    if access == "all":
        selected += [worker["name"] for worker in agents["workers"]]
    existing = provision.bridge("workspace", "mcp", "list", team["workspace_id"], "--output", "json")
    for endpoint in endpoints:
        name = "relay-" + endpoint["name"].replace("_", "-")
        server_id = state["servers"].get(name)
        if any(row["name"] == name and row["id"] != server_id for row in existing):
            raise RuntimeError(f"An untracked MCP registration already uses {name}. Reconcile it before retrying.")
        if server_id and not any(row["id"] == server_id for row in existing):
            raise RuntimeError(f"Tracked MCP registration {name} is missing. Reconcile it before retrying.")
        filename = endpoint["token"].get("file", "")
        if not re.fullmatch(r"/run/relay-secrets/[a-z][a-z0-9-]{0,79}", filename):
            raise RuntimeError("Wizard registration requires local endpoint token files.")
        token = (ROOT / "mcp-relay/.secrets" / Path(filename).name).read_text().strip()
        spec = {"type": "http", "url": "http://mcp-relay:8080" + endpoint["path"],
                "headers": {"Authorization": "Bearer " + token}}
        command = ["update", server_id, team["workspace_id"], "--name", name] if server_id else ["add", name, team["workspace_id"]]
        provision.bridge("workspace", "mcp", *command, "--server-config-stdin", "--output", "json", data=json.dumps(spec))
        rows = provision.bridge("workspace", "mcp", "list", team["workspace_id"], "--output", "json")
        server_id = next(row["id"] for row in rows if row["name"] == name)
        state["servers"][name] = server_id
        wizard.atomic_write(state_path, json.dumps(state, indent=2) + "\n")
        for agent in selected:
            provision.bridge("agent", "mcp", "add", team["agents"][agent]["id"], server_id, "--output", "json")
    print(f"Registered {len(endpoints)} MCP endpoints; added access for {len(selected)} agents. Existing assignments were preserved.")


if __name__ == "__main__":
    os.umask(0o077)
    if len(sys.argv) != 2 or sys.argv[1] not in ("prepare", "register"):
        raise SystemExit("Usage: wizard-relay.py {prepare|register}")
    with (ROOT / ".operation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            (prepare if sys.argv[1] == "prepare" else register)()
        except (RuntimeError, EOFError, KeyboardInterrupt) as error:
            raise SystemExit(str(error) or "Setup interrupted; rerun ./setup.sh --resume.") from None
