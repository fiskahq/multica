#!/usr/bin/env python3
"""Repair QA Docker connectivity after runtime or host restarts without restarting agents."""
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("qa_client", ROOT / "qa-client.py")
qa_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa_client)


def run(args, check=True, timeout=25):
    return subprocess.run(args, check=check, text=True, capture_output=True, timeout=timeout)


def running(service):
    ids = run(qa_client.compose("ps", "--status", "running", "-q", service)).stdout.split()
    if len(ids) > 1:
        raise RuntimeError(f"Multiple {service} containers found; refusing automatic repair.")
    return ids[0] if ids else None


def reachable(runtime):
    try:
        result = run(["docker", "exec", "-w", "/home/node", runtime, "docker", "--context",
                      "multica-qa", "info", "--format", "{{json .SecurityOptions}}"], check=False, timeout=12)
        return result.returncode == 0 and "name=rootless" in json.loads(result.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        return False


def repair():
    if not (ROOT / "qa-state.json").exists():
        return
    if run(["docker", "info"], check=False).returncode:
        return
    runtime, qa = running("runtime"), running("qa-docker")
    # Respect intentionally stopped services. Normal setup/start handles their creation.
    if not runtime or not qa:
        return
    health = run(["docker", "inspect", "--format", "{{.State.Health.Status}}", runtime]).stdout.strip()
    if health != "healthy" or reachable(runtime):
        return
    print("Refreshing QA Docker TLS credentials and context.", flush=True)
    # qa-client verifies namespaces and refuses to replace QA with active nested containers.
    subprocess.run([sys.executable, str(ROOT / "qa-client.py")], check=True, timeout=330)
    if running("runtime") != runtime or not reachable(runtime):
        raise RuntimeError("QA endpoint is still unreachable from the runtime after repair.")
    print("Verified rootless QA Docker at tcp://127.0.0.1:2376. Runtime was not restarted.")


def main():
    with (ROOT / ".operation.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        repair()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        # Avoid dumping Docker configuration or subprocess output, which can contain credentials.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f"QA connectivity repair failed: {message}", file=sys.stderr)
        sys.exit(1)
