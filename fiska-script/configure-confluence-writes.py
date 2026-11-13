#!/usr/bin/env python3
"""Prepare and register a separate Confluence write relay for development-release."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parent
DIRECTORY = ROOT / "mcp-relay/confluence-write"
spec = importlib.util.spec_from_file_location("provision", ROOT / "provision-agents.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def prepare():
    source = json.loads((ROOT / "mcp-relay/config.json").read_text())
    matches = [row for row in source.get("rest", []) if row["name"] == "confluence"]
    if len(matches) != 1 or matches[0]["auth"]["type"] != "basic":
        raise RuntimeError("Expected an existing Confluence integration with basic authentication.")
    service = matches[0]
    secret_values = {}
    for field in ("username", "password"):
        ref = service["auth"][field]
        path = Path(ref.get("file", ""))
        if str(path.parent) != "/run/relay-secrets":
            raise RuntimeError("Expected local relay secret files for Confluence authentication.")
        secret_values[field] = (ROOT / "mcp-relay/.secrets" / path.name).read_text()
        service["auth"][field] = {"file": f"/run/relay-secrets/{field}"}
    service["name"] = "confluence_write"
    service["allowWrites"] = True
    service["description"] = (
        "Read and write Confluence for delivery documentation. Read spaces/pages via /wiki/api/v2; "
        "create with POST /wiki/api/v2/pages and update with PUT /wiki/api/v2/pages/PAGE_ID. "
        "Read the current page and version first, increment the version on updates, and reconcile "
        "conflicts without overwriting unrelated changes. Search via GET /wiki/rest/api/search "
        "with query.cql. Paths and query arguments are separate. Responses are wrapped in data. "
        "Follow pagination and verify writes by reading back.")
    config = {"http": {"host": "0.0.0.0", "port": 8080, "endpoints": [
        {"name": "confluence_write", "path": "/mcp/confluence-write",
         "token": {"file": "/run/relay-secrets/token"}, "allowTools": ["confluence_write__request"]}]},
        "dataDir": "/data", "rest": [service], "upstreams": [], "commands": []}
    path = DIRECTORY / "config.json"
    if path.exists() and json.loads(path.read_text()) != config:
        raise RuntimeError("Dedicated relay configuration differs; reconcile it before changing a running service.")
    for directory in (DIRECTORY, DIRECTORY / ".secrets", DIRECTORY / "data"):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, value in secret_values.items():
        secret_path = DIRECTORY / ".secrets" / name
        if not secret_path.exists() or secret_path.read_text() != value:
            secret_path.write_text(value)
            secret_path.chmod(0o600)
    token_path = DIRECTORY / ".secrets/token"
    if not token_path.exists():
        token_path.write_text(secrets.token_hex(32) + "\n")
        token_path.chmod(0o600)
    if not path.exists():
        provision.write_state(path, config)
    print("Prepared dedicated Confluence configuration; shared relay configuration was not changed.")


def register():
    cli = provision.bridge
    agents = [a for a in cli("agent", "list", "--output", "json") if a["name"] == "development-release"]
    if len(agents) != 1:
        raise RuntimeError("Expected one development-release agent.")
    agent = agents[0]
    state_path = DIRECTORY / "registration.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get("workspace_id", agent["workspace_id"]) != agent["workspace_id"]:
        raise RuntimeError("Registration belongs to another workspace.")
    name = "factory-confluence-write"
    servers = cli("workspace", "mcp", "list", agent["workspace_id"], "--output", "json")
    existing = [row for row in servers if row["name"] == name]
    if len(existing) > 1 or (existing and existing[0]["id"] != state.get("server_id")):
        raise RuntimeError("An untracked Confluence write registration exists; reconcile it first.")
    if state.get("server_id") and not existing:
        raise RuntimeError("Tracked Confluence write registration is missing.")
    if not existing:
        token = (DIRECTORY / ".secrets/token").read_text().strip()
        config = {"type": "http", "url": "http://confluence-write:8080/mcp/confluence-write",
                  "headers": {"Authorization": "Bearer " + token}}
        cli("workspace", "mcp", "add", name, agent["workspace_id"], "--server-config-stdin",
            "--output", "json", data=json.dumps(config))
        servers = cli("workspace", "mcp", "list", agent["workspace_id"], "--output", "json")
        existing = [row for row in servers if row["name"] == name]
        if len(existing) != 1:
            raise RuntimeError("Could not verify the Confluence write registration.")
        state = {"workspace_id": agent["workspace_id"], "server_id": existing[0]["id"]}
        provision.write_state(state_path, state)
    cli("agent", "mcp", "add", agent["id"], state["server_id"], "--output", "json")
    print("Assigned dedicated Confluence writes to development-release only.")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        {"prepare": prepare, "register": register}[sys.argv[1]]()
    except (RuntimeError, ValueError, KeyError, OSError, IndexError) as error:
        print(f"Confluence setup failed: {error}", file=sys.stderr)
        sys.exit(1)
