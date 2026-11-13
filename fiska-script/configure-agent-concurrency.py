#!/usr/bin/env python3
"""Apply role-specific concurrency limits through native Multica updates."""
import fcntl
import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("provision", ROOT / "provision-agents.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def main():
    with (ROOT / ".operation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        agents = provision.bridge("agent", "list", "--output", "json")
        agents = [agent for agent in agents if not agent.get("archived_at")]
        if not agents:
            raise RuntimeError("No active agents were found in the current workspace.")
        limits = {agent["id"]: provision.agent_concurrency(agent["name"]) for agent in agents}
        backups = ROOT / "backups"
        backups.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="agent-concurrency-", dir=backups)) / "before.json"
        provision.write_state(backup, [{key: agent.get(key) for key in
                                       ("id", "name", "workspace_id", "max_concurrent_tasks")}
                                      for agent in agents])
        changed = 0
        for agent in agents:
            limit = limits[agent["id"]]
            if agent.get("max_concurrent_tasks") == limit:
                continue
            provision.bridge("agent", "update", agent["id"], "--max-concurrent-tasks", str(limit), "--output", "json")
            changed += 1
        saved = provision.bridge("agent", "list", "--output", "json")
        saved = [agent for agent in saved if not agent.get("archived_at")]
        if {a["id"] for a in agents} != {a["id"] for a in saved}:
            raise RuntimeError("The active agent list changed during the update; rerun to reconcile.")
        if any(agent.get("max_concurrent_tasks") != limits[agent["id"]] for agent in saved):
            raise RuntimeError("An agent limit did not match on readback; rerun to reconcile.")
        print(f"Verified role limits for {len(saved)} active agents ({changed} updated).")
        for limit in sorted(set(limits.values())):
            print(f"{limit} concurrent: " + ", ".join(a["name"] for a in saved if limits[a["id"]] == limit))
        print("The runtime-wide concurrency limit is unchanged. No services restarted.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(f"Agent concurrency update failed: {error}", file=sys.stderr)
        sys.exit(1)
