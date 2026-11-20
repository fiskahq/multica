import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("factory", Path(__file__).with_name("configure-factory-policy.py"))
factory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(factory)


class FactoryTest(unittest.TestCase):
    def setUp(self):
        self.rows = {
            "agent": [{"id": name, "name": name, "instructions": "Keep role boundaries."}
                      for name in [*factory.REVIEWERS.values(), "development-planner", "development-builder"]],
            "skill": [{"id": "skill", "name": "ticket-creation", "content": "Keep Jira prerequisite."}],
            "squad": [],
            "workflow": [{"id": "factory", "name": "Software Factory", "steps": [
                *[{"status_key": status, "handler": {"type": "agent", "id": name},
                   "next_status_key": factory.NEXT[status], "back_status_key": "development",
                   "instructions": "Keep Git authorization.\n\nReview the latest builder handoff's exact candidate SHA\nReject immediately."}
                  for status, name in factory.REVIEWERS.items()],
                {"status_key": "todo", "instructions": "Keep intake.", "next_status_key": "development"},
                {"status_key": "development", "instructions": "Keep QA fix planning.", "back_status_key": "todo"},
                {"status_key": "qa", "instructions": "Keep full QA coverage.", "back_status_key": "development"},
                {"status_key": "acceptance", "instructions": "David decides.", "handler": {"type": "member", "id": "david"}},
            ]}],
        }

    def command(self, kind, verb, *args, data=None):
        rows = self.rows[kind]
        if verb == "list":
            return copy.deepcopy(rows)
        if verb == "handoff":
            _, ident, status, _, body, _, back = args
            row = next(r for r in rows if r["id"] == ident)
            step = next(s for s in row["steps"] if s["status_key"] == status)
            step["instructions"] = body
            if back:
                step["back_status_key"] = back
            else:
                step.pop("back_status_key", None)
            return copy.deepcopy(row)
        row = next(r for r in rows if r["id"] == args[0])
        if verb == "update":
            row["content" if kind == "skill" else "instructions"] = data if kind == "skill" else args[2]
        return copy.deepcopy(row)

    def test_round_has_no_early_return_and_preserves_qa_human_and_forward_routes(self):
        before = copy.deepcopy(self.rows)
        with tempfile.TemporaryDirectory() as directory:
            factory.apply(self.command, factory.plan(self.command), Path(directory))
            backup = list(Path(directory).glob("backups/factory-policy-*/changes.json"))
            self.assertEqual(len(backup), 1)
            self.assertEqual(backup[0].stat().st_mode & 0o777, 0o600)
        steps = self.rows["workflow"][0]["steps"]
        for step in steps[:2]:
            self.assertNotIn("back_status_key", step)
        self.assertEqual(steps[2]["back_status_key"], "development")
        for old, new in zip(before["workflow"][0]["steps"][:3], steps[:3]):
            self.assertEqual(new["next_status_key"], old["next_status_key"])
            self.assertEqual(new["handler"], old["handler"])
            self.assertTrue(new["instructions"].startswith("Keep Git authorization."))
            self.assertNotIn("Reject immediately", new["instructions"])
        self.assertEqual(steps[-2:], before["workflow"][0]["steps"][-2:])
        self.assertEqual(factory.plan(self.command), [])

    def test_refuses_unknown_workflow_before_any_write(self):
        for change in [lambda s: s.update(handler={"type": "agent", "id": "another"}),
                       lambda s: s.update(next_status_key="development"),
                       lambda s: s.update(instructions="Custom review instructions.")]:
            with self.subTest(change=change):
                self.setUp()
                change(self.rows["workflow"][0]["steps"][0])
                before = copy.deepcopy(self.rows)
                with self.assertRaises(RuntimeError):
                    factory.plan(self.command)
                self.assertEqual(before, self.rows)

    def test_concurrent_route_edit_is_preserved(self):
        records = [r for r in factory.plan(self.command) if r["kind"] == "workflow"]
        self.rows["workflow"][0]["steps"][0]["next_status_key"] = "blocked"
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(RuntimeError, "Concurrent"):
            factory.apply(self.command, records, Path(directory))
        self.assertEqual(self.rows["workflow"][0]["steps"][0]["next_status_key"], "blocked")

    def test_edited_managed_block_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            factory.apply(self.command, factory.plan(self.command), Path(directory))
        self.rows["agent"][0]["instructions"] = self.rows["agent"][0]["instructions"].replace("15", "30")
        with self.assertRaisesRegex(RuntimeError, "Edited"):
            factory.plan(self.command)


if __name__ == "__main__":
    unittest.main()
