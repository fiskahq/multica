import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("relay_import", Path(__file__).with_name("import-relay.py"))
relay_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay_import)


class RelayImportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.setup = root / "setup"
        self.target = self.setup / "mcp-relay"
        self.target.mkdir(parents=True)
        self.source = root / "original"
        (self.source / ".secrets").mkdir(parents=True)
        (self.source / "data").mkdir()
        (self.source / "config.json").write_text('{"test":true}')
        (self.source / ".secrets/token").write_text("fixture-token")
        (self.source / "data/mail.sqlite").write_bytes(b"fixture database")
        self.details = {"State": {"Running": False}, "Mounts": [
            {"Source": str(self.source / name), "Destination": destination}
            for name, destination in [("config.json", "/app/config.json"),
                                      (".secrets", "/run/relay-secrets"), ("data", "/data")]
        ]}

    def run_import(self):
        with patch.object(relay_import, "__file__", str(self.setup / "import-relay.py")), \
             patch("sys.argv", ["import-relay.py", "--source", str(self.source), "--container", "fixture"]), \
             patch.object(relay_import.subprocess, "check_output", return_value=json.dumps([self.details])):
            relay_import.main()

    def test_import_preserves_source_and_data_with_private_permissions(self):
        self.run_import()
        for relative in ["config.json", ".secrets/token", "data/mail.sqlite"]:
            self.assertEqual((self.source / relative).read_bytes(), (self.target / relative).read_bytes())
            self.assertEqual((self.target / relative).stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.target / ".secrets").stat().st_mode & 0o777, 0o700)
        with self.assertRaisesRegex(SystemExit, "already exists"):
            self.run_import()

    def test_running_source_is_rejected_before_copy(self):
        self.details["State"]["Running"] = True
        with self.assertRaisesRegex(SystemExit, "Stop the source"):
            self.run_import()
        self.assertEqual(list(self.target.iterdir()), [])

    def test_wrong_mount_is_rejected_before_copy(self):
        self.details["Mounts"][0]["Source"] = "/different/config.json"
        with self.assertRaisesRegex(SystemExit, "does not match"):
            self.run_import()
        self.assertEqual(list(self.target.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
