#!/usr/bin/env python3
"""Copy a stopped relay deployment into this setup without changing its source."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--container", required=True, help="Stopped source relay container")
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(__file__).resolve().parent
    with (root / ".operation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source = args.source.resolve(strict=True)
        target = root / "mcp-relay"
        details = json.loads(subprocess.check_output(
            ["docker", "inspect", args.container], text=True))[0]
        if details["State"]["Running"]:
            raise SystemExit("Stop the source relay after active work finishes before importing its data.")
        mounts = {m["Destination"]: Path(m["Source"]).resolve() for m in details["Mounts"]}
        for name, mount in [("config.json", "/app/config.json"),
                            (".secrets", "/run/relay-secrets"), ("data", "/data")]:
            if mounts.get(mount) != source / name:
                raise SystemExit("Source directory does not match the source container mounts.")
            if (target / name).exists():
                raise SystemExit(f"Destination {name} already exists; refusing to overwrite it.")
            if (source / name).is_symlink():
                raise SystemExit("Import requires local files and directories, not symlinks.")
        # Stage all files before publishing any private deployment paths.
        with tempfile.TemporaryDirectory(prefix=".relay-import-", dir=root) as temporary:
            staging = Path(temporary)
            for name in ["config.json", ".secrets", "data"]:
                path = source / name
                if path.is_dir():
                    if any(p.is_symlink() for p in path.rglob("*")):
                        raise SystemExit("Import requires self-contained directories without symlinks.")
                    shutil.copytree(path, staging / name)
                else:
                    shutil.copy2(path, staging / name)
            for path in staging.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
            for name in ["config.json", ".secrets", "data"]:
                (staging / name).rename(target / name)
        print("Imported relay configuration, credentials and data. Source files were preserved.")


if __name__ == "__main__":
    main()
