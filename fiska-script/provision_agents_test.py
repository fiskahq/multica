import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("provision", ROOT / "provision-agents.py")
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "agents.example.json").read_text())

    def test_rejects_duplicate_kiro_references_and_incompatible_login(self):
        duplicate = copy.deepcopy(self.config)
        duplicate["workers"][1]["credential"] = duplicate["workers"][0]["credential"]
        with self.assertRaisesRegex(ValueError, "distinct"):
            provision.validate(duplicate)
        self.config["workers"][0]["credential"] = {"type": "claude_login"}
        with self.assertRaisesRegex(ValueError, "kiro_api_key"):
            provision.validate(self.config)

    def test_read_once_stdin_injection_and_restart_without_secrets(self):
        agents, squads, members, injected, reads = [], [], [], {}, []

        def op_run(args, **kwargs):
            self.assertEqual(args[:3], ["op", "read", "--no-newline"])
            reads.append(args[3])
            return subprocess.CompletedProcess(args, 0, "secret-" + str(len(reads)), "")

        def bridge(*args, data=None):
            if args == ("health",):
                return {"daemon_id": "docker", "active_task_count": 0}
            if args == ("kiro-api-mode",):
                return {}
            if args[:2] == ("runtime", "list"):
                return [{"id": p, "workspace_id": "workspace", "daemon_id": "docker", "provider": p} for p in ("claude", "kiro")]
            if args[:2] == ("agent", "list"):
                return agents
            if args[:2] == ("agent", "create"):
                agent = {"id": str(len(agents)), "name": args[args.index("--name") + 1]}
                agents.append(agent)
                return agent
            if args[:3] == ("agent", "env", "set"):
                self.assertFalse(any("secret-" in arg for arg in args))
                injected[args[3]] = json.loads(data)
                return {}
            if args[:2] == ("squad", "list"):
                return squads
            if args[:2] == ("squad", "create"):
                squad = {"id": "squad", "leader_id": "0", "name": self.config["squad_name"]}
                squads.append(squad)
                return squad
            if args[:3] == ("squad", "member", "list"):
                return members
            if args[:3] == ("squad", "member", "add"):
                members.append({"member_id": args[args.index("--member-id") + 1], "member_type": "agent"})
                return {}
            if args[:3] == ("squad", "pool", "set"):
                self.assertEqual(len(json.loads(data)["members"]), 2)
                self.assertNotIn("secret-", data)
                return {}
            self.fail(f"unexpected command {args}")

        with tempfile.TemporaryDirectory() as tmp, patch.object(provision, "bridge", side_effect=bridge), patch.object(provision.subprocess, "run", side_effect=op_run):
            state = Path(tmp) / "state.json"
            provision.provision(self.config, state)
            self.assertEqual(len(reads), 4)
            self.assertEqual(set(injected["0"]), {"ANTHROPIC_API_KEY", "CODEX_API_KEY"})
            self.assertEqual(set(injected["1"]), {"KIRO_API_KEY"})
            self.assertNotEqual(injected["1"], injected["2"])
            self.assertNotIn("secret-", state.read_text())
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            provision.provision(self.config, state)
            self.assertEqual(len(reads), 4, "rerun must not fetch installed keys again")
            self.assertEqual(len(agents), 3)

    def test_failed_read_does_not_echo_provider_output(self):
        with patch.object(provision.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "secret-value", "secret-value")), patch.object(provision.sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "1Password lookup failed") as error:
                provision.resolve_secret("op://vault/item/key", {})
            self.assertNotIn("secret-value", str(error.exception))


if __name__ == "__main__":
    unittest.main()
