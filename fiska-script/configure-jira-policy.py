#!/usr/bin/env python3
"""Apply the Jira prerequisite through native agent, squad, skill and workflow instructions."""
import argparse
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("architect", ROOT / "configure-architect.py")
architect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(architect)
provision = architect.provision

# Keep this short: some existing handoffs are close to Multica's 8000-character limit.
CHECK = "Jira gate: verify a matching Jira ticket or explicit user opt-out before development."
TERMINAL = {"done", "cancelled", "canceled"}
SKILLS = {"ticket-creation", "delivery-handoff"}


def add_checkpoint(text):
    text = text or ""
    if CHECK not in text:
        text = CHECK + "\n\n" + text
    text = text.strip()
    if len(text) > 8000:
        raise RuntimeError("Jira checkpoint would exceed the 8000-character workflow limit.")
    return text


def plan(command):
    changes = []
    workflows = {}
    for kind in ("agent", "squad", "skill", "workflow"):
        for summary in command(kind, "list"):
            # This run-only reviewer cannot create tickets or perform development. Its output
            # is captured by the learner service; coding agents still check intake on those tickets.
            if kind == "agent" and summary["name"] == "background-learner":
                continue
            if kind == "skill" and summary["name"] not in SKILLS:
                continue
            row = command(kind, "get", summary["id"], *(["--with-content"] if kind == "skill" else []))
            if kind == "agent" and row["name"] == "Architect":
                servers = command("workspace", "mcp", "list", row["workspace_id"])
                writer = architect.unique(servers, "factory-jira-triage")
                if writer:
                    before_ids = sorted(s["id"] for s in command("agent", "mcp", "list", row["id"]))
                    if writer["id"] not in before_ids:
                        changes.append(dict(kind="agent_mcp", id=row["id"], name=row["name"],
                                            field="mcp_ids", server_id=writer["id"], before=before_ids,
                                            after=sorted([*before_ids, writer["id"]])))
            if kind == "workflow":
                expected = copy.deepcopy(row["steps"])
                for step in expected:
                    # Multica discards instructions for handler=none during normalization.
                    if step["status_key"] in TERMINAL or step.get("handler", {}).get("type", "none") == "none":
                        continue
                    before = step.get("instructions") or ""
                    after = add_checkpoint(before)
                    step["instructions"] = after
                    if before != after:
                        changes.append(dict(kind="workflow_step", id=row["id"], name=row["name"],
                                            status=step["status_key"], field="instructions",
                                            before=before, after=after))
                workflows[row["id"]] = expected
            else:
                field = "content" if kind == "skill" else "instructions"
                before = row.get(field) or ""
                after = provision.jira_instructions(before)
                if before != after:
                    changes.append(dict(kind=kind, id=row["id"], name=row["name"], field=field,
                                        before=before, after=after))
    return changes, workflows


def read_change(command, change):
    kind, ident = change["kind"], change["id"]
    if kind == "agent_mcp":
        return {"mcp_ids": sorted(s["id"] for s in command("agent", "mcp", "list", ident))}
    if kind == "workflow_step":
        return next(s for s in command("workflow", "get", ident)["steps"]
                    if s["status_key"] == change["status"])
    return command(kind, "get", ident, *(["--with-content"] if kind == "skill" else []))


def configure(command, deployment, dry_run=False):
    changes, workflows = plan(command)
    counts = {kind: sum(r["kind"] == kind for r in changes)
              for kind in ("agent", "agent_mcp", "squad", "skill", "workflow_step")}
    print(json.dumps({"dry_run": dry_run, "changes": counts}))
    if dry_run:
        return
    if changes:
        backups = deployment / "backups"
        backups.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="jira-policy-", dir=backups)) / "changes.json"
        provision.write_state(backup, changes)
    for change in changes:
        kind, ident, field = change["kind"], change["id"], change["field"]
        current = read_change(command, change).get(field)
        if (current if current is not None else "") != change["before"]:
            raise RuntimeError(f"Concurrent edit detected for {kind} {ident}; reconcile before retrying.")
        if kind == "agent_mcp":
            command("agent", "mcp", "add", ident, change["server_id"])
        elif kind == "workflow_step":
            command("workflow", "handoff", "set", ident, change["status"],
                    "--instructions", change["after"])
        elif kind == "skill":
            command("skill", "update", ident, "--content-stdin", data=change["after"])
        else:
            command(kind, "update", ident, "--instructions", change["after"])
        if read_change(command, change).get(field) != change["after"]:
            raise RuntimeError(f"Jira policy readback failed for {kind} {ident}.")
    for ident, expected in workflows.items():
        if command("workflow", "get", ident)["steps"] != expected:
            raise RuntimeError(f"Workflow {ident} differs from the requested instructions-only change.")
    print("Verified Jira prerequisite instructions. No restart or ticket creation was performed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-dir", type=Path, default=ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    deployment = args.deployment_dir.resolve()
    architect.ROOT = deployment
    os.umask(0o077)
    try:
        with (deployment / ".operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            configure(architect.command, deployment, args.dry_run)
    except (RuntimeError, ValueError, KeyError, OSError, StopIteration) as error:
        print(f"Jira policy setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
