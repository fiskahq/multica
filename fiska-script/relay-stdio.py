#!/usr/bin/env python3
"""Stdio adapter for the setup's stateless, JSON-response HTTP MCP relay."""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 16 * 1024 * 1024
BRIDGE_PATH = "/opt/agent-tools/relay-stdio.py"
LIVE_PATH = "/home/node/.local/lib/multica/relay-stdio.py"
BOOTSTRAP = ("import os,runpy; paths=" + repr([BRIDGE_PATH, LIVE_PATH]) +
             "; runpy.run_path(next(p for p in paths if os.path.isfile(p)), run_name='__main__')")


def server_config(url, headers):
    return {"command": "python3", "args": ["-c", BOOTSTRAP],
            "env": {"MULTICA_RELAY_URL": url, "MULTICA_RELAY_HEADERS": json.dumps(headers)}}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run():
    url = os.environ.get("MULTICA_RELAY_URL", "")
    parsed = urllib.parse.urlsplit(url)
    headers = json.loads(os.environ.get("MULTICA_RELAY_HEADERS", "{}"))
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or not isinstance(headers, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items())):
        raise ValueError("Invalid relay configuration")
    opener = urllib.request.build_opener(NoRedirect())
    protocol = None
    while True:
        line = sys.stdin.buffer.readline(MAX_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_BYTES:
            raise ValueError("MCP input too large")
        request = json.loads(line)
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            raise ValueError("Invalid MCP request")
        notification = "id" not in request
        outgoing = dict(headers, **{"Content-Type": "application/json",
                                    "Accept": "application/json, text/event-stream"})
        if protocol:
            outgoing["MCP-Protocol-Version"] = protocol
        try:
            # Never retry a request: a timed-out Jira write might already have committed.
            with opener.open(urllib.request.Request(url, data=line, headers=outgoing), timeout=60) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("MCP response too large")
                if notification:
                    continue
                result = json.loads(body)
                if not isinstance(result, dict) or result.get("id") != request.get("id"):
                    raise ValueError("Unexpected MCP response")
                if request.get("method") == "initialize":
                    protocol = result.get("result", {}).get("protocolVersion")
        except Exception as error:
            if notification:
                continue
            status = f" (HTTP {error.code})" if isinstance(error, urllib.error.HTTPError) else ""
            result = {"jsonrpc": "2.0", "id": request.get("id"), "error": {
                "code": -32603,
                "message": "Relay request failed" + status + ". Check relay access. "
                           "A write may have completed; read back before retrying."}}
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    try:
        run()
    except (ValueError, OSError):
        print("Relay stdio adapter failed; check its configuration and connection.", file=sys.stderr)
        sys.exit(1)
