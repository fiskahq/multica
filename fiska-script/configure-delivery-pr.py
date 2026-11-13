#!/usr/bin/env python3
"""Make PR creation part of Delivery Preparation; preserve routing and candidate checks."""
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

AUTH_OLD = "SHA. This permission does not authorize PR publication, merge, release or deployment."
AUTH_NEW = """SHA. David also authorizes development-release to create or update the task's pull request as
part of Delivery Preparation without another approval. Merge, release and deployment still
require their own task authorization."""
PR_POLICY = """## Delivery Preparation includes PR creation

David authorizes development-release to create or update the pull request for the assigned code
change as part of Delivery Preparation. This standing authorization is sufficient; do not ask
again or stop at a prepared PR title/body. Explicit task instructions to keep work local or to
prepare a packet only still take precedence. A non-code deliverable with no repository change
does not need an artificial PR; state that exception in the handoff.

Complete the existing exact-candidate review, QA and Acceptance branch-finalization checks first.
Use the finalized canonical source branch at the unchanged approved SHA and the task's actual
target branch. Confirm the repository and remote from project resources; never assume the target
is main. If the target is materially ambiguous, finish the packet and report the missing decision.
Use the installed authenticated gh CLI for GitHub, or the project's configured Git host tool.
GitHub MCP is not required. Inspect CLI help for supported options.

Before creating, inspect PRs for this repository, source branch and target, including closed and
merged results. Reuse the matching open PR and update its title/body when needed. Do not create
a duplicate. Reconcile a closed, merged or mismatched PR and any uncertain prior create result
before retrying; do not reopen, close, retarget or migrate an existing PR without authorization
covering that change. An old PR head still follows the skill's branch-preservation rules.

Create a normal open PR ready for human review unless the task explicitly requests a draft.
Write the body to a UTF-8 file and use gh pr create/edit --body-file, with the exact repository,
base and head on creation. Follow the repository template. Explain the problem and resulting
behavior, link the issue, identify the candidate, and include actual validation, limitations and
relevant rollout/rollback information. Do not claim tests that were not run.

Read the saved PR back from the host. Verify its URL, open state, repository, target branch,
source branch and head commit match the delivered candidate and requested draft state. A command
exit code or a local draft is not a publication receipt. If the head changed, reconcile it before
claiming readiness; changed candidates require fresh review and QA. Report missing access or
publication failures as Blocked, with owner and Delivery Preparation as the resume step, retaining
the prepared packet and any partial-success receipt. Do not hand off a code change to Acceptance
without a verified PR URL unless an explicit task exception applies.

Post the verified PR URL, base/source branches, exact head SHA and delivery evidence to the
originating Multica issue. Then use the native workflow handoff once, or return the result to the
squad leader as appropriate. Do not merge, enable auto-merge, release or deploy under this
authorization. This is an agent instruction, not a server-enforced status gate.
"""
PR_WORKFLOW = """## Delivery Preparation includes PR creation

Follow delivery-handoff's PR-creation procedure under David's standing authorization. Use the
authenticated gh CLI (GitHub MCP is not required), the finalized source branch and approved SHA,
and the actual project target branch. Reuse a matching open PR; inspect closed/merged matches
and reconcile uncertain create results before retrying. Do not create duplicates. Create a normal
open PR unless a draft is explicitly requested. Use a body file and the repository's PR template.
Verify the saved PR's URL, repository, open state, base/source branches, head SHA and draft state.
Post that receipt with the delivery packet before Step done. Missing access or a failed/mismatched
publication is Blocked, with Delivery Preparation as the resume step. Explicit local-only or
packet-only requests and non-code deliverables without repository changes are exceptions.
Merge, auto-merge, release and deployment are not authorized by PR creation.
"""


def replace(text, old, new):
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError("Instructions changed unexpectedly; review them before applying this policy.")
    return text.replace(old, new, 1)


def append_policy(text):
    if PR_POLICY.strip() in text:
        return text
    if "## Delivery Preparation includes PR creation" in text:
        raise RuntimeError("A different delivery PR policy is already present; reconcile it first.")
    return text.rstrip() + "\n\n" + PR_POLICY


def named(rows, name):
    matches = [row for row in rows if row["name"] == name]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {name}.")
    return matches[0]


def main():
    with (ROOT / ".operation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        agents = cli("agent", "list", "--output", "json")
        release = named(agents, "development-release")
        leader = named(agents, "development-lead")
        squad = named(cli("squad", "list", "--output", "json"), "Software Development")
        squad = cli("squad", "get", squad["id"], "--output", "json")
        skill = named(cli("skill", "list", "--output", "json"), "delivery-handoff")
        skill = cli("skill", "get", skill["id"], "--with-content", "--output", "json")
        workflow = named(cli("workflow", "list", "--output", "json"), "Software Factory")
        steps = {step["status_key"]: step for step in workflow["steps"]}
        delivery = steps["delivery_preparation"]
        acceptance = steps["acceptance"]
        if delivery["handler"] != {"type": "agent", "id": release["id"]}:
            raise RuntimeError("Delivery Preparation is not assigned to development-release.")

        release_text = replace(release["instructions"], AUTH_OLD, AUTH_NEW)
        release_text = replace(release_text,
            "the task requires it. Use an available authorized Git host tool for an authorized PR action.\nOtherwise return a ready PR title/body and exact branch/head with the missing capability. Do not\nclaim that a draft packet is a published PR.",
            "the task requires it. Create or update the PR as part of Delivery Preparation under the\nstanding authorization below. A prepared title/body is not a completed code delivery.")
        release_text = append_policy(release_text)
        leader_text = replace(leader["instructions"], AUTH_OLD, AUTH_NEW)
        leader_text = replace(leader_text,
            "Use development-release to prepare the final PR or delivery packet when useful and authorized.",
            "Use development-release to prepare the delivery packet and create or update the PR for code\nchanges. David authorizes this without another approval. Require a verified PR URL and matching\nbase/source branches and reviewed SHA before Acceptance, unless the task explicitly requests\nlocal-only or packet-only delivery. Non-code deliverables do not require an artificial PR.")
        squad_text = replace(squad["instructions"],
            "Release: final delivery packet and authorized PR preparation.",
            "Release: final delivery packet and creation/update of the task PR, authorized by David\nwithout another approval. Require its verified URL and exact candidate before Acceptance, except\nfor explicit local-only/packet-only tasks or non-code deliverables with no repository change.")
        skill_text = replace(skill["content"],
            "PR publication, merge, release and deployment still require their own task authorization.",
            "David authorizes development-release to create or update the task PR during Delivery\nPreparation without another approval, as specified below. Merge, release and deployment still\nrequire their own task authorization.")
        skill_text = append_policy(skill_text)
        delivery_text = replace(delivery["instructions"],
            "Prepare a PR title/body. Publish a PR only when the originating request authorizes it and a\nworking authorized tool is available. If publishing was requested but unavailable, use Blocked;\nif the requested deliverable is a local change or prepared packet, a ready draft is sufficient.",
            "Create or update the task PR under David's standing authorization below. No additional\npublication approval is needed. A prepared title/body alone is insufficient for code delivery,\nunless the task explicitly requests local-only or packet-only work.")
        if PR_WORKFLOW.strip() not in delivery_text:
            if "## Delivery Preparation includes PR creation" in delivery_text:
                raise RuntimeError("A different workflow PR policy already exists; reconcile it first.")
            delivery_text = delivery_text.rstrip() + "\n\n" + PR_WORKFLOW
        acceptance_text = replace(acceptance["instructions"],
            "delivery packet and any requested PR receipt.",
            "delivery packet and the verified PR URL for code changes. Check that the PR is open, targets\nthe intended branch and has the exact reviewed head SHA. A prepared PR title/body alone is not\nsufficient. Return a missing or mismatched PR to Delivery Preparation. Explicit local-only or\npacket-only task instructions and non-code deliverables are exceptions.")

        # Workflow handoff normalizes outer whitespace when persisting instructions.
        delivery_text = delivery_text.strip()
        acceptance_text = acceptance_text.strip()
        if max(len(delivery_text), len(acceptance_text)) > 8000:
            raise RuntimeError("Workflow instructions exceed Multica's 8000-character limit.")

        # Save only the fields being changed, never agent environment or MCP credentials.
        records = [
            ("agent", release, "instructions", release_text),
            ("agent", leader, "instructions", leader_text),
            ("squad", squad, "instructions", squad_text),
            ("skill", skill, "content", skill_text),
        ]
        changes = [{"kind": kind, "id": row["id"], "field": field,
                    "before": row[field], "after": content} for kind, row, field, content in records]
        changes += [{"kind": "workflow_step", "id": workflow["id"], "status": status,
                     "before": steps[status]["instructions"], "after": content}
                    for status, content in [("delivery_preparation", delivery_text), ("acceptance", acceptance_text)]]
        backups = ROOT / "backups"
        backups.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="delivery-pr-policy-", dir=backups)) / "changes.json"
        provision.write_state(backup, changes)

        for kind, row, field, content in records:
            if row[field] == content:
                continue
            # Detect intervening edits before writing. This is not an atomic compare-and-swap.
            current = cli(kind, "get", row["id"], *(["--with-content"] if kind == "skill" else []), "--output", "json")
            if current[field] != row[field]:
                raise RuntimeError(f"Concurrent {kind} edit detected; reconcile before retrying.")
            if kind == "skill":
                cli(kind, "update", row["id"], "--content-stdin", "--output", "json", data=content)
            else:
                cli(kind, "update", row["id"], "--instructions", content, "--output", "json")
            saved = cli(kind, "get", row["id"], *(["--with-content"] if kind == "skill" else []), "--output", "json")
            if saved[field] != content:
                raise RuntimeError(f"Saved {kind} instructions did not match.")
        for status, content in [("delivery_preparation", delivery_text), ("acceptance", acceptance_text)]:
            if steps[status]["instructions"] == content:
                continue
            current = cli("workflow", "get", workflow["id"], "--output", "json")
            step = next(s for s in current["steps"] if s["status_key"] == status)
            if step != steps[status]:
                raise RuntimeError("Concurrent workflow edit detected; reconcile before retrying.")
            cli("workflow", "handoff", "set", workflow["id"], status, "--instructions", content, "--output", "json")
        saved = cli("workflow", "get", workflow["id"], "--output", "json")
        expected_steps = json.loads(json.dumps(workflow["steps"]))
        for step in expected_steps:
            if step["status_key"] == "delivery_preparation":
                step["instructions"] = delivery_text
            elif step["status_key"] == "acceptance":
                step["instructions"] = acceptance_text
        if saved["steps"] != expected_steps:
            raise RuntimeError("Workflow readback differs from the intended instructions-only changes.")
        print("Verified release agent, leader, squad, handoff skill, Delivery Preparation and Acceptance instructions.")
        print(f"Before/after instructions saved to {backup}")
        print("No tasks started, PRs created, services restarted or workflow routing changed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print(f"Delivery PR policy failed: {error}", file=sys.stderr)
        sys.exit(1)
