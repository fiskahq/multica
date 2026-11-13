#!/usr/bin/env python3
"""Give the release agent scoped Jira writes and configured Confluence access."""
import fcntl
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("provision", ROOT / "provision-agents.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)
cli = provision.bridge

READ_ONLY_ACCESS = """Use tools actually present in the run. Jira search and metadata use jira__request. Ticket edits
use the attached jira_triage__request write tool, whose response is wrapped in data. Confluence
context uses confluence__request through the Docker MCP relay; that tool is read-only."""
WRITE_ACCESS = READ_ONLY_ACCESS.replace(
    "context uses confluence__request through the Docker MCP relay; that tool is read-only.",
    "reads and writes use confluence_write__request through the dedicated Docker relay. Update\ndocumentation within the task; preserve unrelated content and verify the saved page/version.")
LEGACY_POLICY = """## Jira delivery updates

Update the linked Jira ticket during delivery using jira_triage__request: record the verified PR,
implemented impact/change and actual validation. Discover field IDs via jira__request; populate
Impact and Change Description when a solution is implemented. Read current fields first, preserve
unrelated edits, use ADF for rich text, and verify writes by reading back. Reconcile uncertain
results before retrying; avoid duplicate comments. Follow David's first-work/Open assignment rule
and the task's authorized transitions; do not mark Done merely because a PR exists. Missing edit
access blocks the Jira update with a named owner and resume step. Use confluence__request for
linked documentation context. Deletes, bulk edits and administration are outside this role.
"""
WRITE_POLICY = """## Jira delivery updates

Use jira_triage__request to update linked Jira tickets with the verified PR, implemented impact
and actual validation. Discover field IDs via jira__request; fill Impact and Change Description
after implementation. Preserve unrelated edits, use ADF, verify writes and reconcile uncertain
results before retrying. Follow David's first-work/Open rule and authorized transitions; a PR
does not mean Done. Use confluence_write__request to read, create or update task documentation. Read
the current page and version before editing, preserve unrelated content, and on a version conflict
reread and reconcile instead of overwriting. Verify the saved page/version and report its link.
Missing access blocks the update with owner and resume step. Deletes, bulk edits and administration
are outside this role.
"""


def unique(rows, name):
    matches = [row for row in rows if row["name"] == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {name}.")
    return matches[0]


def add_policy(text, policy):
    if policy.strip() in text:
        return text.strip()
    if LEGACY_POLICY.strip() in text:
        return text.replace(LEGACY_POLICY.strip(), policy.strip()).strip()
    if "## Jira delivery updates" in text:
        raise RuntimeError("A different Jira delivery policy already exists; reconcile it first.")
    return text.rstrip() + "\n\n" + policy.strip()


def main():
    with (ROOT / ".operation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        agent = unique(cli("agent", "list", "--output", "json"), "development-release")
        servers = cli("workspace", "mcp", "list", agent["workspace_id"], "--output", "json")
        wanted = [unique(servers, name) for name in
                  ("factory-jira", "factory-jira-triage", "factory-confluence")]
        workflow = unique(cli("workflow", "list", "--output", "json"), "Software Factory")
        step = next(s for s in workflow["steps"] if s["status_key"] == "delivery_preparation")
        if step["handler"] != {"type": "agent", "id": agent["id"]}:
            raise RuntimeError("Delivery Preparation is assigned to a different handler.")
        instructions = agent["instructions"]
        writable = (ROOT / "confluence-write-state.json").exists()
        if writable:
            wanted.append(unique(servers, "factory-confluence-write"))
        access = WRITE_ACCESS if writable else READ_ONLY_ACCESS
        policy = WRITE_POLICY if writable else LEGACY_POLICY
        old = "Use tools actually present in the run. Assigned Jira and Confluence relay tools are read-only."
        if old in instructions:
            instructions = instructions.replace(old, access, 1)
        elif READ_ONLY_ACCESS in instructions:
            instructions = instructions.replace(READ_ONLY_ACCESS, access, 1)
        elif access not in instructions:
            raise RuntimeError("Release tool instructions changed; review before updating.")
        instructions = add_policy(instructions, policy)
        workflow_text = add_policy(step["instructions"], policy)
        if len(workflow_text) > 8000:
            raise RuntimeError("Delivery workflow exceeds the native instruction limit.")
        backups = ROOT / "backups"
        backups.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="release-mcp-", dir=backups)) / "instructions.json"
        provision.write_state(backup, {"agent_id": agent["id"], "instructions": agent["instructions"],
                                      "workflow_id": workflow["id"], "step": step,
                                      "mcp_ids": [s["id"] for s in cli("agent", "mcp", "list", agent["id"], "--output", "json")]})
        for server in wanted:
            cli("agent", "mcp", "add", agent["id"], server["id"], "--output", "json")
        if instructions != agent["instructions"]:
            cli("agent", "update", agent["id"], "--instructions", instructions, "--output", "json")
        if workflow_text != step["instructions"]:
            cli("workflow", "handoff", "set", workflow["id"], "delivery_preparation",
                "--instructions", workflow_text, "--output", "json")
        saved = cli("agent", "get", agent["id"], "--output", "json")
        attached = cli("agent", "mcp", "list", agent["id"], "--output", "json")
        saved_workflow = cli("workflow", "get", workflow["id"], "--output", "json")
        expected = json.loads(json.dumps(workflow["steps"]))
        next(s for s in expected if s["status_key"] == "delivery_preparation")["instructions"] = workflow_text
        if saved["instructions"] != instructions or saved_workflow["steps"] != expected:
            raise RuntimeError("Saved instructions did not match; inspect the backup and current configuration.")
        if not {s["id"] for s in wanted} <= {s["id"] for s in attached}:
            raise RuntimeError("A requested MCP attachment is missing.")
        print("Verified release agent: Jira reads/writes, Confluence " +
              ("reads/writes" if writable else "reads") + ", and delivery workflow instructions.")
        print("No Jira tickets changed and no services restarted.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(f"Release MCP setup failed: {error}", file=sys.stderr)
        sys.exit(1)
