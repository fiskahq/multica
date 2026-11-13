#!/usr/bin/env python3
"""Install user-scoped daily cleanup on Linux or macOS without Docker daemon changes."""
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
IDENT = "multica-docker-cleanup-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]
MARKER = "Managed by docker-multica-setup cleanup scheduler"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def write_owned(path, data):
    if path.exists() and MARKER not in path.read_text():
        raise RuntimeError(f"Refusing to overwrite an unmanaged file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.replace(temp, path)


def quote(value):
    if "\n" in value or "\r" in value:
        raise RuntimeError("Paths and settings must not contain newlines.")
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def main():
    remove = sys.argv[1:] == ["--remove"]
    if sys.argv[1:] and not remove:
        raise RuntimeError("Usage: schedule-docker-cleanup.py [--remove]")
    system = platform.system()
    if system not in ("Linux", "Darwin"):
        raise RuntimeError("Automatic scheduling supports Linux and macOS. Run cleanup-docker.sh manually here.")
    bash, docker = shutil.which("bash"), shutil.which("docker")
    if not bash or not docker:
        raise RuntimeError("Bash and Docker CLI must be installed.")
    context = os.environ.get("CLEANUP_DOCKER_CONTEXT") or run(docker, "context", "show", capture_output=True).stdout.strip()
    env = {"PATH": str(Path(docker).parent) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
           "CLEANUP_DOCKER_CONTEXT": context,
           "MULTICA_PROJECT_NAME": os.environ.get("MULTICA_PROJECT_NAME", "multica-sandbox"),
           "CLEANUP_MIN_AGE": "48h", "CLEANUP_HOST_CACHE": "20GB", "CLEANUP_QA_CACHE": "10GB"}
    if system == "Linux":
        unit_dir = Path.home() / ".config/systemd/user"
        service, timer = unit_dir / f"{IDENT}.service", unit_dir / f"{IDENT}.timer"
        if remove:
            run("systemctl", "--user", "disable", "--now", timer.name)
            for path in (timer, service):
                if path.exists() and MARKER in path.read_text():
                    path.unlink()
            run("systemctl", "--user", "daemon-reload")
            return
        # Require the user manager before writing anything.
        run("systemctl", "--user", "show-environment", stdout=subprocess.DEVNULL)
        settings = "\n".join("Environment=" + quote(k + "=" + v) for k, v in env.items())
        write_owned(service, f"""# {MARKER}
[Unit]
Description=Remove old unused Docker images and build cache for Multica

[Service]
Type=oneshot
{settings}
ExecStart={quote(bash)} {quote(str(ROOT / 'cleanup-docker.sh'))} --scheduled
TimeoutStartSec=30min
Nice=10
""")
        write_owned(timer, f"""# {MARKER}
[Unit]
Description=Daily conservative Docker cleanup for Multica

[Timer]
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
""")
        run("systemd-analyze", "--user", "verify", str(service), str(timer))
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "enable", "--now", timer.name)
        print(f"Enabled {timer.name}. Inspect with systemctl --user status {timer.name}")
        print(f"Cleanup logs: journalctl --user -u {service.name}")
    else:
        label = "local." + IDENT
        path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
        domain = f"gui/{os.getuid()}"
        if remove:
            subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=False)
            if path.exists() and MARKER in path.read_text():
                path.unlink()
            return
        data = {"Label": label, "Comment": MARKER,
                "ProgramArguments": [bash, str(ROOT / "cleanup-docker.sh"), "--scheduled"],
                "EnvironmentVariables": env, "StartCalendarInterval": {"Hour": 4, "Minute": 15},
                "ProcessType": "Background"}
        write_owned(path, plistlib.dumps(data).decode())
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run("launchctl", "bootstrap", domain, str(path))
        print(f"Enabled {label} for daily cleanup while logged in.")
    print(f"Docker context: {context}. Retention: 48 hours; cache targets: 20 GB host, 10 GB QA.")
    print("Volumes, containers, tagged images, workspaces, credentials and backups are preserved.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Cleanup schedule setup failed: {error}", file=sys.stderr)
        sys.exit(1)
