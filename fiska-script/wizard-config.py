#!/usr/bin/env python3
"""Validate and persist non-secret wizard answers; never source a user's .env."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import sys
import tempfile

ROOT = Path(__file__).resolve().parent


def provision_module():
    spec = importlib.util.spec_from_file_location("provision", ROOT / "provision-agents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if not re.match(r"^[A-Z][A-Z0-9_]*=", line):
                continue
            key, value = line.split("=", 1)
            parts = shlex.split(value, comments=True)
            values[key] = parts[0] if len(parts) == 1 else value
    return values


def atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(prefix=".wizard-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_configuration(answers):
    integer_ranges = {"runtime_cpus": (1, 256), "runtime_memory": (2, 2048),
                      "concurrency": (1, 64), "qa_cpus": (1, 256), "qa_memory": (2, 2048),
                      "frontend_port": (1024, 65535), "backend_port": (1024, 65535),
                      "relay_port": (1024, 65535)}
    for name, (minimum, maximum) in integer_ranges.items():
        if type(answers.get(name)) is not int or not minimum <= answers[name] <= maximum:
            raise ValueError(f"Invalid {name}")
    ports = [answers["frontend_port"], answers["backend_port"]]
    if answers["relay"]:
        ports.append(answers["relay_port"])
    if len(set(ports)) != len(ports):
        raise ValueError("App, API and relay ports must be distinct")
    for key in ["qa", "relay", "github"]:
        if type(answers.get(key)) is not bool:
            raise ValueError(f"Invalid {key}")
    agents_file = ROOT / "agents.json"
    agents = json.loads(agents_file.read_text()) if agents_file.exists() else answers["agents"]
    provision_module().validate(agents)
    path = ROOT / ".env"
    text = path.read_text() if path.exists() else (ROOT / ".env.example").read_text()
    values = read_env(path)
    updates = {
        "RUNTIME_CPUS": str(answers["runtime_cpus"]),
        "RUNTIME_MEMORY_LIMIT": f'{answers["runtime_memory"]}g',
        "RUNTIME_MAX_CONCURRENT_TASKS": str(answers["concurrency"]),
        "QA_DOCKER_CPUS": str(answers["qa_cpus"]),
        "QA_DOCKER_MEMORY_LIMIT": f'{answers["qa_memory"]}g',
        "FRONTEND_PORT": str(answers["frontend_port"]),
        "BACKEND_PORT": str(answers["backend_port"]),
        "FRONTEND_ORIGIN": f'http://localhost:{answers["frontend_port"]}',
        "MULTICA_APP_URL": f'http://localhost:{answers["frontend_port"]}',
        "RELAY_HTTP_PORT": str(answers["relay_port"]),
    }
    # Match the owner of private relay bind mounts on Linux and macOS.
    if not (ROOT / "mcp-relay/config.json").exists():
        updates.update(RELAY_UID=str(os.getuid()), RELAY_GID=str(os.getgid()))
    for key in ["POSTGRES_PASSWORD", "JWT_SECRET"]:
        if not values.get(key) or values[key] == "GENERATE_ON_INSTALL":
            updates[key] = secrets.token_hex(32)
    lines = [line for line in text.splitlines() if line.split("=", 1)[0] not in updates]
    lines.extend(f"{key}={value}" for key, value in updates.items())
    atomic_write(path, "\n".join(lines) + "\n")
    if not agents_file.exists():
        atomic_write(agents_file, json.dumps(agents, indent=2) + "\n")
    # Keep only user preferences; authentication happens later via the provider CLIs.
    atomic_write(ROOT / "wizard-state.json", json.dumps(answers, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["read", "write"])
    parser.add_argument("key", nargs="?")
    parser.add_argument("default", nargs="?", default="")
    args = parser.parse_args()
    if args.action == "read":
        print(read_env(ROOT / ".env").get(args.key, args.default))
    else:
        write_configuration(json.load(sys.stdin))


if __name__ == "__main__":
    main()
