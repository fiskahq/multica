#!/usr/bin/env python3
"""Install the relay stdio adapter and convert this Docker deployment's MCP registrations."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def configure(deployment):
    architect, bridge = module("configure-architect"), module("relay-stdio")
    architect.ROOT = deployment
    command = architect.command
    agents = command("agent", "list")
    workspace_ids = {a["workspace_id"] for a in agents}
    if len(workspace_ids) != 1:
        raise RuntimeError("Expected agents in one selected workspace.")
    workspace_id = workspace_ids.pop()
    servers = command("workspace", "mcp", "list", workspace_id)
    entries = []
    for relative, origin in (("mcp-relay", "http://mcp-relay:8080"),
                             ("mcp-relay/confluence-write", "http://confluence-write:8080")):
        directory = deployment / relative
        if not (directory / "config.json").exists():
            continue
        config = json.loads((directory / "config.json").read_text())
        for endpoint in config.get("http", {}).get("endpoints", []):
            suffix = endpoint["name"].replace("_", "-")
            for prefix in ("factory-", "relay-"):
                server = architect.unique(servers, prefix + suffix)
                if not server:
                    continue
                secret_file = endpoint.get("token", {}).get("file", "")
                if not secret_file.startswith("/run/relay-secrets/"):
                    raise RuntimeError("Expected a local relay endpoint token file.")
                token = (directory / ".secrets" / Path(secret_file).name).read_text().strip()
                if len(token) < 32:
                    raise RuntimeError("Relay endpoint token is invalid.")
                entries.append((server, bridge.server_config(origin + endpoint["path"],
                                                             {"Authorization": "Bearer " + token})))
    if not entries:
        raise RuntimeError("No matching Docker relay registrations found.")
    # Install before changing registrations. This path is also baked into future runtime images.
    script = 'source "$1/common.sh"\nshift\ncompose exec -T runtime python3 "$@"'
    install = ("import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
               "p.parent.mkdir(parents=True, exist_ok=True); "
               "tmp=p.with_suffix('.tmp'); tmp.write_bytes(sys.stdin.buffer.read()); "
               "tmp.chmod(0o644); tmp.replace(p)")
    result = subprocess.run(["bash", "-c", script, "bash", str(deployment), "-c", install, bridge.LIVE_PATH],
                            input=(ROOT / "relay-stdio.py").read_bytes(), capture_output=True)
    if result.returncode:
        raise RuntimeError("Could not install relay adapter in the running runtime.")
    for server, config in entries:
        command("workspace", "mcp", "update", server["id"], workspace_id,
                "--server-config-stdin", data=json.dumps(config))
    saved = command("workspace", "mcp", "list", workspace_id)
    if any(not any(s["id"] == row["id"] and s["transport"] == "stdio" for s in saved)
           for row, _ in entries):
        raise RuntimeError("Relay transport readback failed.")
    print(f"Verified {len(entries)} relay registrations use stdio. No runtime restart.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-dir", type=Path, default=ROOT)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        with (args.deployment_dir / ".operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            configure(args.deployment_dir.resolve())
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"Relay adapter setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
