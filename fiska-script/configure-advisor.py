#!/usr/bin/env python3
"""Install a Kiro implementation advisor and native consultation hooks without a restart."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("architect", ROOT / "configure-architect.py")
architect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(architect)
command, unique, provision = architect.command, architect.unique, architect.provision
NAME = "development-advisor"
MARKER = "## Implementation advisor consultation"


def tracked(state, key, rows, name):
    ident = state.get(key)
    row = unique(rows, name)
    if ident and (not row or row["id"] != ident):
        raise RuntimeError(f"Tracked {name} changed; reconcile advisor-state.json before retrying.")
    if row and row["id"] != ident:
        raise RuntimeError(f"Untracked {name} exists; refusing to overwrite it.")
    return ident


def append_policy(before, policy):
    if policy in before:
        return before
    if MARKER in before:
        raise RuntimeError("A different advisor policy exists; reconcile it before retrying.")
    return before.rstrip() + "\n\n" + policy


def configure():
    state_path = ROOT / "advisor-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    health = command(health=True)
    runtimes = [r for r in command("runtime", "list") if r.get("daemon_id") == health["daemon_id"]
                and r.get("provider") == "kiro" and not r.get("profile_id") and r.get("status") == "online"]
    if len(runtimes) != 1:
        raise RuntimeError("Expected one online built-in Kiro runtime on this Docker daemon.")
    runtime = runtimes[0]
    if state.get("workspace_id", runtime["workspace_id"]) != runtime["workspace_id"]:
        raise RuntimeError("Advisor state belongs to a different workspace.")
    state["workspace_id"] = runtime["workspace_id"]
    agents, skills = command("agent", "list"), command("skill", "list")
    agent_id = tracked(state, "agent_id", agents, NAME)
    skill_id = tracked(state, "skill_id", skills, "implementation-planning")
    servers = command("workspace", "mcp", "list", state["workspace_id"])
    selected_servers = [unique(servers, f"factory-{service}") or unique(servers, f"relay-{service}")
                        for service in ("jira", "confluence")]
    supplements = [unique(skills, name) for name in
                   ("research-with-sources", "conventions", "architecture-designer", "domain-modeling")]
    content = (ROOT / "skills/implementation-planning/SKILL.md").read_text()
    instructions = (ROOT / "agents/development-advisor/instructions.md").read_text()
    skill = command("skill", *(["update", skill_id] if skill_id else ["create"]),
                    "--name", "implementation-planning", "--description",
                    "Repository-grounded coding plans: file and symbol changes, contracts, sequencing and verification without writing code.",
                    "--content-stdin", data=content)
    state["skill_id"] = skill["id"]
    provision.write_state(state_path, state)
    limit = provision.agent_concurrency(NAME)
    agent = command("agent", *(["update", agent_id] if agent_id else ["create"]),
                    "--name", NAME, "--runtime-id", runtime["id"], "--model", "claude-opus-5.5",
                    "--thinking-level", "high", "--custom-args", '["--agent-engine","v3"]',
                    "--max-concurrent-tasks", str(limit), "--permission-mode", "public_to",
                    "--public-to-workspace", "--description",
                    "Senior software engineer advising builders on low-level implementation design, architecture, change order and tests. Plans only; does not write code.",
                    "--instructions", instructions, "--conversation-starters", json.dumps([
                        {"label": "Plan implementation", "prompt": "Help plan the coding work for an existing ticket. Ask for the issue and repository, inspect the code, and return a concrete change plan without editing files."},
                        {"label": "Unblock a builder", "prompt": "Help resolve an implementation design question. Ask for the candidate commit and question, then provide evidence-backed technical direction."}]))
    agent_id = state["agent_id"] = agent["id"]
    provision.write_state(state_path, state)
    wanted_skills = [skill["id"], *[r["id"] for r in supplements if r]]
    command("agent", "skills", "add", agent_id, "--skill-ids", ",".join(wanted_skills))
    for server in selected_servers:
        if server:
            command("agent", "mcp", "add", agent_id, server["id"])

    policy = f"""{MARKER}

Consult [@{NAME}](mention://agent/{agent_id}) for nontrivial coding plans or
uncertain implementation decisions. It is a senior engineering advisor: file/symbol change maps,
interfaces, data invariants, architecture, sequencing and proposed verification; it does not code.
Include the accepted scope, repository, base/candidate SHA, relevant evidence and a bounded question.
Use a native issue-comment mention (with --parent on a comment-triggered run), then end the run to
free the slot. Its reply resumes the requester. Reuse an existing plan; do not redispatch on a mere
acknowledgement. A small clear fix needs no consultation. Reconsult if new evidence invalidates the plan.
This helper consultation is permitted within Development and does not transfer ownership, advance
status, invoke the next workflow handler, or start another pipeline. A plan alone is not Step done.
Builder owns implementation and validation; existing review, QA and delivery handoffs still apply."""
    records = []
    for name in ("development-builder", "development-lead", "development-planner", "Architect"):
        row = unique(agents, name)
        if row:
            row = command("agent", "get", row["id"])
            records.append(("agent", row, append_policy(row.get("instructions") or "", policy)))
    squad = unique(command("squad", "list"), "Software Development")
    if squad:
        squad = command("squad", "get", squad["id"])
        records.append(("squad", squad, append_policy(squad.get("instructions") or "", policy)))
    workflow = unique(command("workflow", "list"), "Software Factory")
    step = next((s for s in workflow["steps"] if s["status_key"] == "development"), None) if workflow else None
    workflow_text = append_policy(step.get("instructions") or "", policy).strip() if step else None
    if workflow_text and len(workflow_text) > 8000:
        raise RuntimeError("Development instructions would exceed 8000 characters.")
    changes = [{"kind": kind, "id": row["id"], "before": row.get("instructions") or "", "after": after}
               for kind, row, after in records if (row.get("instructions") or "") != after]
    if step and step["instructions"] != workflow_text:
        changes.append({"kind": "workflow_step", "id": workflow["id"], "status": "development",
                        "before": step["instructions"], "after": workflow_text})
    if changes:
        backups = ROOT / "backups"
        backups.mkdir(exist_ok=True)
        provision.write_state(Path(tempfile.mkdtemp(prefix="advisor-policy-", dir=backups)) / "changes.json", changes)
    for kind, row, after in records:
        if row.get("instructions") == after:
            continue
        current = command(kind, "get", row["id"])
        if current.get("instructions") != row.get("instructions"):
            raise RuntimeError(f"Concurrent {kind} edit detected; reconcile before retrying.")
        command(kind, "update", row["id"], "--instructions", after)
        if command(kind, "get", row["id"]).get("instructions") != after:
            raise RuntimeError(f"Saved {kind} consultation instructions did not match.")
    if step and step["instructions"] != workflow_text:
        current = command("workflow", "get", workflow["id"])
        if current["steps"] != workflow["steps"]:
            raise RuntimeError("Workflow changed during setup; reconcile before retrying.")
        command("workflow", "handoff", "set", workflow["id"], "development", "--instructions", workflow_text)
        saved_steps = command("workflow", "get", workflow["id"])["steps"]
        expected_steps = [dict(s, instructions=workflow_text) if s["status_key"] == "development" else s
                          for s in workflow["steps"]]
        if saved_steps != expected_steps:
            raise RuntimeError("Workflow readback differs from the requested instructions-only change.")
    if squad:
        members = command("squad", "member", "list", squad["id"])
        if not any(m["member_id"] == agent_id and m["member_type"] == "agent" for m in members):
            command("squad", "member", "add", squad["id"], "--member-id", agent_id, "--type", "agent",
                    "--role", "Senior engineering advisor: implementation planning and technical direction, no code changes")
        members = command("squad", "member", "list", squad["id"])
        if sum(m["member_id"] == agent_id and m["member_type"] == "agent" for m in members) != 1:
            raise RuntimeError("Expected exactly one advisor squad membership.")
    saved = command("agent", "get", agent_id)
    expected = {"model": "claude-opus-5.5", "thinking_level": "high", "runtime_id": runtime["id"],
                "instructions": instructions, "max_concurrent_tasks": limit, "visibility": "workspace",
                "custom_args": ["--agent-engine", "v3"]}
    if any(saved.get(k) != v for k, v in expected.items()):
        raise RuntimeError("Saved advisor settings did not match.")
    attached_skills = command("agent", "skills", "list", agent_id)
    attached_servers = command("agent", "mcp", "list", agent_id)
    if not set(wanted_skills) <= {r["id"] for r in attached_skills}:
        raise RuntimeError("A required skill attachment was not saved.")
    if not {r["id"] for r in selected_servers if r} <= {r["id"] for r in attached_servers}:
        raise RuntimeError("A context MCP attachment was not saved.")
    print(json.dumps({"agent_id": agent_id, "name": NAME, "model": saved["model"], "thinking_level": "high",
                      "max_concurrent_tasks": limit, "skills": [r["name"] for r in attached_skills],
                      "mcp_servers": [r["name"] for r in attached_servers],
                      "squad": squad["name"] if squad else None, "development_workflow": bool(step)}, indent=2))
    print("Verified configuration and consultation hooks. No restart or model request was made.")


if __name__ == "__main__":
    try:
        configure()
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(f"Advisor setup failed: {error}", file=sys.stderr)
        sys.exit(1)
