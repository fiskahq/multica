#!/usr/bin/env python3
"""Install or inspect the background learner without restarting application services."""
import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import re

ROOT = Path(__file__).resolve().parent
NAME = "background-learner"
TITLE = "Background learning review"
PROJECT = "Learning Review"
PRIVATE_AUTOPILOT_FIX = "c3a9d8fd1e460f9cf79be7160f19cd1a12624332"


def verify_backend(version):
    match = re.search(r"-g([0-9a-f]{7,40})(?:-dirty)?$", version or "")
    ref = match[1] if match else version
    if not ref or not re.fullmatch(r"[a-zA-Z0-9._-]+", ref):
        raise RuntimeError("Backend has no verifiable source version; deploy the synced fork before enabling learning.")
    result = subprocess.run(["git", "-C", str(ROOT / ".."), "merge-base", "--is-ancestor",
                             PRIVATE_AUTOPILOT_FIX, ref], capture_output=True)
    if result.returncode:
        raise RuntimeError("The running backend predates private Autopilot protection. Deploy the synced fork before enabling learning; the learner agent is prepared but no runs were started.")


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def docker(deployment, *args, capture=True):
    script = 'source "$1/common.sh"\nshift\nrequire_docker\nrequire_env\ncompose "$@"'
    result = subprocess.run(["bash", "-c", script, "bash", str(deployment), *args],
                            capture_output=capture, text=True,
                            env=dict(os.environ, LEARNER_SOURCE_DIR=str(ROOT / "learner"),
                                     LEARNER_UID=str(os.getuid()), LEARNER_GID=str(os.getgid())))
    if result.returncode:
        raise RuntimeError("Docker learner operation failed; inspect container health. Credentials are omitted.")
    return result.stdout


def native_api(deployment, path, body):
    script = '''import json,sys,urllib.request
c=json.load(open('/home/node/.multica/config.json'))
r=urllib.request.Request('http://backend:8080'+sys.argv[1],data=sys.argv[2].encode(),headers={
 'Authorization':'Bearer '+c['token'],'X-Workspace-ID':c['workspace_id'],'Content-Type':'application/json'})
with urllib.request.urlopen(r,timeout=30) as response:
 print(response.read().decode())
'''
    return json.loads(docker(deployment, "exec", "-T", "runtime", "python3", "-c", script,
                             path, json.dumps(body)))


def configure(args, deployment):
    architect = load("configure-architect")
    architect.ROOT = deployment
    command = architect.command
    state_path = deployment / "learner-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    health = command(health=True)
    runtimes = [r for r in command("runtime", "list") if r.get("daemon_id") == health["daemon_id"]
                and r.get("provider") == "kiro" and r.get("status") == "online" and not r.get("profile_id")]
    if len(runtimes) != 1:
        raise RuntimeError("Expected one online Kiro runtime. Configure Kiro before installing the learner.")
    runtime = runtimes[0]
    if state.get("workspace_id", runtime["workspace_id"]) != runtime["workspace_id"]:
        raise RuntimeError("Learner state belongs to a different workspace.")
    state["workspace_id"] = runtime["workspace_id"]
    state.setdefault("started_at", datetime.now(timezone.utc).isoformat())
    agents = command("agent", "list")
    existing = architect.unique(agents, NAME)
    if existing and existing["id"] != state.get("agent_id"):
        raise RuntimeError("An untracked background-learner exists; reconcile learner-state.json first.")
    if state.get("agent_id") and not existing:
        raise RuntimeError("Tracked learner was removed or renamed; reconcile state first.")
    # Use an installed Kiro model, not a guessed model identifier.
    reference = architect.unique(agents, "development-release") or next(
        (a for a in agents if a.get("runtime_id") == runtime["id"] and a.get("model")), None)
    model = args.model or state.get("model") or (reference or {}).get("model")
    if not model:
        raise RuntimeError("Pass --model with an available Kiro model.")
    instructions = (ROOT / "learner/instructions.md").read_text()
    agent = command("agent", *( ["update", existing["id"]] if existing else ["create"] ),
                    "--name", NAME, "--runtime-id", runtime["id"], "--model", model,
                    "--custom-args", '["--agent-engine","v3"]', "--max-concurrent-tasks", "1",
                    "--permission-mode", "private", "--description",
                    "Reviews finished issue runs and proposes evidence-backed lessons for human review. Never publishes automatically.",
                    "--instructions", instructions)
    state.update(agent_id=agent["id"], model=model)
    architect.provision.write_state(state_path, state)
    saved = command("agent", "get", agent["id"])
    if (saved.get("max_concurrent_tasks") != 1 or saved.get("instructions") != instructions
            or saved.get("model") != model or saved.get("permission_mode") != "private"):
        raise RuntimeError("Learner agent readback differs from requested settings.")
    if args.prepare_only:
        print(json.dumps({"name": NAME, "agent_id": agent["id"], "model": model,
                          "enabled": False, "next_step": "Deploy the synced backend, then rerun setup-learner.sh."}, indent=2))
        return
    version = json.loads(docker(deployment, "exec", "-T", "runtime", "curl", "--fail", "--silent",
                                "http://backend:8080/api/config")).get("server_version", "")
    verify_backend(version)
    state["server_version"] = version
    projects = command("project", "list")
    matches = [p for p in projects if p["title"] == PROJECT]
    if len(matches) > 1 or (matches and matches[0]["id"] != state.get("project_id")):
        raise RuntimeError("Untracked Learning Review project exists; reconcile learner-state.json first.")
    if state.get("project_id") and not matches:
        raise RuntimeError("Tracked learning project was removed or renamed.")
    project = command("project", "get", matches[0]["id"]) if matches else native_api(
        deployment, "/api/projects", {"title": PROJECT, "visibility": "private",
        "description": "Private draft lessons from agent runs. Human review is required before publication."})
    if project.get("visibility") != "private":
        raise RuntimeError("Learning Review must remain a private project.")
    state["project_id"] = project["id"]
    architect.provision.write_state(state_path, state)
    pilots = command("autopilot", "list")["autopilots"]
    matches = [a for a in pilots if a["title"] == TITLE]
    if len(matches) > 1 or (matches and matches[0]["id"] != state.get("autopilot_id")):
        raise RuntimeError("An untracked or ambiguous learning Autopilot exists; reconcile state first.")
    if state.get("autopilot_id") and not matches:
        raise RuntimeError("Tracked learning Autopilot was removed or renamed.")
    pilot = command("autopilot", *( ["update", matches[0]["id"]] if matches else ["create"] ),
                    "--title", TITLE, "--agent", agent["id"], "--mode", "run_only",
                    "--project", project["id"],
                    "--description", "Review the supplied learning.review event under your background-learner instructions. Return the required JSON draft or no_lesson. Do not use tools or publish changes.")
    pilot = pilot.get("autopilot", pilot)
    state["autopilot_id"] = pilot["id"]
    architect.provision.write_state(state_path, state)
    pilot = command("autopilot", "get", state["autopilot_id"], "--show-secrets")
    triggers = pilot.get("triggers", [])
    if not state.get("trigger_id"):
        if triggers:
            raise RuntimeError("Unexpected learning triggers exist; reconcile state before enabling.")
        trigger = command("autopilot", "trigger-add", state["autopilot_id"], "--kind", "webhook",
                          "--label", "Durable background learning queue")
        state["trigger_id"] = trigger["id"]
        architect.provision.write_state(state_path, state)
        pilot = command("autopilot", "get", state["autopilot_id"], "--show-secrets")
    triggers = pilot.get("triggers", [])
    if len(triggers) != 1 or triggers[0]["id"] != state["trigger_id"] or triggers[0]["kind"] != "webhook":
        raise RuntimeError("Expected exactly the managed webhook trigger; remove unexpected schedules manually.")
    trigger = triggers[0]
    webhook_path = trigger.get("webhook_path") or "/api/webhooks/autopilots/" + trigger["webhook_token"]
    # Capture the current owner CLI credential without displaying it or mounting the runtime home.
    credentials = json.loads(docker(deployment, "exec", "-T", "runtime", "cat", "/home/node/.multica/config.json"))
    if credentials.get("workspace_id") != state["workspace_id"] or not credentials.get("token"):
        raise RuntimeError("Runtime CLI credentials do not match the learner workspace.")
    private = deployment / "learner-private"
    private.mkdir(mode=0o700, exist_ok=True)
    (private / "data").mkdir(mode=0o700, exist_ok=True)
    config = dict(state, server_url="http://backend:8080", token=credentials["token"],
                  webhook_path=webhook_path, capacity=500)
    architect.provision.write_state(private / "config.json", config)
    docker(deployment, "-f", str(ROOT / "compose.learner.yml"), "--profile", "learner",
           "up", "-d", "--no-deps", "learner", capture=False)
    print(json.dumps({"name": NAME, "agent_id": agent["id"], "model": model,
                      "autopilot_id": state["autopilot_id"], "capacity": 500, "scan_seconds": 60,
                      "started_at": state["started_at"], "automatic_publication": False}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="install", choices=("install", "status", "candidates", "show", "stop", "start"))
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--deployment-dir", type=Path, default=ROOT)
    parser.add_argument("--model", help="Kiro model; defaults to the existing release agent's model")
    parser.add_argument("--prepare-only", action="store_true", help="Create/update the learner agent without starting background work")
    args = parser.parse_args()
    deployment = args.deployment_dir.resolve()
    os.umask(0o077)
    try:
        if args.action == "install":
            with (deployment / ".operation.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                configure(args, deployment)
        else:
            base = ("-f", str(ROOT / "compose.learner.yml"), "--profile", "learner")
            if args.action in ("start", "stop"):
                docker(deployment, *base, args.action, "learner", capture=False)
            else:
                tail = [args.task_id] if args.task_id else []
                print(docker(deployment, *base, "exec", "-T", "learner", "python3", "-B",
                             "/app/service.py", args.action, *tail))
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        # Native tool errors are already sanitized by the shared command bridge.
        print(f"Learner setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
