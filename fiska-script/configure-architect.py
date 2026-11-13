#!/usr/bin/env python3
"""Install the Architect role through native Multica commands, without restarting the runtime."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("provision", ROOT / "provision-agents.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def command(*args, data=None, health=False):
    script = 'source "$1/common.sh"\nshift\ncompose exec -T -w /home/node runtime "$@"'
    cli = ["curl", "--fail", "--silent", "http://127.0.0.1:19514/health"] if health else ["multica", *args, "--output", "json"]
    result = subprocess.run(["bash", "-c", script, "bash", str(ROOT), *cli],
                            input=data, text=True, capture_output=True)
    if result.returncode:
        # Provider and MCP responses may contain credentials. Do not echo them.
        raise RuntimeError(f"Multica {' '.join(args[:2]) or 'health'} failed; check runtime health and workspace access.")
    return json.loads(result.stdout)


def unique(rows, name):
    matches = [row for row in rows if row["name"] == name]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple resources named {name}; resolve the ambiguity before retrying.")
    return matches[0] if matches else None


def managed_id(state, key, rows, name):
    ident = state.get(key)
    match = unique(rows, name)
    if ident and not any(row["id"] == ident and row["name"] == name for row in rows):
        raise RuntimeError(f"Tracked {name} was removed or renamed; reconcile architect-state.json first.")
    if match and match["id"] != ident:
        raise RuntimeError(f"Untracked {name} already exists; nothing will overwrite it.")
    return ident


def configure(mcp_names):
    state_path = ROOT / "architect-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    health = command(health=True)
    runtimes = [row for row in command("runtime", "list")
                if row.get("daemon_id") == health["daemon_id"] and row.get("provider") == "claude"
                and not row.get("profile_id") and row.get("status") == "online"]
    if len(runtimes) != 1:
        raise RuntimeError("Expected one online built-in Claude runtime on this Docker daemon.")
    runtime = runtimes[0]
    if state.get("workspace_id", runtime["workspace_id"]) != runtime["workspace_id"]:
        raise RuntimeError("Architect state belongs to another workspace; use a separate setup directory.")
    state["workspace_id"] = runtime["workspace_id"]
    agents = command("agent", "list")
    skills = command("skill", "list")
    servers = command("workspace", "mcp", "list", state["workspace_id"])
    agent_id = managed_id(state, "agent_id", agents, "Architect")
    skill_id = managed_id(state, "skill_id", skills, "ticket-creation")
    supplements = [unique(skills, name) for name in
                   ("research-with-sources", "architecture-designer", "conventions")]
    if mcp_names is None:
        selected_servers = []
        for service in ("jira", "confluence"):
            server = unique(servers, f"factory-{service}") or unique(servers, f"relay-{service}")
            if server:
                selected_servers.append(server)
            else:
                print(f"No scoped {service} MCP registration found; continuing with native workspace context.")
    else:
        selected_servers = [unique(servers, name) for name in dict.fromkeys(mcp_names)]
        if any(server is None for server in selected_servers):
            raise RuntimeError("A requested MCP server name was not found; no configuration was changed.")
    instructions = (ROOT / "agents/architect/instructions.md").read_text()
    content = (ROOT / "skills/ticket-creation/SKILL.md").read_text()
    skill_args = ["update", skill_id] if skill_id else ["create"]
    skill = command("skill", *skill_args, "--name", "ticket-creation", "--description",
                    "Research and refine tickets with duplicate checks, specialist context, evidence and testable acceptance criteria.",
                    "--content-stdin", data=content)
    state["skill_id"] = skill["id"]
    provision.write_state(state_path, state)
    starters = [
        {"label": "Create a ticket", "prompt": "Help me create an implementation-ready ticket. Ask what change I need, then gather relevant context and consult specialists when useful."},
        {"label": "Refine an existing ticket", "prompt": "Help me refine an existing ticket. Ask for its link, then check scope, related work and acceptance criteria with the relevant specialists."},
        {"label": "Break down a feature", "prompt": "Help me break a feature into scoped tickets with clear dependencies. Ask for the feature goal and project, then gather context before proposing the breakdown."},
    ]
    agent_args = ["update", agent_id] if agent_id else ["create"]
    limit = provision.agent_concurrency("Architect")
    agent = command("agent", *agent_args, "--name", "Architect", "--runtime-id", runtime["id"],
                    "--model", "claude-opus-5-5", "--thinking-level", "high",
                    "--max-concurrent-tasks", str(limit), "--permission-mode", "public_to", "--public-to-workspace",
                    "--description", "Creates evidence-backed tickets by consulting specialists, checking related work and defining scope and acceptance criteria.",
                    "--instructions", instructions, "--conversation-starters", json.dumps(starters))
    state["agent_id"] = agent["id"]
    provision.write_state(state_path, state)
    wanted_skills = [state["skill_id"], *[row["id"] for row in supplements if row]]
    command("agent", "skills", "add", agent["id"], "--skill-ids", ",".join(wanted_skills))
    for server in selected_servers:
        command("agent", "mcp", "add", agent["id"], server["id"])
    saved = command("agent", "get", agent["id"])
    attached_skills = command("agent", "skills", "list", agent["id"])
    attached_servers = command("agent", "mcp", "list", agent["id"])
    expected = {"model": "claude-opus-5-5", "thinking_level": "high", "runtime_id": runtime["id"],
                "max_concurrent_tasks": limit, "visibility": "workspace", "instructions": instructions}
    if any(saved.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Saved Architect settings did not match the requested configuration.")
    if not set(wanted_skills) <= {row["id"] for row in attached_skills}:
        raise RuntimeError("A required skill attachment was not saved.")
    if not {row["id"] for row in selected_servers} <= {row["id"] for row in attached_servers}:
        raise RuntimeError("A requested MCP attachment was not saved.")
    print(json.dumps({"agent_id": agent["id"], "name": saved["name"], "model": saved["model"],
                      "thinking_level": saved["thinking_level"],
                      "skills": [row["name"] for row in attached_skills],
                      "mcp_servers": [row["name"] for row in attached_servers]}, indent=2))
    print("Verified saved configuration. No runtime restart or model request was made.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-name", action="append", help="Exact existing context MCP name; repeat for multiple servers.")
    args = parser.parse_args()
    try:
        configure(args.mcp_name)
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(f"Architect setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
