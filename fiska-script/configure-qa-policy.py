#!/usr/bin/env python3
"""Install complete QA reporting and native planner consultation without starting tasks."""
import argparse
import fcntl
import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("provision", ROOT / "provision-agents.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)
START = "<!-- fiska:qa-fix-policy:start -->"
END = "<!-- fiska:qa-fix-policy:end -->"

PLANNER = """## QA fix-planning consultation

When QA requests a fix plan, treat it as a bounded consultation on the originating issue, not
permission to run QA or move the issue out of its current workflow step. Read the complete QA
report, evidence, candidate SHA, repository instructions and related code. Account for every
failure, blocked check and acceptance criterion. Return likely causes with evidence, file/symbol
change guidance, dependencies, implementation order, regression tests and the full rerun commands.
Distinguish confirmed causes from hypotheses and infrastructure blockers from code defects.
Consult development-advisor for difficult implementation design using the existing protocol.
Do not write code, start a competing pipeline, mark Step done, or dispatch the builder yourself.
Reply in the originating request thread without agent mentions so QA resumes with one complete
plan tied to the rejected SHA. Link existing plans instead of creating duplicate consultations.
"""
IMPLEMENTATION = """## QA fix-plan prerequisite

For a QA rejection, require the complete QA report and development-planner's fix plan for the
rejected candidate before implementation. If missing, request that bounded consultation through
the existing issue and wait; do not fix only the first failure. The builder owns implementation
of the complete agreed plan. Preserve the issue's canonical branch and Jira mapping, report the
new SHA, and repeat required reviews and the full QA scope. No new human approval is implied;
existing decision policy and explicit user instructions still apply. Squad leaders must obtain
this report and plan before dispatching the builder for QA fixes.
"""
TESTING = """## Complete QA coverage

During QA, the complete repository- and ticket-required test scope is mandatory, regardless of
whether focused checks pass or fail. This overrides the general advice above about broadening
only after a pass. Disable fail-fast/bail where supported, run every independent suite, and
retain each result. Report failed, blocked, skipped and not-run checks separately with evidence
and the exact candidate SHA. Before implementation of QA fixes, follow the QA role's bounded
development-planner consultation and return the complete report plus plan to the builder.
"""


def append_policy(text, policy):
    text = text or ""
    block = START + "\n" + policy.strip() + "\n" + END
    if START in text or END in text:
        if block not in text or text.count(START) != 1 or text.count(END) != 1:
            raise RuntimeError("An edited QA policy exists; reconcile it before applying.")
        return text
    return (text.rstrip() + "\n\n" + block).strip()


def named(rows, name, required=True):
    matches = [row for row in rows if row["name"] == name]
    if len(matches) == 1:
        return matches[0]
    if not matches and not required:
        return None
    raise RuntimeError(f"Expected exactly one {name}.")


def plan(command):
    agents = command("agent", "list")
    planner = named(agents, "development-planner")
    qa = named(agents, "development-qa")
    qa_policy = (ROOT / "qa-policy.md").read_text().replace(
        "{{PLANNER}}", f"[@development-planner](mention://agent/{planner['id']})")
    records = []
    for name, policy in [("development-qa", qa_policy), ("development-planner", PLANNER),
                         ("development-builder", IMPLEMENTATION), ("development-lead", IMPLEMENTATION)]:
        row = named(agents, name, required=name != "development-lead")
        if row:
            row = command("agent", "get", row["id"])
            records.append(dict(kind="agent", id=row["id"], before=row.get("instructions") or "",
                                after=append_policy(row.get("instructions"), policy)))
    squad = named(command("squad", "list"), "Software Development", required=False)
    if squad:
        squad = command("squad", "get", squad["id"])
        records.append(dict(kind="squad", id=squad["id"], before=squad.get("instructions") or "",
                            after=append_policy(squad.get("instructions"), IMPLEMENTATION)))
    skill = named(command("skill", "list"), "delivery-testing", required=False)
    if skill:
        skill = command("skill", "get", skill["id"], "--with-content")
        records.append(dict(kind="skill", id=skill["id"], before=skill.get("content") or "",
                            after=append_policy(skill.get("content"), TESTING)))
    workflow = named(command("workflow", "list"), "Software Factory", required=False)
    if workflow:
        workflow = command("workflow", "get", workflow["id"])
        steps = {s["status_key"]: s for s in workflow["steps"]}
        if steps["qa"]["handler"] != {"type": "agent", "id": qa["id"]}:
            raise RuntimeError("Software Factory QA is assigned to a different handler.")
        for status, policy in [("qa", qa_policy), ("development", IMPLEMENTATION)]:
            before = steps[status].get("instructions") or ""
            after = append_policy(before, policy)
            if len(after) > 8000:
                raise RuntimeError(f"{status} would exceed Multica's instruction limit.")
            records.append(dict(kind="workflow", id=workflow["id"], status=status,
                                before=before, after=after, step=steps[status]))
    return [r for r in records if r["before"] != r["after"]]


def apply(command, records, deployment):
    if records:
        backups = deployment / "backups"
        backups.mkdir(exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="qa-policy-", dir=backups)) / "changes.json"
        provision.write_state(backup, records)
    for record in records:
        kind, ident = record["kind"], record["id"]
        current = command(kind, "get", ident, *(["--with-content"] if kind == "skill" else []))
        if kind == "workflow":
            step = next(s for s in current["steps"] if s["status_key"] == record["status"])
            if step != record["step"]:
                raise RuntimeError("Concurrent workflow edit detected; reconcile before retrying.")
            command("workflow", "handoff", "set", ident, record["status"],
                    "--instructions", record["after"])
            expected = [dict(s, instructions=record["after"]) if s["status_key"] == record["status"]
                        else s for s in current["steps"]]
            if command(kind, "get", ident)["steps"] != expected:
                raise RuntimeError("Workflow readback differs from the instructions-only change.")
        else:
            field = "content" if kind == "skill" else "instructions"
            if (current.get(field) or "") != record["before"]:
                raise RuntimeError("Concurrent instruction edit detected; reconcile before retrying.")
            if kind == "skill":
                command(kind, "update", ident, "--content-stdin", data=record["after"])
            else:
                command(kind, "update", ident, "--instructions", record["after"])
            saved = command(kind, "get", ident, *(["--with-content"] if kind == "skill" else []))
            if saved.get(field) != record["after"]:
                raise RuntimeError("Saved instructions did not match.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    def command(*args, data=None):
        return provision.bridge(*args, "--output", "json", data=data)
    with (ROOT / ".operation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        records = plan(command)
        if not args.dry_run:
            apply(command, records, ROOT)
        print(f"{'Planned' if args.dry_run else 'Verified'} {len(records)} QA policy updates. "
              "No tasks started or workflow routing changed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"QA policy setup failed: {error}", file=sys.stderr)
        sys.exit(1)
