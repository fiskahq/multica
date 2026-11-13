#!/usr/bin/env python3
"""Move an idle local self-hosted instance into a new sandbox deployment."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parent


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def sql(container, user, database, query):
    return subprocess.check_output(
        ["docker", "exec", "-i", container, "psql", "-X", "-At", "-v", "ON_ERROR_STOP=1",
         "-U", user, "-d", database], input=query.encode()).decode().strip()


def fingerprints(container, user, database):
    tables = sql(container, user, database,
                 "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
    result = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        value = sql(container, user, database,
                    f"SELECT json_build_object('rows',count(*),'digest',"
                    f"md5(coalesce(string_agg(h,'' ORDER BY h),''))) "
                    f"FROM (SELECT md5(row_to_json(t)::text) h FROM {quoted} t) r")
        result[table] = json.loads(value)
    return result


def write_json(path, value):
    with path.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def dotenv(value):
    return "'" + value.replace("'", "\\'") + "'"


def compose_command(project):
    return ["docker", "compose", "--project-name", project, "--project-directory", str(ROOT),
            "--env-file", str(ROOT / ".env"), "-f", str(ROOT.parent / "docker-compose.selfhost.yml"),
            "-f", str(ROOT / "compose.sandbox.yml"), "-f", str(ROOT / "compose.local.yml")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-restored", action="store_true", help="Resume only after an exact database restore was verified")
    parser.add_argument("--source-backend", required=True)
    parser.add_argument("--source-frontend", required=True)
    parser.add_argument("--source-postgres", required=True)
    parser.add_argument("--workspaces", type=Path, required=True)
    parser.add_argument("--relay-network")
    parser.add_argument("--relay-origin")
    parser.add_argument("--old-relay-origin", default="http://127.0.0.1:8090")
    args = parser.parse_args()
    os.umask(0o077)
    lock = (ROOT / ".operation.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.resume_restored:
        state = json.loads((ROOT / "migration-state.json").read_text())
        backup = Path(state["backup"])
        project = state["target_project"]
        if state["phase"] != "source-stopped" or state["source_workspaces"] != str(args.workspaces):
            raise RuntimeError("Cannot resume this migration phase")
        if any(inspect(state[k])["State"]["Running"] for k in ["source_backend", "source_frontend"]):
            raise RuntimeError("Source app must remain stopped")
        before = json.loads((backup / "source-fingerprints.json").read_text())
        if fingerprints(project + "-postgres-1", "multica", "multica") != before:
            raise RuntimeError("Restored database changed; automatic resume is unsafe")
        finish_data_copy(args, compose_command(project), project, backup, state)
        return
    if (ROOT / "migration-state.json").exists():
        raise RuntimeError("A migration already started. Inspect migration-state.json before recovery.")
    if not args.workspaces.is_dir() or not args.workspaces.is_absolute():
        raise RuntimeError("--workspaces must be an existing absolute directory")
    if args.relay_network and not args.relay_origin:
        raise RuntimeError("Supply --relay-origin when using an external --relay-network")
    project = os.environ.get("MULTICA_PROJECT_NAME", "multica-sandbox")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise RuntimeError("Invalid target project name")
    source = inspect(args.source_backend)
    if source["Config"].get("Labels", {}).get("com.docker.compose.project") == project:
        raise RuntimeError("Source and target projects must differ")
    source_env = dict(x.split("=", 1) for x in source["Config"]["Env"] if "=" in x)
    db_url = urlsplit(source_env["DATABASE_URL"])
    source_user, source_db = unquote(db_url.username), unquote(db_url.path.lstrip("/"))
    active_query = "SELECT count(*) FROM agent_task_queue WHERE status NOT IN ('completed','failed','cancelled')"
    if sql(args.source_postgres, source_user, source_db, active_query) != "0":
        raise RuntimeError("Source has unfinished tasks. Wait for completion before migration.")
    if (ROOT / ".env").exists() or (ROOT / "compose.local.yml").exists():
        raise RuntimeError("Migration requires a new target without .env or compose.local.yml")
    existing = subprocess.check_output(["docker", "volume", "ls", "--format", "{{.Name}}"], text=True).splitlines()
    if any(v.startswith(project + "_") for v in existing):
        raise RuntimeError("Target volumes already exist; refusing to overwrite data")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ROOT / "backups" / ("migration-" + stamp)
    backup.mkdir(parents=True, mode=0o700)
    write_json(backup / "source-environment.json", source_env)
    write_json(backup / "source-containers.json", {
        name: {"restart_policy": inspect(name)["HostConfig"]["RestartPolicy"]}
        for name in [args.source_backend, args.source_frontend]})
    env = {k: v for k, v in source_env.items() if k not in {"DATABASE_URL", "DATABASE_REPLICA_URL", "PATH", "PORT", "HOSTNAME"}}
    env.update(POSTGRES_USER="multica", POSTGRES_DB="multica", POSTGRES_PASSWORD=secrets.token_hex(32),
               FRONTEND_PORT="3100", BACKEND_PORT="8180", FRONTEND_ORIGIN="http://localhost:3100",
               MULTICA_APP_URL="http://localhost:3100", GOOGLE_REDIRECT_URI="http://localhost:3100/auth/callback",
               MULTICA_IMAGE_TAG="latest", DO_NOT_TRACK="1", RUNTIME_MAX_CONCURRENT_TASKS="1")
    with (ROOT / ".env").open("w") as stream:
        stream.write("# Migrated instance settings. Contains secrets.\n")
        for key, value in sorted(env.items()):
            stream.write(f"{key}={dotenv(value)}\n")
    # A volume alias preserves absolute paths in Git worktrees and task records.
    # It is a second mount of the Docker volume, not a host-directory bind.
    local = {"services": {"runtime": {
        "environment": {"MULTICA_WORKSPACES_ROOT": str(args.workspaces)},
        "volumes": [f"runtime_workspaces:{args.workspaces}"]}}}
    if args.relay_network:
        local["networks"] = {"migration_relay": {"external": True, "name": args.relay_network}}
        for service in ["runtime", "backend"]:
            local["services"].setdefault(service, {})["networks"] = ["default", "migration_relay"]
    # JSON is valid YAML, and handles paths without shell/YAML interpolation.
    write_json(ROOT / "compose.local.yml", local)
    state = {"phase": "prepared", "backup": str(backup), "source_backend": args.source_backend,
             "source_frontend": args.source_frontend, "source_postgres": args.source_postgres,
             "source_workspaces": str(args.workspaces), "target_project": project}
    write_json(ROOT / "migration-state.json", state)
    compose = compose_command(project)
    print("Building sandbox images before source shutdown.", flush=True)
    with (backup / "build.log").open("w") as log:
        run(["bash", "-c", 'source "$1/common.sh"; build_images', "migration", str(ROOT)], stdout=log, stderr=log)
    print("Stopping source app writes; source database and volumes remain intact.", flush=True)
    run(["docker", "stop", args.source_frontend, args.source_backend])
    state["phase"] = "source-stopped"
    write_json(ROOT / "migration-state.json", state)
    if sql(args.source_postgres, source_user, source_db, active_query) != "0":
        raise RuntimeError("Tasks appeared during build. Restart source and drain tasks before continuing.")
    before = fingerprints(args.source_postgres, source_user, source_db)
    write_json(backup / "source-fingerprints.json", before)
    print("Saving the complete database and uploaded files.", flush=True)
    with (backup / "database.dump").open("wb") as stream:
        run(["docker", "exec", args.source_postgres, "pg_dump", "-U", source_user,
             "-d", source_db, "-Fc", "--no-owner", "--no-acl"], stdout=stream)
    uploads = next(m["Name"] for m in source["Mounts"] if m["Destination"] == "/app/data/uploads")
    image = project + "-runtime:local"
    with (backup / "uploads.tar").open("wb") as stream:
        run(["docker", "run", "--rm", "--network", "none", "--mount",
             f"type=volume,src={uploads},dst=/source,readonly", image, "tar", "-C", "/source", "-cf", "-", "."], stdout=stream)
    run(compose + ["up", "-d", "--wait", "postgres"])
    target_db = project + "-postgres-1"
    if sql(target_db, "multica", "multica", "SELECT count(*) FROM pg_tables WHERE schemaname='public'") != "0":
        raise RuntimeError("Target database is not empty")
    with (backup / "database.dump").open("rb") as stream:
        run(["docker", "exec", "-i", target_db, "pg_restore", "-U", "multica", "-d", "multica",
             "--no-owner", "--no-acl", "--exit-on-error", "--single-transaction"], stdin=stream)
    restored = fingerprints(target_db, "multica", "multica")
    write_json(backup / "restored-fingerprints.json", restored)
    if restored != before:
        raise RuntimeError("Restored table fingerprints differ from the source; do not start the target")
    print(f"Verified exact row counts and content fingerprints for all {len(before)} tables.", flush=True)
    finish_data_copy(args, compose, project, backup, state)


def finish_data_copy(args, compose, project, backup, state):
    image = project + "-runtime:local"
    target_db = project + "-postgres-1"
    run(compose + ["create", "backend", "runtime"])
    with (backup / "uploads.tar").open("rb") as stream:
        run(["docker", "run", "--rm", "-i", "--network", "none", "--user", "0:0", "--mount",
             f"type=volume,src={project}_backend_uploads,dst=/target", image,
             "tar", "-C", "/target", "-xpf", "-"], stdin=stream)
    print("Copying task workspaces, including Git metadata and uncommitted files.", flush=True)
    producer = subprocess.Popen(["tar", "-C", str(args.workspaces), "-cf", "-", "."], stdout=subprocess.PIPE)
    try:
        run(compose + ["run", "--rm", "-T", "--no-deps", "runtime", "tar", "--no-same-owner", "-C", "/workspaces", "-xpf", "-"], stdin=producer.stdout)
    finally:
        producer.stdout.close()
        producer.wait()
    if producer.returncode:
        raise RuntimeError("Source workspace archive failed")
    if args.relay_origin:
        def literal(value):
            return "'" + value.replace("'", "''") + "'"
        sql(target_db, "multica", "multica",
            "UPDATE workspace_mcp_server SET config=jsonb_set(config,'{url}',to_jsonb(replace(config->>'url',"
            f"{literal(args.old_relay_origin)},{literal(args.relay_origin)}))) "
            f"WHERE starts_with(config->>'url',{literal(args.old_relay_origin + '/')})")
    state["phase"] = "data-restored"
    write_json(ROOT / "migration-state.json", state)
    print("Starting the migrated app and applying database migrations.", flush=True)
    run(compose + ["up", "-d", "--wait", "--wait-timeout", "360", "postgres", "backend", "frontend"])
    run(["docker", "update", "--restart=no", args.source_backend, args.source_frontend], stdout=subprocess.DEVNULL)
    state["phase"] = "app-ready-runtime-setup-pending"
    write_json(ROOT / "migration-state.json", state)
    print("Migrated app: http://localhost:3100. Import provider login state and configure the runtime next.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        # Do not echo subprocess arguments or database/provider values.
        print(f"Migration stopped ({str(error) if isinstance(error, RuntimeError) else type(error).__name__}). Inspect migration-state.json and protected backup logs. "
              "Source volumes remain intact. Do not run both source and target runtimes.", file=sys.stderr)
        sys.exit(1)
