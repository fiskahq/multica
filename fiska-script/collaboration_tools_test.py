import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("collaboration_tools", ROOT / "configure-collaboration-tools.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class CollaborationToolTests(unittest.TestCase):
    def test_scoped_rules_keep_credentials_and_pending_reload_survives_retry(self):
        template = json.loads((ROOT / "mcp-relay/examples/jira-triage.json").read_text())["integration"]
        template["writeRules"] = template["writeRules"][:5]
        template["auth"] = {"type": "bearer", "token": {"env": "PRIVATE"}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mcp-relay/.secrets").mkdir(parents=True)
            (root / "mcp-relay/confluence-write").mkdir()
            (root / "mcp-relay/confluence-write/config.json").write_text("{}")
            path = root / "mcp-relay/config.json"
            path.write_text(json.dumps({"http": {"endpoints": []}, "rest": [template,
                {"name": "confluence", "allowWrites": False, "auth": {"secret": "keep"}},
                {"name": "unrelated", "allowWrites": False}]}))
            setup.prepare(root)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["rest"][0], template)
            self.assertEqual(saved["rest"][3]["auth"], template["auth"])
            self.assertEqual(saved["rest"][3]["name"], "jira_release")
            self.assertEqual(len(saved["rest"][3]["writeRules"]), 3)
            self.assertEqual(saved["http"]["endpoints"][0]["allowTools"], ["jira_release__request"])
            self.assertEqual((root / "mcp-relay/.secrets/endpoint-jira-release-token").stat().st_mode & 0o777, 0o600)
            self.assertFalse(saved["rest"][1]["allowWrites"])
            self.assertFalse(saved["rest"][2]["allowWrites"])
            self.assertTrue(any(r["pathPattern"] == "^/rest/api/3/version$" for r in saved["rest"][3]["writeRules"]))
            self.assertTrue(json.loads((root / "collaboration-tools-state.json").read_text())["pending_reload"])
            setup.prepare(root)
            self.assertTrue(json.loads((root / "collaboration-tools-state.json").read_text())["pending_reload"])
            self.assertEqual(len(list((root / "backups").glob("*/config.json"))), 1)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
