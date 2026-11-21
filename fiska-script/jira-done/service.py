#!/usr/bin/env python3
"""Reconcile new Multica Done transitions to the explicitly linked Jira issue."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

KEY = r"[A-Z][A-Z0-9_]*-[1-9][0-9]*"
SUCCESS_STATUSES = {"done", "resolved", "completed"}
SUCCESS_RESOLUTIONS = ["done / fixed", "fixed", "done", "resolved", "completed"]
NOTE_KEY = "jira_done_sync"


class Blocked(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http(url, method, headers, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, method=method, data=data,
                                     headers=dict(headers, **{"Content-Type": "application/json"}))
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise RuntimeError("Response too large")
        return json.loads(raw) if raw else None


class Multica:
    def __init__(self, config):
        self.config = config

    def request(self, path, method="GET", body=None):
        if not path.startswith("/api/issues"):
            raise ValueError("Only issue APIs are used by Done sync")
        return http(self.config["server_url"].rstrip("/") + path, method,
                    {"Authorization": "Bearer " + self.config["token"],
                     "X-Workspace-ID": self.config["workspace_id"]}, body)

    def issues(self):
        rows, offset = {}, 0
        while True:
            page = self.request(f"/api/issues?limit=100&offset={offset}")
            for issue in page["issues"]:
                rows[issue["id"]] = issue
            offset += len(page["issues"])
            if offset >= page["total"]:
                return list(rows.values())
            if not page["issues"]:
                raise RuntimeError("Incomplete issue scan")

    def get(self, ident):
        return self.request("/api/issues/" + ident)

    def note(self, ident, value):
        current = self.get(ident)
        if current.get("metadata", {}).get(NOTE_KEY) != value:
            self.request(f"/api/issues/{ident}/metadata/{NOTE_KEY}", "PUT", {"value": value})


class Jira:
    def __init__(self, config):
        self.config = config
        self.protocol = None
        self.sequence = 0

    def rpc(self, method, params):
        self.sequence += 1
        headers = {"Authorization": "Bearer " + self.config["relay_token"],
                   "Accept": "application/json, text/event-stream"}
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        reply = http(self.config["relay_url"], "POST", headers,
                     {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params})
        if reply.get("id") != self.sequence or "error" in reply:
            raise RuntimeError("Relay protocol failure")
        result = reply["result"]
        if result.get("isError"):
            raise RuntimeError("Jira request failed; check access or workflow requirements")
        return result

    def request(self, method, path, body=None, query=None):
        allowed = re.fullmatch(r"/rest/api/3/issue/" + KEY + r"(?:/(transitions|assignee))?", path)
        if not allowed or (method, allowed[1]) not in {
                ("GET", None), ("GET", "transitions"), ("PUT", "assignee"), ("POST", "transitions")}:
            raise ValueError("Done sync cannot use this Jira operation")
        if not self.protocol:
            self.protocol = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                     "clientInfo": {"name": "multica-jira-done", "version": "1"}})["protocolVersion"]
        args = dict(method=method, path=path)
        if body is not None:
            args["body"] = body
        if query:
            args["query"] = query
        result = self.rpc("tools/call", {"name": "jira_triage__request", "arguments": args})
        value = json.loads(result["content"][0]["text"])
        return value.get("data", value) if isinstance(value, dict) else value


def primary_key(issue, site):
    keys = set()
    explicit = (issue.get("metadata") or {}).get("jira_key")
    if explicit:
        if not isinstance(explicit, str) or not re.fullmatch(KEY, explicit):
            raise Blocked("Invalid jira_key metadata")
        keys.add(explicit)
    heading = False
    exempt = False
    for raw in (issue.get("description") or "").splitlines():
        line = raw.strip().lstrip("#- ").replace("**", "").replace("`", "")
        if not line:
            continue
        match = re.match(r"(?i)^(?:Primary )?Jira(?: (?:ticket|issue|key|mapping))?\s*:\s*(.*)$", line)
        if line.lower() == "jira":
            heading = True
            continue
        value = match[1].strip() if match else line if heading else None
        heading = False
        if value is None:
            continue
        if re.match(r"(?i)^not required\b", value):
            exempt = True
            continue
        link = re.match(r"\[(" + KEY + r")\]\((https?://[^)]+)\)", value)
        url = re.match(r"https?://[^\s)]+", value)
        if link or url:
            parsed = urllib.parse.urlsplit(link[2] if link else url[0].rstrip("."))
            found = re.fullmatch(r"/browse/(" + KEY + r")/?", parsed.path)
            if parsed.netloc != urllib.parse.urlsplit(site).netloc or not found or parsed.query or parsed.fragment:
                raise Blocked("Primary Jira URL does not match the configured site and issue path")
            if link and link[1] != found[1]:
                raise Blocked("Primary Jira link text and URL disagree")
            keys.add(found[1])
        else:
            bare = re.match(r"^(" + KEY + r")(?:\b|$)", value)
            if bare:
                keys.add(bare[1])
    if len(keys) > 1 or (keys and exempt):
        raise Blocked("Conflicting primary Jira links or exemption")
    return next(iter(keys)) if keys else None, exempt


def completed(issue):
    return issue.get("status_category") in ("done", "completed") or issue.get("status") == "done"


def successful(fields):
    status = fields.get("status") or {}
    resolution = fields.get("resolution") or {}
    return (status.get("statusCategory", {}).get("key") == "done"
            and status.get("name", "").casefold() in SUCCESS_STATUSES
            and resolution.get("name", "").casefold() in SUCCESS_RESOLUTIONS)


def transition_body(transitions, account):
    choices = [t for t in transitions if t.get("isAvailable", True) and
               t.get("to", {}).get("statusCategory", {}).get("key") == "done" and
               t.get("to", {}).get("name", "").casefold() in SUCCESS_STATUSES and
               not re.search(r"(?i)cancel|abandon|kill|duplicate|reject|won.t", t.get("name", ""))]
    if len(choices) != 1:
        raise Blocked("Expected one available successful Done/Resolved transition; found " + str(len(choices)))
    selected = choices[0]
    fields = {}
    for name, definition in selected.get("fields", {}).items():
        if name == "resolution":
            values = {v.get("name", "").casefold(): v["id"] for v in definition.get("allowedValues", [])}
            picked = next((values[n] for n in SUCCESS_RESOLUTIONS if n in values), None)
            if not picked:
                raise Blocked("Completion transition has no supported Done/Fixed resolution")
            fields[name] = {"id": picked}
        elif name == "assignee":
            fields[name] = {"accountId": account}
        elif definition.get("required") and not definition.get("hasDefaultValue"):
            raise Blocked("Completion requires a value for " + name)
    return {"transition": {"id": selected["id"]}, **({"fields": fields} if fields else {})}


def connect(path):
    db = sqlite3.connect(path, isolation_level=None, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS issues(id TEXT PRIMARY KEY, identifier TEXT NOT NULL,
        done INTEGER NOT NULL, jira_key TEXT, state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
        due REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', reported TEXT NOT NULL DEFAULT '');
    """)
    return db


def observe(db, issues, config):
    first = not db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone()
    links = {}
    db.execute("BEGIN IMMEDIATE")
    try:
        for issue in issues:
            try:
                key, exempt = primary_key(issue, config["jira_site"])
                problem = "" if key or exempt else "No verified primary Jira link; set jira_key or a Jira: link"
            except Blocked as error:
                key, exempt, problem = None, False, str(error)
            done = completed(issue)
            if key:
                links.setdefault(key, []).append(issue)
            old = db.execute("SELECT * FROM issues WHERE id=?", (issue["id"],)).fetchone()
            changed = not old or not old["done"] or old["jira_key"] != key
            if not old:
                state = "baseline" if first and done else "pending" if done else "idle"
                db.execute("INSERT INTO issues(id,identifier,done,jira_key,state) VALUES(?,?,?,?,?)",
                           (issue["id"], issue.get("identifier", issue["id"]), done, key, state))
            elif not done:
                db.execute("UPDATE issues SET done=0,jira_key=?,state='idle',attempt=0,due=0,note='' WHERE id=?",
                           (key, issue["id"]))
            elif changed and old["state"] != "baseline":
                db.execute("UPDATE issues SET done=1,jira_key=?,state='pending',attempt=0,due=0,note='' WHERE id=?",
                           (key, issue["id"]))
            if done:
                state = db.execute("SELECT state FROM issues WHERE id=?", (issue["id"],)).fetchone()[0]
                if state not in ("baseline", "synced") and (exempt or problem):
                    db.execute("UPDATE issues SET state=?,note=? WHERE id=?",
                               ("exempt" if exempt else "blocked", "Jira not required" if exempt else problem, issue["id"]))
                elif state in ("blocked", "exempt") and not problem and not exempt:
                    db.execute("UPDATE issues SET state='pending' WHERE id=?", (issue["id"],))
        db.execute("INSERT OR IGNORE INTO settings VALUES('initialized','1')")
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return links


def reconcile(api, jira, job, links, config):
    key = job["jira_key"]
    def fresh():
        issue = api.get(job["id"])
        if not completed(issue) or primary_key(issue, config["jira_site"])[0] != key:
            raise Blocked("Multica status or primary Jira link changed before synchronization")
        if job["id"] not in config.get("shared_close_overrides", {}):
            for peer in links.get(key, []):
                current = api.get(peer["id"])
                if primary_key(current, config["jira_site"])[0] == key and not completed(current):
                    raise Blocked("Waiting for linked Multica ticket " + current.get("identifier", current["id"]) + " to reach Done")
        return issue
    fresh()
    path = "/rest/api/3/issue/" + key
    fields = jira.request("GET", path, query={"fields": "status,resolution,assignee"})["fields"]
    # Reconcile after every uncertain write before considering another assignment/transition.
    body = None if successful(fields) else transition_body(
        jira.request("GET", path + "/transitions", query={"expand": "transitions.fields"})["transitions"],
        config["assignee_id"])
    if (fields.get("assignee") or {}).get("accountId") != config["assignee_id"]:
        fresh()
        jira.request("PUT", path + "/assignee", {"accountId": config["assignee_id"]})
        fields = jira.request("GET", path, query={"fields": "status,resolution,assignee"})["fields"]
        if (fields.get("assignee") or {}).get("accountId") != config["assignee_id"]:
            raise RuntimeError("Jira assignment readback did not match")
    if not successful(fields):
        fresh()
        jira.request("POST", path + "/transitions", body)
        fields = jira.request("GET", path, query={"fields": "status,resolution,assignee"})["fields"]
    if not successful(fields) or (fields.get("assignee") or {}).get("accountId") != config["assignee_id"]:
        raise Blocked("Jira readback is not successful Done/Resolved with the configured assignee")
    return f"Synced {key}: {fields['status']['name']}; {fields['resolution']['name']}; assigned to {config['assignee_name']}"


def cycle(api, jira, db, config):
    links = observe(db, api.issues(), config)
    jobs = db.execute("SELECT * FROM issues WHERE state IN ('pending','retry') AND due<=? LIMIT 10", (time.time(),)).fetchall()
    for job in jobs:
        try:
            note = reconcile(api, jira, job, links, config)
            state, due = "synced", 0
        except Blocked as error:
            state, note, due = "retry", str(error), time.time() + 300
        except Exception as error:
            state = "retry"
            note = "Synchronization could not be verified (" + type(error).__name__ + "); will read Jira before retrying"
            due = time.time() + min(3600, 30 * 2 ** min(job["attempt"], 7))
        db.execute("UPDATE issues SET state=?,note=?,due=?,attempt=attempt+1 WHERE id=?", (state, note, due, job["id"]))
    for row in db.execute("SELECT * FROM issues WHERE done=1 AND state NOT IN ('baseline','idle') AND note != reported").fetchall():
        # Single-key metadata updates preserve user fields and never dispatch an agent.
        api.note(row["id"], row["note"])
        db.execute("UPDATE issues SET reported=note WHERE id=?", (row["id"],))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["serve", "status", "retry"], default="serve", nargs="?")
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(Path("/run/jira-done/config.json").read_text())
    db = connect("/data/sync.sqlite")
    if args.action == "status":
        print(json.dumps([dict(r) for r in db.execute("SELECT identifier,jira_key,state,attempt,note FROM issues WHERE state NOT IN ('idle','baseline')")], indent=2))
        return
    if args.action == "retry":
        db.execute("UPDATE issues SET due=0 WHERE state='retry'")
        return
    api, jira = Multica(config), Jira(config)
    while True:
        with open("/data/worker.lock", "a") as lock:
            # Shared-volume lock covers scan, writes and receipts across overlapping workers.
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                cycle(api, jira, db, config)
                Path("/data/heartbeat").write_text(datetime.now(timezone.utc).isoformat())
            except BlockingIOError:
                pass
            except Exception as error:
                print("Jira Done scan failed: " + type(error).__name__, flush=True)
        time.sleep(30)


if __name__ == "__main__":
    main()
