import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("wizard_config", ROOT / "wizard-config.py")
wizard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wizard)


class WizardTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.setup = root / "multica" / "fiska-script"
        self.setup.mkdir(parents=True)
        for name in ["setup.sh", "common.sh", "wizard-config.py", "provision-agents.py", ".env.example"]:
            shutil.copy2(ROOT / name, self.setup / name)
        for name in ["docker-compose.selfhost.yml", "server/internal/handler/worker_pool.go", "server/internal/service/kiro_credential.go"]:
            path = root / "multica" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        self.bin = root / "bin"
        self.bin.mkdir()
        for name, body in {
            "git": "echo v0.8.1-72-gabcdef0-dirty\n",
            "docker": '''printf '%s\\n' "$*" >> "$WIZARD_DOCKER_LOG"
case " $* " in
 *' info --format '*) echo '16 34359738368' ;;
 *' compose version --short '*) echo 'v2.39.0' ;;
esac
''',
        }.items():
            path = self.bin / name
            path.write_text("#!/usr/bin/env bash\nset -eu\n" + body)
            path.chmod(0o755)
        self.log = root / "docker.log"

    def run_dialogue(self, confirmations="yes", prefix=None):
        dialogue = [
            ("Agent runtime CPUs", "bogus"), ("Agent runtime CPUs", "4"),
            ("Agent runtime RAM", "6"), ("Maximum simultaneous", "2"),
            ("Enable QA Docker?", "no"), ("Browser port", "3100"),
            ("API port", "8180"), ("Enable the custom MCP relay?", "no"),
            ("Configure GitHub CLI", "no"), ("Squad name", "Development"),
            ("Claude coordinator name", "Coordinator"), ("Use a Claude subscription", "yes"),
            ("Use a Codex subscription", "yes"), ("Number of Kiro keys", "2"),
            ("Kiro worker 1 key reference", "op://Dev/Kiro one/key"),
            ("Kiro worker 1 capabilities", "code,review"),
            ("Kiro worker 2 key reference", "op://Dev/Kiro two/key"),
            ("Kiro worker 2 capabilities", "qa"),
            ("Save this configuration", confirmations),
        ]
        master, slave = pty.openpty()
        env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "WIZARD_DOCKER_LOG": str(self.log)}
        command = (prefix or ["bash"]) + [str(self.setup / "setup.sh"), "--configure-only"]
        child = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, env=env)
        os.close(slave)
        transcript = ""
        pending = ""
        deadline = time.monotonic() + 25
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536).decode()
                    except OSError:
                        break
                    if not chunk:
                        break
                    transcript += chunk
                    pending += chunk
                    if dialogue and dialogue[0][0] in pending:
                        _, answer = dialogue.pop(0)
                        os.write(master, (answer + "\n").encode())
                        pending = ""
                elif child.poll() is not None:
                    break
            if child.poll() is None:
                child.wait(timeout=2)
            self.assertEqual(child.returncode, 0, transcript)
            self.assertFalse(dialogue, transcript)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait()
            os.close(master)
        return transcript

    def test_interactive_configuration_and_invalid_input(self):
        output = self.run_dialogue()
        self.assertIn("Enter a whole number", output)
        agents = json.loads((self.setup / "agents.json").read_text())
        self.assertEqual(agents["main"]["credentials"], [{"type": "claude_login"}, {"type": "codex_login"}])
        self.assertEqual(agents["workers"][1]["capabilities"], ["qa"])
        values = wizard.read_env(self.setup / ".env")
        self.assertEqual(values["RUNTIME_CPUS"], "4")
        self.assertEqual(len(values["POSTGRES_PASSWORD"]), 64)
        for name in [".env", "agents.json", "wizard-state.json"]:
            self.assertEqual((self.setup / name).stat().st_mode & 0o777, 0o600)
        self.assertNotIn(" build ", self.log.read_text())
        self.assertNotIn(" up ", self.log.read_text())

    def test_cancel_does_not_write_configuration(self):
        self.run_dialogue("no")
        self.assertFalse((self.setup / ".env").exists())
        self.assertFalse((self.setup / "agents.json").exists())

    def test_preserves_existing_secrets_and_agents(self):
        self.run_dialogue()
        original = (self.setup / "agents.json").read_bytes()
        values = wizard.read_env(self.setup / ".env")
        with (self.setup / ".env").open("a") as stream:
            stream.write("CUSTOM_SETTING='keep me'\n")
        answers = json.loads((self.setup / "wizard-state.json").read_text())
        answers["runtime_memory"] = 8
        with patch.object(wizard, "ROOT", self.setup):
            wizard.write_configuration(answers)
        after = wizard.read_env(self.setup / ".env")
        self.assertEqual(after["POSTGRES_PASSWORD"], values["POSTGRES_PASSWORD"])
        self.assertEqual(after["JWT_SECRET"], values["JWT_SECRET"])
        self.assertEqual(after["CUSTOM_SETTING"], "keep me")
        self.assertEqual(after["RUNTIME_MEMORY_LIMIT"], "8g")
        self.assertEqual((self.setup / "agents.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
