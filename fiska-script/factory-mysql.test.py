import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("mysql", Path(__file__).with_name("factory-mysql.py"))
mysql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mysql)


class MySQLTest(unittest.TestCase):
    def test_failure_does_not_skip_second_suite_and_preserves_all_results(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            processes = [Mock(pid=41), Mock(pid=42)]
            processes[0].wait.return_value = 1
            processes[1].wait.return_value = 0
            with patch.object(mysql.subprocess, "Popen", side_effect=processes) as popen:
                results = mysql.run_suites(output, output, {}, 30)
            self.assertEqual([r["outcome"] for r in results], ["failed", "passed"])
            self.assertEqual(json.loads((output / "results.json").read_text()), results)
            self.assertEqual(popen.call_count, 2)
            for call in popen.call_args_list:
                self.assertNotIn("--filter", call.args[0])
                self.assertTrue(call.kwargs["start_new_session"])

    def test_timeout_kills_process_group_and_runs_next_suite(self):
        with tempfile.TemporaryDirectory() as directory:
            processes = [Mock(pid=41), Mock(pid=42)]
            processes[0].wait.side_effect = [subprocess.TimeoutExpired("dotnet", 1), -9]
            processes[1].wait.return_value = 0
            with patch.object(mysql.subprocess, "Popen", side_effect=processes), patch.object(mysql.os, "killpg") as kill:
                results = mysql.run_suites(Path(directory), Path(directory), {}, 1)
            kill.assert_called_once_with(41, mysql.signal.SIGKILL)
            self.assertEqual([r["outcome"] for r in results], ["timed_out", "passed"])

    def test_cleanup_is_scoped_even_when_readiness_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for project in mysql.PROJECTS:
                path = root / project
                path.parent.mkdir(parents=True)
                path.touch()
            args = Mock(repository=str(root), output=str(root / "results"), docker_context="multica-qa", timeout=5)
            commands = []
            def capture(command, **kwargs):
                commands.append(command)
                return "0.0.0.0:54321" if "port" in command else "container-id"
            with patch.object(mysql, "capture", side_effect=capture), patch.object(mysql.shutil, "which", return_value="tool"), \
                    patch.object(mysql.subprocess, "run", return_value=Mock(returncode=0)) as run:
                with self.assertRaisesRegex(RuntimeError, "loopback"):
                    mysql.run(args)
            create = commands[0]
            name = create[create.index("--name") + 1]
            self.assertTrue(name.startswith("fiska-test-mysql-"))
            self.assertIn("/var/lib/mysql:rw,size=1g", create)
            self.assertNotIn("-v", create)
            self.assertEqual(run.call_args.args[0], ["docker", "--context", "multica-qa", "rm", "--force", "--volumes", name])


if __name__ == "__main__":
    unittest.main()
