#!/usr/bin/env python3
"""Run both payment test projects against a private tmpfs MySQL, retaining every result."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid

PROJECTS = [
    "services/FiskaPaymentApiService/FiskaPaymentApiService.Tests/FiskaPaymentApiService.Tests.csproj",
    "services/NuveiCorePlatformAdapter/NuveiCorePlatformAdapter.Test/NuveiCorePlatformAdapter.Test.csproj",
]


def capture(args, **kwargs):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, **kwargs).strip()


def run_suites(repo, output, environment, timeout):
    results = []
    for index, project in enumerate(PROJECTS, 1):
        folder = output / str(index)
        folder.mkdir()
        command = ["dotnet", "test", str(repo / project), "-p:SkipSdkBuild=true",
                   "--logger", "trx;LogFileName=results.trx", "--results-directory", str(folder)]
        started = time.monotonic()
        with (folder / "output.log").open("w") as log:
            process = subprocess.Popen(command, cwd=repo, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
                outcome = "passed" if code == 0 else "failed"
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                code, outcome = 124, "timed_out"
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
        results.append(dict(project=project, command=command, exit_code=code, outcome=outcome,
                            seconds=round(time.monotonic() - started, 1), artifacts=str(folder)))
        (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(f"{project}: {outcome}", flush=True)
    return results


def run(args):
    repo = Path(args.repository).resolve()
    for project in PROJECTS:
        if not (repo / project).is_file():
            raise RuntimeError(f"Required test project missing: {project}")
    for tool in ("docker", "dotnet", "git"):
        if not shutil.which(tool):
            raise RuntimeError(f"Required tool missing: {tool}")
    output = Path(args.output).resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    docker = ["docker", *(["--context", args.docker_context] if args.docker_context else [])]
    name = "fiska-test-mysql-" + uuid.uuid4().hex
    environment = dict(os.environ, MYSQL_ROOT_PASSWORD=secrets.token_hex(24))
    created = False
    try:
        # No existing schema, named volume or shared Compose project is used.
        capture([*docker, "create", "--name", name, "--memory", "2g", "--cpus", "1",
                 "--tmpfs", "/var/lib/mysql:rw,size=1g", "-p", "127.0.0.1::3306",
                 "-e", "MYSQL_ROOT_PASSWORD", "-e", "MYSQL_ROOT_HOST=%", "mysql:8.0",
                 "--skip-log-bin", "--innodb-flush-log-at-trx-commit=0", "--innodb-doublewrite=0",
                 "--sync-binlog=0", "--innodb-flush-method=nosync"], env=environment)
        created = True
        capture([*docker, "start", name])
        address = capture([*docker, "port", name, "3306/tcp"])
        host, port = address.rsplit(":", 1)
        if host != "127.0.0.1" or not port.isdigit():
            raise RuntimeError("Expected a loopback-only MySQL binding.")
        deadline = time.monotonic() + 120
        while True:
            ready = subprocess.run([*docker, "exec", name, "sh", "-c",
                                    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h127.0.0.1 -uroot -Nse "SELECT 1"'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Private MySQL did not become ready within 120 seconds.")
            time.sleep(2)
        environment["FISKA_TEST_MYSQL"] = (
            f"Server=127.0.0.1;Port={port};User ID=root;Password={environment['MYSQL_ROOT_PASSWORD']};SslMode=None")
        environment.pop("MYSQL_ROOT_PASSWORD")
        metadata = dict(candidate=capture(["git", "-C", str(repo), "rev-parse", "HEAD"]),
                        dirty=bool(capture(["git", "-C", str(repo), "status", "--porcelain"])),
                        mysql_image=capture([*docker, "inspect", "--format", "{{.Image}}", name]),
                        docker_context=args.docker_context or capture([*docker, "context", "show"]))
        (output / "environment.json").write_text(json.dumps(metadata, indent=2) + "\n")
        results = run_suites(repo, output, environment, args.timeout)
        return 1 if any(r["exit_code"] for r in results) else 0
    finally:
        if created:
            result = subprocess.run([*docker, "rm", "--force", "--volumes", name],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode:
                raise RuntimeError(f"Could not remove this run's container {name}; clean up that exact container.")


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", help="Candidate checkout; tests use its current contents")
    parser.add_argument("--output", required=True, help="New evidence directory, outside source where possible")
    parser.add_argument("--docker-context", help="Docker daemon sharing this process's loopback, e.g. multica-qa")
    parser.add_argument("--timeout", type=int, default=3600, help="Maximum seconds per complete suite")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    os.umask(0o077)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        return run(args)
    except KeyboardInterrupt:
        print("Interrupted; private MySQL cleanup attempted.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        # Subprocess exceptions can contain environment-derived command values; keep output narrow.
        print(str(error) if isinstance(error, RuntimeError) else type(error).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
