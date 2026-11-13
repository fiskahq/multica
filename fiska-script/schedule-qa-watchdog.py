#!/usr/bin/env python3
"""Install user-scoped QA endpoint recovery on Linux or macOS."""
import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("schedule", ROOT / "schedule-docker-cleanup.py")
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)
schedule.MARKER = "Managed by docker-multica-setup QA connectivity watchdog"
IDENT = "multica-qa-connectivity-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:10]


def main():
    if sys.argv[1:] == ["--help"]:
        print("Usage: ./setup-qa-watchdog.sh [--remove]\nCheck and repair QA endpoint connectivity every minute without restarting agents.")
        return
    remove = sys.argv[1:] == ["--remove"]
    if sys.argv[1:] and not remove:
        raise RuntimeError("Usage: ./setup-qa-watchdog.sh [--remove]")
    system = platform.system()
    if system not in ("Linux", "Darwin"):
        raise RuntimeError("Watchdog scheduling supports Linux and macOS; use repair-qa.sh manually here.")
    python, docker = shutil.which("python3"), shutil.which("docker")
    if not python or not docker:
        raise RuntimeError("Python 3 and Docker CLI are required.")
    context = schedule.run(docker, "context", "show", capture_output=True).stdout.strip()
    env = {"PATH": str(Path(docker).parent) + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
           "DOCKER_CONTEXT": context,
           "MULTICA_PROJECT_NAME": os.environ.get("MULTICA_PROJECT_NAME", "multica-sandbox")}
    if system == "Linux":
        directory = Path.home() / ".config/systemd/user"
        service, timer = directory / f"{IDENT}.service", directory / f"{IDENT}.timer"
        if remove:
            schedule.run("systemctl", "--user", "disable", "--now", timer.name)
            for path in (service, timer):
                if path.exists() and schedule.MARKER in path.read_text():
                    path.unlink()
            schedule.run("systemctl", "--user", "daemon-reload")
            return
        schedule.run("systemctl", "--user", "show-environment", stdout=subprocess.DEVNULL)
        settings = "\n".join("Environment=" + schedule.quote(k + "=" + v) for k, v in env.items())
        schedule.write_owned(service, f"""# {schedule.MARKER}
[Unit]
Description=Verify and repair Multica QA Docker endpoint connectivity

[Service]
Type=oneshot
{settings}
ExecStart={schedule.quote(python)} {schedule.quote(str(ROOT / 'repair-qa.py'))}
TimeoutStartSec=7min
""")
        schedule.write_owned(timer, f"""# {schedule.MARKER}
[Unit]
Description=Check Multica QA Docker connectivity every minute

[Timer]
OnStartupSec=30s
OnUnitInactiveSec=60s
AccuracySec=5s

[Install]
WantedBy=timers.target
""")
        schedule.run("systemd-analyze", "--user", "verify", str(service), str(timer))
        schedule.run("systemctl", "--user", "daemon-reload")
        schedule.run("systemctl", "--user", "enable", "--now", timer.name)
        print(f"Enabled {timer.name}; logs: journalctl --user -u {service.name}")
    else:
        label = "local." + IDENT
        path = Path.home() / "Library/LaunchAgents" / f"{label}.plist"
        domain = f"gui/{os.getuid()}"
        if remove:
            subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=False)
            if path.exists() and schedule.MARKER in path.read_text():
                path.unlink()
            return
        data = {"Label": label, "Comment": schedule.MARKER,
                "ProgramArguments": [python, str(ROOT / "repair-qa.py")],
                "EnvironmentVariables": env, "RunAtLoad": True, "StartInterval": 60,
                "ProcessType": "Background"}
        schedule.write_owned(path, plistlib.dumps(data).decode())
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        schedule.run("launchctl", "bootstrap", domain, str(path))
        print(f"Enabled {label} while logged in.")
    print(f"Docker context: {context}. Stopped services and active nested containers are preserved.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"QA watchdog setup failed: {error}", file=sys.stderr)
        sys.exit(1)
