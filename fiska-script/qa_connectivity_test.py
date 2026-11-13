"""Regression tests for stale QA namespaces and non-disruptive recovery."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


qa = load("qa_client_test", "qa-client.py")
repair = load("repair_qa_test", "repair-qa.py")


class NamespaceTests(unittest.TestCase):
    def simulate(self, runtime_ns="net:[2]", qa_ns="net:[1]", active="", changed=False):
        runtime_reads = 0

        def output(cmd, **kwargs):
            nonlocal runtime_reads
            if cmd[-1] == "runtime":
                runtime_reads += 1
                return "runtime-new\n" if changed and runtime_reads > 1 else "runtime-id\n"
            if cmd[-1] == "qa-docker":
                return "qa-id\n"
            if "readlink" in cmd:
                return runtime_ns if "runtime-id" in cmd else qa_ns
            if cmd[-2:] == ["ps", "-q"]:
                return active
            raise AssertionError(cmd)
        return patch.object(qa.subprocess, "check_output", side_effect=output)

    def test_healthy_namespace_never_recreates(self):
        with self.simulate(qa_ns="net:[2]"), patch.object(qa.subprocess, "run") as run:
            qa.ensure_network_namespace()
            run.assert_not_called()

    def test_stale_idle_namespace_recreates_only_qa(self):
        with self.simulate(), patch.object(qa.subprocess, "run") as run:
            qa.ensure_network_namespace()
            command = run.call_args.args[0]
            self.assertEqual(command[-1], "qa-docker")
            self.assertIn("--no-deps", command)
            self.assertIn("--force-recreate", command)
            self.assertIn("--no-build", command)
            self.assertNotIn("down", command)

    def test_stale_namespace_preserves_active_nested_containers(self):
        with self.simulate(active="active-test-id"), patch.object(qa.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "nested containers are running"):
                qa.ensure_network_namespace()
            run.assert_not_called()

    def test_concurrent_runtime_replacement_defers_repair(self):
        with self.simulate(changed=True), patch.object(qa.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Runtime changed"):
                qa.ensure_network_namespace()
            run.assert_not_called()


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "qa-state.json").write_text('{"enabled":true}')
        self.root_patch = patch.object(repair, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def test_stopped_services_are_not_started(self):
        with patch.object(repair, "run", return_value=subprocess.CompletedProcess([], 0, "")), \
             patch.object(repair, "running", side_effect=["runtime-id", None]), \
             patch.object(repair.subprocess, "run") as mutate:
            repair.repair()
            mutate.assert_not_called()

    def test_healthy_endpoint_is_noop(self):
        with patch.object(repair, "run", return_value=subprocess.CompletedProcess([], 0, "healthy")), \
             patch.object(repair, "running", side_effect=["runtime-id", "qa-id"]), \
             patch.object(repair, "reachable", return_value=True), \
             patch.object(repair.subprocess, "run") as mutate:
            repair.repair()
            mutate.assert_not_called()

    def test_unreachable_endpoint_uses_guarded_setup_and_checks_result(self):
        with patch.object(repair, "run", return_value=subprocess.CompletedProcess([], 0, "healthy")), \
             patch.object(repair, "running", side_effect=["runtime-id", "qa-id", "runtime-id"]), \
             patch.object(repair, "reachable", side_effect=[False, True]), \
             patch.object(repair.subprocess, "run") as mutate:
            repair.repair()
            self.assertEqual(mutate.call_count, 1)
            self.assertEqual(Path(mutate.call_args.args[0][-1]).name, "qa-client.py")

    def test_non_rootless_endpoint_is_rejected(self):
        with patch.object(repair, "run", return_value=subprocess.CompletedProcess([], 0, '["name=seccomp"]')):
            self.assertFalse(repair.reachable("runtime-id"))


if __name__ == "__main__":
    unittest.main()
