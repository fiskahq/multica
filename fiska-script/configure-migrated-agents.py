#!/usr/bin/env python3
"""Configure migrated Kiro roles to use workspace-wide credential rotation."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def api_mode_args(args):
    kept = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in ["--effort", "--agent-engine"]:
            skip = True
        elif not arg.startswith(("--effort=", "--agent-engine=")):
            kept.append(arg)
    return kept + ["--agent-engine", "v3"]


def main():
    os.umask(0o077)
    lock = (ROOT / ".operation.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    spec = importlib.util.spec_from_file_location("provision", ROOT / "provision-agents.py")
    provision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provision)
    state = json.loads((ROOT / "agent-state.json").read_text())
    migration = json.loads((ROOT / "migration-state.json").read_text())
    config = json.loads((ROOT / "agents.json").read_text())
    if provision.bridge("health").get("active_task_count", 0):
        raise RuntimeError("Wait for active tasks before changing migrated agent credentials")
    workers = [state["agents"][worker["name"]]["id"] for worker in config["workers"]]
    runtimes = provision.bridge("runtime", "list", "--output", "json")
    providers = {r["id"]: r["provider"] for r in runtimes}
    agents = provision.bridge("agent", "list", "--include-archived", "--output", "json")
    managed = {a["id"] for a in state["agents"].values()}
    if "original_agent_ids" not in migration:
        migration["original_agent_ids"] = [a["id"] for a in agents if a["id"] not in managed]
    originals = set(migration["original_agent_ids"])
    legacy = sorted([a for a in agents if a["id"] in originals and not a.get("archived_at") and providers.get(a.get("runtime_id")) == "kiro"], key=lambda a: a["id"])
    subprocess.run([sys.executable, str(ROOT / "configure-kiro-pool.py")], check=True)
    for agent in legacy:
        current = provision.bridge("agent", "env", "get", agent["id"], "--output", "json")["custom_env"] or {}
        if "KIRO_API_KEY" in current:
            del current["KIRO_API_KEY"]
            provision.bridge("agent", "env", "set", agent["id"], "--custom-env-stdin", "--output", "json", data=json.dumps(current))
    for agent in agents:
        if agent.get("archived_at") or agent["id"] not in originals | set(workers) or providers.get(agent.get("runtime_id")) != "kiro":
            continue
        args = api_mode_args(agent.get("custom_args") or [])
        provision.bridge("agent", "update", agent["id"], "--custom-args", json.dumps(args), "--thinking-level", "", "--output", "json")
    migration.pop("kiro_credential_bindings", None)
    migration["kiro_credential_mode"] = "workspace_round_robin"
    provision.write_state(ROOT / "migration-state.json", migration)
    print(f"Configured {len(legacy)} migrated Kiro roles with per-task credential rotation. No 1Password reads.")
    print("Existing squads retain their roles; every Kiro task uses workspace-wide key round-robin.")


if __name__ == "__main__":
    main()
