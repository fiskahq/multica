#!/usr/bin/env python3
"""Connect the runtime Docker client to the sandbox's TLS-protected QA daemon."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def compose(*args):
    command = ["docker", "compose", "--project-name", os.environ.get("MULTICA_PROJECT_NAME", "multica-sandbox"),
               "--project-directory", str(ROOT), "--env-file", str(ROOT / ".env"),
               "-f", str(ROOT.parent / "docker-compose.selfhost.yml"),
               "-f", str(ROOT / "compose.sandbox.yml")]
    if (ROOT / "compose.local.yml").exists():
        command += ["-f", str(ROOT / "compose.local.yml")]
    return command + ["--profile", "runtime", "--profile", "qa", *args]


def pipe(source, destination):
    producer = subprocess.Popen(source, stdout=subprocess.PIPE)
    try:
        subprocess.run(destination, stdin=producer.stdout, check=True)
    finally:
        producer.stdout.close()
        status = producer.wait()
    if status:
        raise subprocess.CalledProcessError(status, source)


def ensure_network_namespace():
    """Docker restart policies can start QA before the runtime rejoins its network."""
    containers = {}
    namespaces = {}
    for service in ("runtime", "qa-docker"):
        ids = subprocess.check_output(compose("ps", "--status", "running", "-q", service), text=True).split()
        if len(ids) != 1:
            raise RuntimeError(f"Expected one running {service} container.")
        containers[service] = ids[0]
        namespaces[service] = subprocess.check_output(
            ["docker", "exec", ids[0], "readlink", "/proc/self/ns/net"], text=True).strip()
    if namespaces["runtime"] == namespaces["qa-docker"]:
        return
    active = subprocess.check_output(
        ["docker", "exec", containers["qa-docker"], "docker", "--host=unix:///run/user/1000/docker.sock",
         "ps", "-q"], text=True).strip()
    if active:
        raise RuntimeError("QA network is stale but nested containers are running; stop their owned test stacks before repair.")
    current = subprocess.check_output(compose("ps", "--status", "running", "-q", "runtime"), text=True).strip()
    if current != containers["runtime"]:
        raise RuntimeError("Runtime changed during QA inspection; retry setup.")
    print("Reconnecting idle QA Docker to the current runtime network namespace.", flush=True)
    subprocess.run(compose("up", "-d", "--no-deps", "--no-build", "--pull", "never",
                           "--force-recreate", "--wait", "--wait-timeout", "180", "qa-docker"),
                   check=True, timeout=240)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-client", help="Official docker:<version>-cli image to install without a restart")
    args = parser.parse_args()
    ensure_network_namespace()
    if args.install_client:
        subprocess.run(["docker", "pull", args.install_client], check=True)
        pipe(["docker", "run", "--rm", "--entrypoint", "tar", args.install_client,
              "-C", "/usr/local/bin", "-cf", "-", "docker"],
             compose("exec", "-T", "runtime", "tar", "-C", "/home/node/.local/bin", "-xf", "-"))
        subprocess.run(compose("exec", "-T", "runtime", "mkdir", "-p", "/home/node/.docker/cli-plugins"), check=True)
        pipe(["docker", "run", "--rm", "--entrypoint", "tar", args.install_client,
              "-C", "/usr/local/libexec/docker/cli-plugins", "-cf", "-", "."],
             compose("exec", "-T", "runtime", "tar", "-C", "/home/node/.docker/cli-plugins", "-xf", "-"))
    pipe(compose("exec", "-T", "qa-docker", "tar", "-C", "/certs/client", "-cf", "-", "ca.pem", "cert.pem", "key.pem"),
         compose("exec", "-T", "runtime", "sh", "-ec",
                 "umask 077; mkdir -p /home/node/.docker/qa-tls; "
                 "tar -C /home/node/.docker/qa-tls -xf -; chmod 600 /home/node/.docker/qa-tls/*.pem"))
    exists = subprocess.run(compose("exec", "-T", "runtime", "docker", "context", "inspect", "multica-qa"),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    endpoint = "host=tcp://127.0.0.1:2376,ca=/home/node/.docker/qa-tls/ca.pem,cert=/home/node/.docker/qa-tls/cert.pem,key=/home/node/.docker/qa-tls/key.pem"
    subprocess.run(compose("exec", "-T", "runtime", "docker", "context", "update" if exists else "create",
                           "multica-qa", "--docker", endpoint), check=True)
    subprocess.run(compose("exec", "-T", "runtime", "docker", "context", "use", "multica-qa"), check=True)
    raw = subprocess.check_output(compose("exec", "-T", "runtime", "docker", "--context", "multica-qa",
                                          "info", "--format", "{{json .SecurityOptions}}"), text=True, timeout=20)
    if "name=rootless" not in json.loads(raw):
        raise RuntimeError("QA Docker must report rootless mode")
    subprocess.run(compose("exec", "-T", "runtime", "docker", "compose", "version"), check=True)
    print("Runtime Docker CLI is connected to the separate rootless QA daemon.")


if __name__ == "__main__":
    main()
