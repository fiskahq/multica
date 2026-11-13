#!/usr/bin/env python3
"""Provision native Multica agents and pools. Never persist resolved secrets locally."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
ENV_KEYS = {"anthropic_api_key": "ANTHROPIC_API_KEY", "codex_api_key": "CODEX_API_KEY", "kiro_api_key": "KIRO_API_KEY"}
LOGINS = {"claude_login": ["claude", "auth", "login"], "codex_login": ["codex", "login", "--device-auth"]}
LABEL = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


def agent_concurrency(name, default=None):
    policy = json.loads((ROOT / "concurrency-policy.json").read_text())
    limit = policy["agents"].get(name, policy["default_agent_limit"] if default is None else default)
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError(f"Invalid concurrency limit for {name}")
    return limit


def validate(config):
    if not isinstance(config, dict) or not isinstance(config.get("main"), dict):
        raise ValueError("main must be an object")
    if not LABEL.fullmatch(config.get("pool_name", "kiro")):
        raise ValueError("invalid pool_name")
    if not isinstance(config.get("squad_name"), str) or not config["squad_name"].strip():
        raise ValueError("squad_name is required")
    workers = config.get("workers")
    if not isinstance(workers, list) or not 1 <= len(workers) <= 64:
        raise ValueError("workers must contain 1 to 64 entries")
    names, references = set(), set()
    for spec in [config["main"], *workers]:
        if not isinstance(spec, dict):
            raise ValueError("each agent must be an object")
        name = spec.get("name", "")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("each agent needs a unique name")
        names.add(name)
        if not isinstance(spec.get("instructions", ""), str):
            raise ValueError("instructions must be a string")
        credentials = spec.get("credentials", []) if spec is config["main"] else [spec.get("credential", {})]
        if not isinstance(credentials, list):
            raise ValueError("credentials must be an array")
        if spec is config["main"] and not credentials:
            raise ValueError("main.credentials must configure Claude and Codex via API keys or CLI login")
        kinds = set()
        for credential in credentials:
            if not isinstance(credential, dict):
                raise ValueError("credentials must be objects")
            kind = credential.get("type")
            if kind not in ENV_KEYS and kind not in LOGINS:
                raise ValueError("unsupported credential type; use anthropic_api_key, codex_api_key, kiro_api_key, claude_login or codex_login")
            if kind in kinds:
                raise ValueError("duplicate credential type")
            kinds.add(kind)
            if "reuse_existing" in credential and (kind not in LOGINS or not isinstance(credential["reuse_existing"], bool)):
                raise ValueError("reuse_existing is a boolean supported only for CLI login credentials")
            if kind in ENV_KEYS:
                ref = credential.get("reference", "")
                if not isinstance(ref, str) or not ref.startswith("op://") or "\n" in ref:
                    raise ValueError("API credentials require an op:// reference, never a raw key")
                if kind == "kiro_api_key":
                    if ref in references:
                        raise ValueError("each Kiro worker must use a distinct key reference")
                    references.add(ref)
            elif credential.get("reference"):
                raise ValueError("CLI login credentials do not accept secret references")
        if spec is config["main"]:
            if len(kinds & {"anthropic_api_key", "claude_login"}) != 1 or len(kinds & {"codex_api_key", "codex_login"}) != 1 or "kiro_api_key" in kinds:
                raise ValueError("main requires exactly one Claude and one Codex authentication method")
        elif kinds != {"kiro_api_key"}:
            raise ValueError("pooled Kiro workers require kiro_api_key credentials")
        caps = spec.get("capabilities", ["code"])
        if not isinstance(spec.get("enabled", True), bool):
            raise ValueError("enabled must be a boolean")
        if not isinstance(caps, list) or len(caps) > 32 or any(not isinstance(c, str) or not LABEL.fullmatch(c) for c in caps) or len(set(caps)) != len(caps):
            raise ValueError("capabilities must be unique lowercase labels (maximum 32)")


def bridge(*args, data=None):
    result = subprocess.run([str(ROOT / "agent-cli.sh"), *args], input=data, text=True, capture_output=True)
    if result.returncode:
        # CLI errors and provider subprocess output can contain credential material.
        raise RuntimeError(f"Multica command failed: {' '.join(args[:2])}. Check runtime health and workspace access.")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("Multica returned an invalid JSON response") from None


def write_state(path, state):
    fd, name = tempfile.mkstemp(prefix=".agent-state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def credential_fingerprint(credentials):
    return hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest()


def resolve_secret(reference, cache):
    if reference in cache:
        return cache[reference]
    try:
        result = subprocess.run(["op", "read", "--no-newline", reference], text=True, capture_output=True)
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0 and result.stdout:
        value = result.stdout
    elif sys.stdin.isatty():
        print("1Password could not resolve a key. Unlock/sign in to 1Password and rerun, or paste the API key below.")
        value = getpass.getpass("API key (hidden; empty cancels): ")
    else:
        raise RuntimeError("1Password lookup failed. Run op signin, then rerun setup-agents.sh in a terminal.")
    if not value or "\n" in value or "\x00" in value:
        raise RuntimeError("API key must be a nonempty single-line value")
    cache[reference] = value
    return value


def provision(config, state_path):
    state = json.loads(state_path.read_text()) if state_path.exists() else {"agents": {}}
    health = bridge("health")
    if health.get("active_task_count", 0):
        raise RuntimeError("Wait for active tasks to finish before provisioning agents")
    print("Clearing the shared Kiro browser session so each worker uses its injected API key.")
    bridge("kiro-api-mode")
    daemon_id = health["daemon_id"]
    runtimes = []
    for _ in range(30):
        runtimes = [r for r in bridge("runtime", "list", "--output", "json") if r.get("daemon_id") == daemon_id]
        if {"claude", "kiro"} <= {r.get("provider") for r in runtimes}:
            break
        time.sleep(2)
    runtime_ids = {}
    for provider in ("claude", "kiro"):
        matches = [r for r in runtimes if r.get("provider") == provider and not r.get("profile_id")]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one built-in {provider} runtime on this Docker daemon")
        runtime_ids[provider] = matches[0]["id"]
    workspace_id = runtimes[0]["workspace_id"]
    if state.get("workspace_id", workspace_id) != workspace_id:
        raise RuntimeError("Saved agent state belongs to another workspace. Use a separate setup directory.")
    state["workspace_id"] = workspace_id
    existing = bridge("agent", "list", "--output", "json")
    cache = {}
    ids = []
    for index, spec in enumerate([config["main"], *config["workers"]]):
        name = spec["name"]
        provider = "claude" if index == 0 else "kiro"
        credentials = spec["credentials"] if index == 0 else [spec["credential"]]
        entry = state["agents"].get(name, {})
        agent_id = entry.get("id")
        if agent_id and not any(a["id"] == agent_id for a in existing):
            raise RuntimeError(f"Managed agent {name} is missing or archived. Restore it before provisioning.")
        if not agent_id:
            if any(a["name"] == name for a in existing):
                raise RuntimeError(f"Agent {name} exists outside this installer state. Choose another name.")
            instructions = spec.get("instructions", "")
            if index == 0:
                instructions += "\nCoordinate the squad using its worker pool. Codex CLI is available as a tool via codex exec; Claude is your primary provider. Delegate implementation to the pool and review results."
            extra = ["--custom-args", json.dumps(["--agent-engine", "v3"])] if provider == "kiro" else []
            limit = agent_concurrency(name, default=10 if index == 0 else 1)
            agent = bridge("agent", "create", "--name", name, "--runtime-id", runtime_ids[provider], "--instructions", instructions, "--max-concurrent-tasks", str(limit), *extra, "--output", "json")
            agent_id = agent["id"]
            entry = {"id": agent_id}
            state["agents"][name] = entry
            write_state(state_path, state)
        fingerprint = credential_fingerprint(credentials)
        if entry.get("credentials") != fingerprint:
            env = {}
            for credential in credentials:
                kind = credential["type"]
                if kind in LOGINS:
                    if credential.get("reuse_existing", False):
                        bridge("auth-status", kind)
                        continue
                    if not sys.stdin.isatty():
                        raise RuntimeError("Provider login requires a terminal. Rerun setup-agents.sh interactively.")
                    print(f"Complete {kind} in the container. Open its URL in your browser and follow the CLI instructions.")
                    subprocess.run([str(ROOT / "agent-cli.sh"), "interactive", *LOGINS[kind]], check=True)
                else:
                    env[ENV_KEYS[kind]] = resolve_secret(credential["reference"], cache)
            bridge("agent", "env", "set", agent_id, "--custom-env-stdin", "--output", "json", data=json.dumps(env))
            entry["credentials"] = fingerprint
            write_state(state_path, state)
        ids.append(agent_id)
    squads = bridge("squad", "list", "--output", "json")
    squad_id = state.get("squad_id")
    if not squad_id:
        if any(s["name"] == config["squad_name"] for s in squads):
            raise RuntimeError("Squad name already exists outside installer state")
        squad_id = bridge("squad", "create", "--name", config["squad_name"], "--leader", ids[0], "--output", "json")["id"]
        state["squad_id"] = squad_id
        write_state(state_path, state)
    elif not any(s["id"] == squad_id and s["leader_id"] == ids[0] for s in squads):
        raise RuntimeError("Managed squad is missing or its leader changed")
    members = bridge("squad", "member", "list", squad_id, "--output", "json")
    for agent_id in ids[1:]:
        if not any(m["member_id"] == agent_id and m["member_type"] == "agent" for m in members):
            bridge("squad", "member", "add", squad_id, "--member-id", agent_id, "--type", "agent", "--role", "Kiro worker", "--output", "json")
    body = {"members": [{"agent_id": agent_id, "capabilities": spec.get("capabilities", ["code"]), "enabled": spec.get("enabled", True)} for agent_id, spec in zip(ids[1:], config["workers"])]}
    # The pool file contains IDs and labels only. Send it through stdin into the container.
    bridge("squad", "pool", "set", squad_id, config.get("pool_name", "kiro"), "--file", "/dev/stdin", data=json.dumps(body))
    print(f"Configured Claude leader and {len(ids)-1} Kiro workers in squad {squad_id}.")
    print("Assign an issue to this squad in the browser. Provider authentication has not been tested with a paid model request.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        config = json.loads(args.config.read_text())
        validate(config)
        if not args.validate:
            provision(config, ROOT / "agent-state.json")
    except (ValueError, KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
