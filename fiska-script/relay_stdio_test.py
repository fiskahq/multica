import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("relay_stdio", ROOT / "relay-stdio.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ConfigTests(unittest.TestCase):
    def test_secrets_are_in_environment_not_arguments(self):
        config = bridge.server_config("http://mcp-relay:8080/mcp/jira", {"Authorization": "secret"})
        self.assertEqual(config["command"], "python3")
        self.assertNotIn("secret", json.dumps(config["args"]))
        self.assertEqual(json.loads(config["env"]["MULTICA_RELAY_HEADERS"])["Authorization"], "secret")

    def test_invalid_configuration_has_no_secret_diagnostics(self):
        env = dict(os.environ, MULTICA_RELAY_URL="https://secret@example.com", MULTICA_RELAY_HEADERS="{}")
        result = subprocess.run([sys.executable, str(ROOT / "relay-stdio.py")],
                                input="", text=True, capture_output=True, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("secret", result.stderr)

    def test_redirects_never_forward_credentials(self):
        self.assertIsNone(bridge.NoRedirect().redirect_request(None, None, 307, "", {}, "https://elsewhere"))


if __name__ == "__main__":
    unittest.main()
