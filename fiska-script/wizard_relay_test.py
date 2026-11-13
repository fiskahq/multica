import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("relay_wizard", Path(__file__).with_name("wizard-relay.py"))
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class RelayWizardTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "mcp-relay").mkdir()
        self.patch = patch.object(relay, "ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def prepare_jira(self):
        with patch.object(relay, "yesno", side_effect=[True, False]), \
             patch.object(relay, "prompt", side_effect=["https://example.atlassian.net", "dev@example.test", "op://Dev/Jira/key"]), \
             patch.object(relay.provision, "resolve_secret", return_value="fixture-secret") as resolve:
            relay.prepare()
            resolve.assert_called_once_with("op://Dev/Jira/key", {})

    def test_scoped_configuration_is_private_and_preserved(self):
        self.prepare_jira()
        config_path = self.root / "mcp-relay/config.json"
        config = json.loads(config_path.read_text())
        self.assertFalse(config["jira"]["allowWrites"])
        self.assertFalse(config["rest"][0]["allowWrites"])
        self.assertNotIn("fixture-secret", config_path.read_text())
        secret_files = list((self.root / "mcp-relay/.secrets").iterdir())
        for path in secret_files + [config_path]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        tokens = [(self.root / "mcp-relay/.secrets" / Path(e["token"]["file"]).name).read_text()
                  for e in config["http"]["endpoints"]]
        self.assertEqual(len(set(tokens)), 2)
        before = config_path.read_bytes()
        with patch.object(relay, "yesno", side_effect=AssertionError("Existing config must not prompt")):
            relay.prepare()
        self.assertEqual(config_path.read_bytes(), before)

    def test_registration_uses_stdin_and_reuses_ids(self):
        self.prepare_jira()
        (self.root / "agents.json").write_text(json.dumps({"main": {"name": "Coordinator"}, "workers": [{"name": "Worker"}]}))
        (self.root / "agent-state.json").write_text(json.dumps({"workspace_id": "ws", "agents": {"Coordinator": {"id": "main"}, "Worker": {"id": "worker"}}}))
        servers = []
        calls = []

        def bridge(*args, data=None):
            calls.append(args)
            if args[:3] == ("workspace", "mcp", "list"):
                return list(servers)
            if args[:3] == ("workspace", "mcp", "add"):
                self.assertIsNotNone(data)
                payload = json.loads(data)
                self.assertTrue(payload["url"].startswith("http://mcp-relay:8080/mcp/"))
                token = payload["headers"]["Authorization"].split()[1]
                self.assertNotIn(token, repr(args))
                servers.append({"id": str(len(servers)), "name": args[3]})
            return {}

        with patch.object(relay, "yesno", return_value=True), patch.object(relay, "prompt", return_value="coordinator"), \
             patch.object(relay.provision, "bridge", side_effect=bridge):
            relay.register()
            relay.register()
        self.assertEqual(len(servers), 2)
        assignments = [a for a in calls if a[:3] == ("agent", "mcp", "add")]
        self.assertEqual({a[3] for a in assignments}, {"main"})
        self.assertEqual(len([a for a in calls if a[:3] == ("workspace", "mcp", "update")]), 2)


if __name__ == "__main__":
    unittest.main()
