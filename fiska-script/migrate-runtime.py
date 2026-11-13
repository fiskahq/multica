#!/usr/bin/env python3
"""Import local runtime state after migrate-local.py, without starting agent tasks."""
import argparse
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--ssh-identity", action="append", default=[])
    parser.add_argument("--factory-dir", type=Path)
    parser.add_argument("--relay-origin", default="http://mcp-relay:8080")
    args = parser.parse_args()
    os.umask(0o077)
    lock = (ROOT / ".operation.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state = json.loads((ROOT / "migration-state.json").read_text())
    if state["phase"] != "app-ready-runtime-setup-pending":
        raise RuntimeError("Finish the application migration before importing runtime state")
    spec = importlib.util.spec_from_file_location("migration", ROOT / "migrate-local.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    compose = migration.compose_command(state["target_project"])
    if subprocess.check_output(compose + ["ps", "--status", "running", "-q", "runtime"], text=True).strip():
        raise RuntimeError("Stop the runtime before importing credentials and sessions")
    source_home = Path.home()
    config = json.loads((source_home / ".multica/profiles" / args.profile / "config.json").read_text())
    config.update(server_url="http://backend:8080", app_url="http://localhost:3100")
    backup = Path(state["backup"])
    archive = backup / "runtime-home.tar"
    if archive.exists():
        raise RuntimeError("Runtime archive already exists; inspect it before resuming")
    with tarfile.open(archive, "w") as tar:
        def add_bytes(name, value):
            data = value.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uid, info.gid = len(data), 0o600, 1000, 1000
            tar.addfile(info, io.BytesIO(data))

        def add_path(path, name):
            if path.exists():
                tar.add(path, arcname=name)

        add_bytes(".multica/config.json", json.dumps(config))
        add_bytes(f".multica/profiles/{args.profile}/config.json", json.dumps(config))
        for name in [".multica/daemon.id", ".multica/pi-sessions", ".claude/.credentials.json",
                     ".claude/projects", ".codex/auth.json", ".codex/sessions",
                     ".local/share/opencode/auth.json", ".ssh/known_hosts"]:
            add_path(source_home / name, name)
        claude_config = source_home / ".claude.json"
        if claude_config.exists():
            original = json.loads(claude_config.read_text())
            # Retain the account/onboarding state without importing host hooks or MCP commands.
            keep = {k: v for k, v in original.items() if k in
                    ["oauthAccount", "hasCompletedOnboarding", "lastOnboardingVersion", "userID", "claudeCodeFirstTokenDate"]}
            add_bytes(".claude.json", json.dumps(keep))
        for identity in args.ssh_identity:
            if Path(identity).name != identity:
                raise RuntimeError("SSH identities must be filenames within ~/.ssh")
            add_path(source_home / ".ssh" / identity, ".ssh/" + identity)
        if args.ssh_identity:
            # Config contains host aliases and paths, not private key values.
            add_path(source_home / ".ssh/config", ".ssh/config")
        git_config = []
        result = subprocess.run(["git", "config", "--global", "--null", "--list"], capture_output=True, text=True)
        for entry in result.stdout.split("\0"):
            key, _, value = entry.partition("\n")
            if key in ["user.name", "user.email"] or key.startswith("url."):
                git_config.append((key, value))
        add_bytes(".multica/migrated-git-settings.json", json.dumps(git_config))
        kiro_db = source_home / ".local/share/kiro-cli/data.sqlite3"
        if kiro_db.exists():
            snapshot = backup / "kiro-sessions.sqlite3"
            with sqlite3.connect(f"file:{kiro_db}?mode=ro", uri=True) as source, sqlite3.connect(snapshot) as target:
                source.backup(target)
            add_path(snapshot, ".local/share/kiro-cli/data.sqlite3")
        if args.factory_dir:
            factory = args.factory_dir.resolve()
            for path in (factory / "multica-setup").rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                name = "migrated/software-factory/" + str(path.relative_to(factory))
                if path.suffix in [".json", ".py", ".md"]:
                    text = path.read_text().replace("http://localhost:8081", "http://backend:8080")
                    text = text.replace("http://127.0.0.1:8090", args.relay_origin)
                    text = text.replace(str(factory), "/home/node/migrated/software-factory")
                    add_bytes(name, text)
                else:
                    add_path(path, name)
            for endpoint in ["jira", "jira-triage"]:
                relative = f"mcp-relay/.secrets/endpoint-{endpoint}-token"
                add_path(factory / relative, "migrated/software-factory/" + relative)
    print("Importing provider login state, session history, and selected repository credentials.", flush=True)
    with archive.open("rb") as stream:
        subprocess.run(compose + ["run", "--rm", "-T", "--no-deps", "runtime", "tar", "--no-same-owner",
                                  "-C", "/home/node", "-xpf", "-"], stdin=stream, check=True)
    script = """import json,pathlib,subprocess
home=pathlib.Path.home()
for key,value in json.loads((home/'.multica/migrated-git-settings.json').read_text()):
    subprocess.run(['git','config','--global',key,value],check=True)
for path in [home/'.ssh',home/'.claude',home/'.codex',home/'.multica']:
    if path.exists(): path.chmod(0o700)
"""
    subprocess.run(compose + ["run", "--rm", "-T", "--no-deps", "runtime", "python3", "-c", script], check=True)
    # Discover/watch the imported workspaces through the normal login flow.
    subprocess.run(compose + ["run", "--rm", "-T", "--no-deps", "runtime", "multica", "login", "--token="],
                   input=(config["token"] + "\n").encode(), check=True, stdout=subprocess.DEVNULL)
    if args.factory_dir:
        old = str(args.factory_dir.resolve()).replace("'", "''")
        new = "/home/node/migrated/software-factory"
        query = "BEGIN;"
        for table, column in [("skill", "content"), ("skill_file", "content"),
                              ("agent", "instructions"), ("squad", "instructions")]:
            query += f"UPDATE {table} SET {column}=replace({column},'{old}','{new}') WHERE strpos({column},'{old}')>0;"
        migration.sql(state["target_project"] + "-postgres-1", "multica", "multica", query + "COMMIT;")
    state["phase"] = "runtime-imported"
    migration.write_json(ROOT / "migration-state.json", state)
    print("Runtime state imported. Start the Docker runtime, then provision its worker pool.", flush=True)


if __name__ == "__main__":
    main()
