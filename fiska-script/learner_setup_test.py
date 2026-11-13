import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("setup_learner", ROOT / "configure-learner.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupTests(unittest.TestCase):
    def test_native_shapes_private_scope_and_idempotent_rerun(self):
        agents, projects, pilots, triggers = [], [], [], []
        writes = []

        def flags(args):
            return {args[i][2:].replace("-", "_"): args[i + 1]
                    for i in range(len(args) - 1) if args[i].startswith("--")}

        def command(*args, **kwargs):
            if kwargs.get("health"):
                return {"daemon_id": "daemon"}
            if args == ("runtime", "list"):
                return [{"id": "runtime", "daemon_id": "daemon", "provider": "kiro",
                         "workspace_id": "workspace", "status": "online"}]
            if args == ("agent", "list"):
                return agents
            if args[:2] in (("agent", "create"), ("agent", "update")):
                value = dict(flags(args), id="agent", max_concurrent_tasks=1)
                agents[:] = [value]
                return value
            if args[:2] == ("agent", "get"):
                return agents[0]
            if args == ("project", "list"):
                return projects
            if args[:2] == ("project", "get"):
                return projects[0]
            if args == ("autopilot", "list"):
                return {"autopilots": pilots}
            if args[:2] in (("autopilot", "create"), ("autopilot", "update")):
                value = dict(flags(args), id="pilot")
                self.assertEqual(value["project"], "project")
                self.assertEqual(value["mode"], "run_only")
                pilots[:] = [value]
                return value
            if args[:2] == ("autopilot", "get"):
                return {"autopilot": pilots[0], "triggers": triggers}
            if args[:2] == ("autopilot", "trigger-add"):
                value = {"id": "trigger", "kind": "webhook", "webhook_path": "/api/webhooks/autopilots/test"}
                triggers.append(value)
                return value
            raise AssertionError(args)

        def native_api(deployment, path, body):
            self.assertEqual(path, "/api/projects")
            self.assertEqual(body["visibility"], "private")
            value = dict(body, id="project")
            projects.append(value)
            return value

        def docker(deployment, *args, **kwargs):
            if "curl" in args:
                return json.dumps({"server_version": "verified"})
            if "cat" in args:
                return json.dumps({"workspace_id": "workspace", "token": "owner-secret"})
            writes.append(args)
            return ""

        architect = SimpleNamespace(command=command,
            unique=lambda rows, name: next((r for r in rows if r["name"] == name), None),
            provision=setup.load("provision-agents"))
        with tempfile.TemporaryDirectory() as directory:
            deployment = Path(directory)
            args = SimpleNamespace(model="installed-model", prepare_only=False)
            with patch.object(setup, "load", return_value=architect), patch.object(setup, "docker", side_effect=docker), \
                    patch.object(setup, "native_api", side_effect=native_api), patch.object(setup, "verify_backend"), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                setup.configure(args, deployment)
                setup.configure(args, deployment)
            self.assertEqual((len(agents), len(projects), len(pilots), len(triggers)), (1, 1, 1, 1))
            self.assertNotIn("owner-secret", output.getvalue())
            self.assertEqual(len(writes), 2)
            self.assertTrue(all("--no-deps" in call and call[-1] == "learner" for call in writes))
            config = deployment / "learner-private/config.json"
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(config.read_text())["project_id"], "project")

    def test_unversioned_backend_refused(self):
        with self.assertRaises(RuntimeError):
            setup.verify_backend("worker-pools")


if __name__ == "__main__":
    unittest.main()
