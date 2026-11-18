#!/usr/bin/env python3
"""Transfer Claude authentication through stdin, preserving unrelated runtime settings."""
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def read_object(path):
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def valid_login(login):
    return (isinstance(login, dict) and bool(login.get("accessToken"))
            and bool(login.get("refreshToken"))
            and isinstance(login.get("expiresAt"), (int, float))
            and login["expiresAt"] > time.time() * 1000)


def write_object(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".claude-auth-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(directory, payload, token=False):
    if token:
        value = payload.get("token")
        if (not isinstance(value, str) or not value.startswith("sk-ant-oat")
                or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError("Invalid setup token")
        path = directory / "settings.json"
        data = read_object(path)
        env = data.setdefault("env", {})
        if not isinstance(env, dict):
            raise ValueError("Invalid settings environment")
        env["CLAUDE_CODE_OAUTH_TOKEN"] = value
    else:
        login = payload.get("claudeAiOauth")
        if not valid_login(login):
            raise ValueError("No unexpired subscription login received")
        settings = read_object(directory / "settings.json")
        if settings.get("env", {}).get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise ValueError("Runtime uses a setup token; use --token to replace it")
        path = directory / ".credentials.json"
        data = read_object(path)
        data["claudeAiOauth"] = login
    write_object(path, data)


def main():
    directory = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    mode = sys.argv[1]
    if mode == "export-login":
        login = read_object(directory / ".credentials.json").get("claudeAiOauth")
        if not valid_login(login):
            raise ValueError("Run claude auth login on the host first")
        json.dump({"claudeAiOauth": login}, sys.stdout)
    elif mode == "prompt-token":
        if not sys.stdin.isatty():
            raise ValueError("Run --token in a terminal for a hidden prompt")
        value = getpass.getpass("Paste the claude setup-token token (hidden): ")
        json.dump({"token": value.strip()}, sys.stdout)
    elif mode in ("install-login", "install-token"):
        install(directory, json.load(sys.stdin), token=mode == "install-token")
    else:
        raise ValueError("Unknown authentication operation")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, AttributeError, EOFError):
        sys.exit("Claude authentication setup failed. Check the login/token and runtime JSON files; credentials were not printed.")
