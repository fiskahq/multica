#!/usr/bin/env python3
"""Install evidence-led intake and complete candidate reviews without dispatching work."""
import argparse
import fcntl
import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("qa_policy", ROOT / "configure-qa-policy.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)
START = "<!-- fiska:factory-policy:start -->"
END = "<!-- fiska:factory-policy:end -->"
REVIEWERS = {"code_review": "development-reviewer", "architecture_review": "development-practices",
             "adversarial_review": "development-adversary"}
NEXT = {"code_review": "architecture_review", "architecture_review": "adversarial_review",
        "adversarial_review": "qa"}


def policy(name):
    return (ROOT / "factory-policy" / (name + ".md")).read_text().strip()


def append(text, body):
    text = text or ""
    block = START + "\n" + body.strip() + "\n" + END
    if START in text or END in text:
        if text.count(START) != 1 or text.count(END) != 1 or block not in text:
            raise RuntimeError("Edited factory policy found; reconcile before applying.")
        return text
    return (text.rstrip() + "\n\n" + block).strip()


def review_step(before, status):
    # Preserve shared Git, authorization and native handoff rules; replace the old early-exit gate.
    if START in before:
        body = policy("reviews") + "\n\n" + REVIEW_SCOPE[status]
        return append(before, body)
    marker = "Review the latest builder handoff's exact candidate SHA"
    if before.count(marker) != 1:
        raise RuntimeError(f"Unrecognized {status} instructions; reconcile before replacing the gate.")
    prefix = before[:before.index(marker)].rstrip()
    prefix = prefix.replace("Send defects back to that\nbuilder with the rejected SHA and actionable findings.",
                            "Collect defects across all three reviews before returning one combined list.")
    prefix = prefix.replace("defective work that can be corrected is Needs changes.",
                            "defective work returns after the complete review round.")
    return append(prefix, policy("reviews") + "\n\n" + REVIEW_SCOPE[status])


REVIEW_SCOPE = {
    "code_review": """Use delivery-review for correctness, security, compatibility, data integrity and tests.
Verify base and required predecessor ancestry of the exact candidate. Inspect all acceptance
criteria. For merchant-services, check e2e coverage and the @smoke decision using merchant-services-e2e;
unsafe production smoke tests remain blockers. Record missing permutation tests before other findings.
Post the full receipt, then Step done to Architecture Review, including when blockers exist.""",
    "architecture_review": """Require a completed Code Review receipt for this SHA, including a failing receipt.
Use codebase-design and delivery-review for layering, boundaries, ownership, API/schema/message
contracts, migrations, rollback and transaction/concurrency safety. Verify ancestry and the plan.
Check the one-behavior scope and >15-file approval. Post all findings, then Step done to Adversarial
Review, including when blockers exist. Do not require the earlier review to pass.""",
    "adversarial_review": """Require completed code and architecture receipts for this SHA, including failing receipts.
Use cold-reviewer and pci-owasp-checklist. Check malformed inputs, boundaries, duplicate events,
partial failure, time zones, overlapping workers, tenant access and sensitive data leaks. Prefer
reproductions and tests over speculation. Complete your own review before consolidating all three.
Post the combined findings and receipt links. Any blocker returns once to Development; otherwise
Step done to QA. Missing coverage/access is Blocked with owner and exact resume step.""",
}

INTAKE_STEP = """## Intake evidence contract
Follow your Evidence and delivery contract: record Assumptions not yet observed, one independently
verifiable behavior per PR, invariants/fact list and first-candidate permutation tests for event or
money work, the written blocking bar and full MySQL/local e2e prerequisites. Target 3-6 files;
more than 15 hand-maintained files needs David's explicit approval recorded on the ticket.
Observe cheaply testable external assumptions in a safe switched-off slice before dependent work.
Preserve existing explicit scope exemptions, including FISK-172 K2(a). Do not create slice or
follow-up tickets when the user prohibits them; document the proposed split on the existing issue."""
BUILDER_STEP = """## Complete candidate contract
Follow Candidate completeness: one behavior, >15-file approval, first-candidate invariant/fact
permutation tests and full required MySQL results. Freeze the SHA during the three-review round;
fix only after the combined findings packet, not an early review failure. Preserve explicit owner
scope exceptions. QA rework still requires the complete QA report and planner fix plan."""
TOOLING = """## Disposable MySQL verification
For merchant-services payment API and Nuvei adapter tests, factory-mysql is installed in the
runtime. Use `factory-mysql <candidate-checkout> --docker-context multica-qa --output <new-evidence-dir>`.
It runs both entire projects against its own loopback-only tmpfs MySQL, continues after a suite
failure, retains logs/TRX/results and removes only its own container. It requires dotnet and the
mysql:8.0 image; do not replace required repository suites with only these two projects. Inspect
TRX skip/failure counts and coverage, not just exit codes. Run other required suites independently.
This helper does not implement FISK-190's schema reuse and does not supply a terminal emulator.
Card-present work must verify baseline-specific emulator, fixtures, routing and local end-to-end
coverage during intake; missing assets or a terminal transport pointed at port 1 are Blocked
coverage. A green card-not-present emulator is not evidence of a card-present flow."""


def plan(command):
    agents = command("agent", "list")
    records = []
    policies = {
        "Architect": policy("planning"), "development-planner": policy("planning"),
        "development-advisor": policy("planning"), "development-builder": policy("builder"),
        "development-qa": TOOLING,
        "development-lead": policy("planning") + "\n\n" + policy("reviews"),
        **{name: policy("reviews") for name in REVIEWERS.values()},
    }
    for name, body in policies.items():
        row = qa.named(agents, name, required=False)
        if row:
            row = command("agent", "get", row["id"])
            before = row.get("instructions") or ""
            after = append(before, body)
            if name == "development-builder" and TOOLING not in after:
                if "## Disposable MySQL verification" in after:
                    raise RuntimeError("Edited MySQL tooling guidance found; reconcile before applying.")
                after += "\n\n" + TOOLING
            records.append(dict(kind="agent", id=row["id"], before=before, after=after))
    for name, body in [("ticket-creation", policy("planning")),
                       ("implementation-planning", policy("planning")),
                       ("delivery-review", policy("reviews")), ("delivery-testing", TOOLING)]:
        row = qa.named(command("skill", "list"), name, required=False)
        if row:
            row = command("skill", "get", row["id"], "--with-content")
            before = row.get("content") or ""
            records.append(dict(kind="skill", id=row["id"], before=before, after=append(before, body)))
    squad = qa.named(command("squad", "list"), "Software Development", required=False)
    if squad:
        row = command("squad", "get", squad["id"])
        before = row.get("instructions") or ""
        records.append(dict(kind="squad", id=row["id"], before=before,
                            after=append(before, policy("reviews"))))
    workflow = qa.named(command("workflow", "list"), "Software Factory", required=False)
    if workflow:
        workflow = command("workflow", "get", workflow["id"])
        steps = {s["status_key"]: s for s in workflow["steps"]}
        for status, name in REVIEWERS.items():
            agent = qa.named(agents, name)
            step = steps[status]
            if step["handler"] != {"type": "agent", "id": agent["id"]} or step.get("next_status_key") != NEXT[status]:
                raise RuntimeError(f"Unexpected handler or forward route for {status}.")
            expected_back = "development" if status == "adversarial_review" else None
            if step.get("back_status_key") not in (None, "development") or (expected_back and step.get("back_status_key") != expected_back):
                raise RuntimeError(f"Unexpected return route for {status}.")
            before = step.get("instructions") or ""
            after = review_step(before, status)
            records.append(dict(kind="workflow", id=workflow["id"], status=status, before=before,
                                after=after, step=step, back=expected_back))
        for status, body in [("todo", INTAKE_STEP), ("development", BUILDER_STEP)]:
            step = steps[status]
            before = step.get("instructions") or ""
            records.append(dict(kind="workflow", id=workflow["id"], status=status, before=before,
                                after=append(before, body), step=step, back=step.get("back_status_key")))
    for record in records:
        if record["kind"] == "workflow" and len(record["after"]) > 8000:
            raise RuntimeError(f"{record['status']} exceeds the workflow instruction limit.")
    return [r for r in records if r["before"] != r["after"] or
            (r["kind"] == "workflow" and r["step"].get("back_status_key") != r["back"])]


def apply(command, records, deployment):
    if records:
        backups = deployment / "backups"
        backups.mkdir(exist_ok=True)
        qa.provision.write_state(Path(tempfile.mkdtemp(prefix="factory-policy-", dir=backups)) / "changes.json", records)
    # Apply role/skill guidance first; no issue writes or runs are dispatched.
    qa.apply(command, [r for r in records if r["kind"] != "workflow"], deployment)
    for record in (r for r in records if r["kind"] == "workflow"):
        current = command("workflow", "get", record["id"])
        step = next(s for s in current["steps"] if s["status_key"] == record["status"])
        if step != record["step"]:
            raise RuntimeError("Concurrent workflow edit detected; reconcile before retrying.")
        command("workflow", "handoff", "set", record["id"], record["status"],
                "--instructions", record["after"], "--back", record["back"] or "")
        changed = dict(step, instructions=record["after"])
        if record["back"]:
            changed["back_status_key"] = record["back"]
        else:
            changed.pop("back_status_key", None)
        expected = [changed if s["status_key"] == record["status"] else s for s in current["steps"]]
        if command("workflow", "get", record["id"])["steps"] != expected:
            raise RuntimeError("Workflow readback differs from the planned review change.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    def command(*args, data=None):
        return qa.provision.bridge(*args, "--output", "json", data=data)
    with (ROOT / ".operation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        records = plan(command)
        if not args.dry_run:
            apply(command, records, ROOT)
        print(f"{'Planned' if args.dry_run else 'Verified'} {len(records)} factory policy updates. No tickets or tasks created.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"Factory policy setup failed: {error}", file=sys.stderr)
        sys.exit(1)
