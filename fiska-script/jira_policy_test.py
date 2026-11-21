import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("jira_policy", ROOT / "configure-jira-policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class NativeFixture:
    def __init__(self):
        self.rows = {
            "agent": [{"id": "a", "name": "builder", "instructions": "Keep human guidance.",
                       "workspace_id": "workspace", "max_concurrent_tasks": 3, "runtime_id": "runtime", "model": "model"},
                      {"id": "learner", "name": "background-learner", "instructions": "No tools."}],
            "squad": [{"id": "s", "name": "team", "instructions": "Leader rules.", "leader_id": "a"}],
            "skill": [{"id": "k", "name": "ticket-creation", "content": "Keep existing content."},
                      {"id": "other", "name": "unrelated", "content": "Leave intact."}],
            "workflow": [{"id": "w", "name": "factory", "steps": [
                {"status_key": "backlog", "instructions": "", "handler": {"type": "none"}},
                {"status_key": "development", "instructions": "Review exact SHA.",
                 "handler": {"type": "agent", "id": "a"},
                 "assignee_id": "a", "next_status_key": "qa"},
                {"status_key": "delivery_preparation", "instructions": "x" * 7892,
                 "handler": {"type": "agent", "id": "a"}},
                {"status_key": "acceptance", "instructions": "", "handler": {"type": "creator"}},
                {"status_key": "done", "instructions": "Terminal guidance."}]}],
        }
        self.writes = []
        self.gets = 0
        self.intervene = False
        self.attachments = [{"id": "jira-read"}]

    def command(self, kind, operation, *args, data=None):
        if kind == "workspace":
            return [{"id": "jira-write", "name": "factory-jira-triage"}]
        if operation == "mcp":
            if args[0] == "add":
                self.writes.append((kind, operation))
                self.attachments.append({"id": args[2]})
            return copy.deepcopy(self.attachments)
        if operation == "list":
            return copy.deepcopy(self.rows[kind])
        if operation == "handoff":
            ident, status = args[1:3]
        else:
            ident = args[0]
        row = next(r for r in self.rows[kind] if r["id"] == ident)
        if operation == "get":
            if kind == "agent" and ident == "a":
                self.gets += 1
                if self.intervene and self.gets == 2:
                    row["instructions"] = "Concurrent human edit."
            return copy.deepcopy(row)
        self.writes.append((kind, operation))
        if operation == "handoff":
            row = next(s for s in row["steps"] if s["status_key"] == status)
        if kind == "skill":
            row["content"] = data
        else:
            row["instructions"] = args[args.index("--instructions") + 1]
            if kind == "workflow":
                row["instructions"] = (row["instructions"].strip()
                                       if row.get("handler", {}).get("type", "none") != "none" else "")
        return copy.deepcopy(row)


class JiraPolicyTests(unittest.TestCase):
    def test_ticket_writer_gets_scoped_access_without_replacing_read_access(self):
        native = NativeFixture()
        native.rows["agent"][0].update(name="Guy", workspace_id="workspace")
        with tempfile.TemporaryDirectory() as tmp:
            policy.configure(native.command, Path(tmp))
            self.assertEqual(native.attachments, [{"id": "jira-read"}, {"id": "jira-write"}])
            writes = len(native.writes)
            policy.configure(native.command, Path(tmp))
            self.assertEqual(len(native.writes), writes)

    def test_collaboration_writers_are_limited_to_selected_roles(self):
        for name in ("Guy", "Architect", "development-release", "development-builder", "background-learner"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                native = NativeFixture()
                native.rows["agent"][0]["name"] = name
                original = native.command
                def command(kind, operation, *args, data=None):
                    if kind == "workspace":
                        return [{"id": server, "name": server} for server in policy.WRITE_SERVERS]
                    return original(kind, operation, *args, data=data)
                policy.configure(command, Path(tmp))
                expected = 4 if name in {"Guy", "Architect", "development-release"} else 1
                self.assertEqual(len(native.attachments), expected)

    def test_apply_and_rerun_preserve_configuration_and_back_up_originals(self):
        native = NativeFixture()
        original = copy.deepcopy(native.rows)
        with tempfile.TemporaryDirectory() as tmp:
            policy.configure(native.command, Path(tmp))
            backups = list(Path(tmp).glob("backups/jira-policy-*/changes.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            self.assertIn("Keep human guidance.", native.rows["agent"][0]["instructions"])
            for key in ("max_concurrent_tasks", "runtime_id", "model"):
                self.assertEqual(native.rows["agent"][0][key], original["agent"][0][key])
            self.assertEqual(native.rows["agent"][1], original["agent"][1])
            self.assertEqual(native.rows["skill"][1], original["skill"][1])
            steps = native.rows["workflow"][0]["steps"]
            self.assertEqual(steps[0], original["workflow"][0]["steps"][0])
            self.assertEqual(steps[3]["instructions"], policy.CHECK)
            self.assertEqual(steps[1]["assignee_id"], "a")
            self.assertEqual(steps[1]["next_status_key"], "qa")
            self.assertEqual(steps[-1], original["workflow"][0]["steps"][-1])
            self.assertLessEqual(len(steps[2]["instructions"]), 8000)
            writes = len(native.writes)
            policy.configure(native.command, Path(tmp))
            self.assertEqual(len(native.writes), writes)
            self.assertEqual(len(list(Path(tmp).glob("backups/jira-policy-*/changes.json"))), 1)

    def test_dry_run_never_writes_or_creates_backup(self):
        native = NativeFixture()
        with tempfile.TemporaryDirectory() as tmp:
            policy.configure(native.command, Path(tmp), dry_run=True)
            self.assertEqual(native.writes, [])
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_oversized_workflow_aborts_before_any_writes(self):
        native = NativeFixture()
        native.rows["workflow"][0]["steps"][1]["instructions"] = "x" * 8000
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(RuntimeError, "8000"):
            policy.configure(native.command, Path(tmp))
        self.assertEqual(native.writes, [])

    def test_modified_policy_and_broken_markers_require_reconciliation(self):
        original = policy.provision.jira_instructions("Human guidance.")
        for text in (original.replace("Every Multica ticket", "Some Multica tickets"),
                     original.replace("<!-- fiska:jira-ticket-policy:end -->", "")):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, "reconcile"):
                policy.provision.jira_instructions(text)

    def test_detected_concurrent_edit_is_not_overwritten(self):
        native = NativeFixture()
        native.intervene = True
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(RuntimeError, "Concurrent edit"):
            policy.configure(native.command, Path(tmp))
        self.assertNotIn(("agent", "update"), native.writes)
        self.assertEqual(native.rows["agent"][0]["instructions"], "Concurrent human edit.")


if __name__ == "__main__":
    unittest.main()
