import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("qa_policy", Path(__file__).with_name("configure-qa-policy.py"))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.rows = {
            "agent": [{"id": name, "name": name, "instructions": "Existing role instruction."}
                      for name in ("development-qa", "development-planner", "development-builder", "development-lead")],
            "squad": [{"id": "squad", "name": "Software Development", "instructions": "Keep squad guidance."}],
            "workflow": [{"id": "workflow", "name": "Software Factory", "steps": [
                {"status_key": "qa", "handler": {"type": "agent", "id": "development-qa"},
                 "instructions": "Keep QA guidance.", "back_status_key": "development", "next_status_key": "delivery_preparation"},
                {"status_key": "development", "handler": {"type": "agent", "id": "development-builder"},
                 "instructions": "Keep development guidance.", "next_status_key": "code_review"},
                {"status_key": "acceptance", "handler": {"type": "member", "id": "human"}, "instructions": "Keep human review."},
            ]}],
        }

    def command(self, kind, verb, *args):
        rows = self.rows[kind]
        if verb == "list":
            return copy.deepcopy(rows)
        if verb == "handoff":
            _, ident, status, _, text = args
            row = next(r for r in rows if r["id"] == ident)
            next(s for s in row["steps"] if s["status_key"] == status)["instructions"] = text
            return copy.deepcopy(row)
        row = next(r for r in rows if r["id"] == args[0])
        if verb == "update":
            row["instructions"] = args[2]
        return copy.deepcopy(row)

    def test_apply_preserves_routing_and_existing_text_and_is_idempotent(self):
        before = copy.deepcopy(self.rows)
        records = policy.plan(self.command)
        with tempfile.TemporaryDirectory() as directory:
            policy.apply(self.command, records, Path(directory))
            self.assertEqual(len(list(Path(directory).glob("backups/*/changes.json"))), 1)
        self.assertEqual(policy.plan(self.command), [])
        for old, new in zip(before["workflow"][0]["steps"], self.rows["workflow"][0]["steps"]):
            self.assertEqual({k: v for k, v in old.items() if k != "instructions"},
                             {k: v for k, v in new.items() if k != "instructions"})
            self.assertTrue(new["instructions"].startswith(old["instructions"]))
        self.assertEqual(before["workflow"][0]["steps"][2], self.rows["workflow"][0]["steps"][2])

    def test_concurrent_edit_is_preserved(self):
        records = policy.plan(self.command)
        self.rows["agent"][0]["instructions"] = "New user instruction."
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(RuntimeError, "Concurrent"):
            policy.apply(self.command, records, Path(directory))
        self.assertEqual(self.rows["agent"][0]["instructions"], "New user instruction.")

    def test_missing_planner_or_wrong_qa_handler_prevents_changes(self):
        self.rows["workflow"][0]["steps"][0]["handler"]["id"] = "someone-else"
        with self.assertRaisesRegex(RuntimeError, "different handler"):
            policy.plan(self.command)
        self.rows["agent"] = [r for r in self.rows["agent"] if r["name"] != "development-planner"]
        with self.assertRaisesRegex(RuntimeError, "development-planner"):
            policy.plan(self.command)


if __name__ == "__main__":
    unittest.main()
