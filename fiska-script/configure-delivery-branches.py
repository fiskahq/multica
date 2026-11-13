#!/usr/bin/env python3
"""Use feature/<Jira-or-Multica-key>-<description> for final delivery branches."""
import fcntl
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

COMMON_OLD = "feature/chore/bugfix branch and removes this flow's redundant agent/ branches as specified in"
COMMON_NEW = "feature/<Jira-or-Multica-key>-<description> branch and removes this flow's redundant agent/ refs as specified in"
RELEASE_OLD = """exact reviewed SHA under feature/MS-123-description, chore/MS-123-description or
bugfix/MS-123-description, omitting the Jira key when no Jira ticket is attached. Remove only the"""
RELEASE_NEW = """exact reviewed SHA under feature/<JIRA-KEY>-<description> when a Jira ticket is attached;
otherwise use feature/<MULTICA-KEY>-<description>. Always use feature/ with the uppercase issue
key and lowercase kebab-case description, for example feature/MS-123-transaction-reporting or
feature/FISK-42-transaction-reporting. Use the actual primary linked Jira key, not a related-ticket
mention; absent Jira, use this Multica issue's actual identifier. Remove only the"""
SKILL_OLD = """1. Name the final branch `<type>/<JIRA-KEY>-<description>` when an actual Jira ticket is attached,
   otherwise `<type>/<description>`. Type is `feature` for new capability, `bugfix` for a defect,
   or `chore` for maintenance. Use the uppercase linked key (for example MS-123), and a concise
   lowercase kebab-case description. Do not infer Jira attachment from a related-ticket mention
   or substitute a Multica FISK key. Examples: `feature/MS-123-transaction-reporting`,
   `bugfix/MS-456-merchant-filter`, `chore/local-jwt-keys`. Validate with `git check-ref-format`."""
SKILL_NEW = """1. Name every final delivery branch `feature/<JIRA-KEY>-<description>` when an actual Jira ticket
   is attached. With no Jira ticket, use `feature/<MULTICA-KEY>-<description>` from the originating
   Multica issue's identifier. Always use `feature/`, including fixes and maintenance. Use an
   uppercase issue key and concise lowercase kebab-case description. Examples:
   `feature/MS-123-transaction-reporting` and `feature/FISK-42-transaction-reporting`.
   Prefer the actual primary linked Jira ticket; a related-ticket mention is not an attachment.
   If several linked Jira tickets leave the primary ambiguous, resolve that decision rather than
   choosing an unrelated key. Never invent an ID or omit the ticket component. Keep an existing
   correctly named canonical branch stable on retries. Validate with `git check-ref-format`."""
WORKFLOW_OLD = """must be <type>/MS-123-description when a Jira ticket is attached, or <type>/description otherwise.
Use feature for new capability, bugfix for a defect, or chore for maintenance, with an uppercase
Jira key and lowercase kebab-case description. Never substitute a FISK key for a Jira key."""
WORKFLOW_NEW = """must be feature/<JIRA-KEY>-<description> when Jira is attached; otherwise use the originating
Multica key: feature/<MULTICA-KEY>-<description>. Always use feature/, uppercase issue key and
lowercase kebab-case description, e.g. feature/MS-123-report or feature/FISK-42-report."""
ACCEPTANCE_OLD = """feature/MS-123-description, chore/MS-123-description or bugfix/MS-123-description; omit the Jira
key when no Jira ticket is attached. Delivery Preparation must verify that only this canonical"""
ACCEPTANCE_NEW = """feature/<JIRA-KEY>-<description>, or feature/<MULTICA-KEY>-<description> with no linked Jira ticket.
Use the actual uppercase ticket key and lowercase kebab-case description. Reject a missing key or
wrong prefix. Delivery Preparation must verify that only this canonical"""


def replace(text, before, after):
    if after in text:
        return text
    if text.count(before) != 1:
        raise RuntimeError("Branch instructions changed unexpectedly; reconcile before applying.")
    return text.replace(before, after, 1)


def configure():
    agents = command("agent", "list")
    release = unique(agents, "development-release")
    skill = unique(command("skill", "list"), "delivery-handoff")
    workflow = unique(command("workflow", "list"), "Software Factory")
    if not all((release, skill, workflow)):
        raise RuntimeError("The development release role, delivery-handoff skill and Software Factory are required.")
    skill = command("skill", "get", skill["id"], "--with-content")
    steps = {s["status_key"]: s for s in workflow["steps"]}
    for text in (release["instructions"], skill["content"], steps["delivery_preparation"]["instructions"]):
        if "## Delivery Preparation includes PR creation" not in text:
            raise RuntimeError("Apply configure-delivery-pr.py first; PR creation must remain enabled.")
    records = []
    for row in agents:
        before = row.get("instructions") or ""
        after = before
        if COMMON_OLD in before:
            after = replace(after, COMMON_OLD, COMMON_NEW)
        if row["id"] == release["id"]:
            after = replace(after, RELEASE_OLD, RELEASE_NEW)
        if before != after:
            records.append({"kind": "agent", "id": row["id"], "field": "instructions", "before": before, "after": after})
    after = replace(skill["content"], SKILL_OLD, SKILL_NEW)
    if after != skill["content"]:
        records.append({"kind": "skill", "id": skill["id"], "field": "content", "before": skill["content"], "after": after})
    expected_steps = json.loads(json.dumps(workflow["steps"]))
    for status, old, new in (("delivery_preparation", WORKFLOW_OLD, WORKFLOW_NEW),
                             ("acceptance", ACCEPTANCE_OLD, ACCEPTANCE_NEW)):
        before = steps[status]["instructions"]
        after = replace(before, old, new).strip()
        if len(after) > 8000:
            raise RuntimeError("Workflow instructions would exceed 8000 characters.")
        for s in expected_steps:
            if s["status_key"] == status:
                s["instructions"] = after
        if before != after:
            records.append({"kind": "workflow_step", "id": workflow["id"], "status": status,
                            "field": "instructions", "before": before, "after": after})
    if records:
        backups = ROOT / "backups"
        backups.mkdir(exist_ok=True)
        provision.write_state(Path(tempfile.mkdtemp(prefix="delivery-branches-", dir=backups)) / "changes.json", records)
    for r in records:
        kind, ident = r["kind"], r["id"]
        if kind == "workflow_step":
            current = next(s for s in command("workflow", "get", ident)["steps"] if s["status_key"] == r["status"])
        else:
            current = command(kind, "get", ident, *(["--with-content"] if kind == "skill" else []))
        if current[r["field"]] != r["before"]:
            raise RuntimeError(f"Concurrent {kind} edit detected; reconcile before retrying.")
        if kind == "workflow_step":
            command("workflow", "handoff", "set", ident, r["status"], "--instructions", r["after"])
        elif kind == "skill":
            command("skill", "update", ident, "--content-stdin", data=r["after"])
        else:
            command(kind, "update", ident, "--instructions", r["after"])
        if kind != "workflow_step":
            saved = command(kind, "get", ident, *(["--with-content"] if kind == "skill" else []))
            if saved[r["field"]] != r["after"]:
                raise RuntimeError(f"Saved {kind} instructions did not match.")
    if command("workflow", "get", workflow["id"])["steps"] != expected_steps:
        raise RuntimeError("Workflow differs from the requested instructions-only change.")
    print(f"Verified PR creation and Jira-first, Multica-fallback feature branch naming ({len(records)} changes).")
    print("No running tasks, Git refs, PRs, workflow routing or services were changed.")


if __name__ == "__main__":
    try:
        with (ROOT / ".operation.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            configure()
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(f"Delivery branch policy failed: {error}", file=sys.stderr)
        sys.exit(1)
