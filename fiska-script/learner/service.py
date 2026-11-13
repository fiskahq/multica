#!/usr/bin/env python3
"""Durable post-run learning queue. Uses only the Python standard library."""
import argparse
from contextlib import contextmanager
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

TERMINAL = {"completed", "failed"}


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def redact(value):
    """Best-effort redaction; the private review store is still sensitive data."""
    def clean(item):
        if isinstance(item, dict):
            return {k: "[REDACTED]" if re.search(r"(?i)(password|secret|token|api.?key|authorization)", k)
                    else clean(v) for k, v in item.items()}
        if isinstance(item, list):
            return [clean(v) for v in item]
        if not isinstance(item, str):
            return item
        item = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", item)
        item = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|mul_[A-Za-z0-9_-]+|mat_[A-Za-z0-9_-]+)", "[REDACTED]", item)
        return re.sub(r'''(?i)((?:api[_-]?key|password|secret|access_token|refresh_token)["']?\s*[:=]\s*["']?)[^\s"',}]+''', r'\1[REDACTED]', item)
    return json.dumps(clean(value), ensure_ascii=False)


class API:
    def __init__(self, config):
        self.config = config
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, path, body=None, key=None):
        # Never forward the owner credential across a redirect or to a caller-supplied URL.
        if not path.startswith("/api/") or "\n" in path:
            raise ValueError("Invalid API path")
        headers = {"Authorization": "Bearer " + self.config["token"],
                   "X-Workspace-ID": self.config["workspace_id"]}
        if key:
            headers["Idempotency-Key"] = key
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.config["server_url"].rstrip("/") + path,
                                     data=data, headers=headers)
        with self.opener.open(req, timeout=30) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("API response exceeds evidence limit")
            return json.loads(raw), response.headers

    def get(self, path):
        return self.request(path)[0]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Queue:
    def __init__(self, path, capacity=500):
        if not 1 <= capacity <= 500:
            raise ValueError("Queue capacity must be between 1 and 500")
        self.capacity = capacity
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
                task_id TEXT PRIMARY KEY, source TEXT NOT NULL, state TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 1, due REAL NOT NULL DEFAULT 0,
                run_id TEXT, request TEXT, candidate TEXT, error TEXT,
                created REAL NOT NULL, updated REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active ON jobs ((1)) WHERE state='active';
            CREATE INDEX IF NOT EXISTS pending_jobs ON jobs (state, due, created);
        """)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def enqueue(self, source):
        with self.transaction():
            if self.db.execute("SELECT 1 FROM jobs WHERE task_id=?", (source["id"],)).fetchone():
                return True
            count = self.db.execute("SELECT count(*) FROM jobs WHERE state IN ('pending','active')").fetchone()[0]
            if count >= self.capacity:
                return False
            now = time.time()
            self.db.execute("INSERT INTO jobs(task_id,source,state,created,updated) VALUES(?,?,'pending',?,?)",
                            (source["id"], json.dumps(source), now, now))
        return True

    def claim(self):
        with self.transaction():
            row = self.db.execute("SELECT * FROM jobs WHERE state='active'").fetchone()
            if row:
                return dict(row)
            row = self.db.execute("SELECT * FROM jobs WHERE state='pending' AND due<=? ORDER BY created,task_id LIMIT 1",
                                  (time.time(),)).fetchone()
            if row:
                self.db.execute("UPDATE jobs SET state='active',updated=? WHERE task_id=?",
                                (time.time(), row["task_id"]))
                return dict(row)

    def update(self, ident, **fields):
        allowed = {"state", "run_id", "request", "candidate", "error", "due", "attempt"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid job update")
        fields["updated"] = time.time()
        sql = ",".join(f"{key}=?" for key in fields)
        self.db.execute(f"UPDATE jobs SET {sql} WHERE task_id=?", (*fields.values(), ident))

    def retry(self, job, reason):
        # Only call after the server confirms the run is terminal or was never admitted.
        if job["attempt"] >= 3:
            self.update(job["task_id"], state="dead", error=reason, request=None)
        else:
            self.update(job["task_id"], state="pending", attempt=job["attempt"] + 1,
                        due=time.time() + 60 * 2 ** (job["attempt"] - 1), run_id=None,
                        request=None, error=reason)

    def status(self):
        return dict(self.db.execute("SELECT state,count(*) FROM jobs GROUP BY state"))


def collect(api, queue, config):
    """Rescan durable history, including runs started before installation but finished after it."""
    for agent in api.get("/api/agents?include_archived=true"):
        if agent["id"] == config["agent_id"] or agent["id"] in config.get("exclude_agents", []):
            continue
        cursor = ""
        seen = set()
        while True:
            path = f'/api/agents/{agent["id"]}/tasks?limit=200'
            if cursor:
                path += "&before=" + urllib.parse.quote(cursor, safe="")
            try:
                tasks, headers = api.request(path)
            except urllib.error.HTTPError as error:
                if error.code in (403, 404):
                    break
                raise
            for task in reversed(tasks):
                if task.get("status") not in TERMINAL or not task.get("completed_at"):
                    continue
                if timestamp(task["completed_at"]) < timestamp(config["started_at"]):
                    continue
                # Chat transcripts can contain unrelated personal context; start with issue work.
                if not task.get("issue_id"):
                    continue
                source = {k: task.get(k) for k in ("id", "agent_id", "issue_id", "status", "completed_at")}
                source["agent_name"] = agent["name"]
                if not queue.enqueue(source):
                    return "full"
            cursor = headers.get("X-Agent-Tasks-Next-Cursor", "")
            if not cursor:
                if len(tasks) == 200:
                    raise ValueError("Task history pagination unavailable; upgrade the backend")
                break
            if cursor in seen:
                raise ValueError("Repeated task history cursor")
            seen.add(cursor)
    return "ok"


def evidence(api, source):
    issue = api.get(f'/api/issues/{source["issue_id"]}')
    messages = api.get(f'/api/tasks/{source["id"]}/messages')
    comments = api.get(f'/api/issues/{source["issue_id"]}/comments?recent=5')
    # Keep the end of the run within a fixed prompt budget.
    selected, budget = [], 48000
    for msg in reversed(messages):
        item = {k: msg.get(k) for k in ("seq", "type", "content")}
        text = redact(item)
        if len(text) > 12000:
            item = {"seq": msg.get("seq"), "type": msg.get("type"),
                    "content_excerpt": text[:6000] + "\n[TRUNCATED]\n" + text[-6000:]}
            text = json.dumps(item)
        if len(text) > budget:
            break
        selected.append(json.loads(text))
        budget -= len(text)
    selected.reverse()
    context = {k: issue.get(k) for k in ("id", "identifier", "project_id", "title", "status")}
    comment_excerpt = redact(comments)[-12000:]
    return {"event": "learning.review", "event_payload": {
        "source": source, "issue": context, "messages": selected,
        "recent_comments_excerpt": comment_excerpt,
        "messages_omitted": len(messages) - len(selected),
        "review_required": True}}


def parse_candidate(result, task_id):
    value = result.get("output", result.get("summary", result)) if isinstance(result, dict) else result
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("```json") and value.endswith("```"):
            value = value[7:-3].strip()
        value = json.loads(value)
    if not isinstance(value, dict) or value.get("decision") not in ("candidate", "no_lesson"):
        raise ValueError("Invalid learner decision")
    for field in ("title", "lesson", "rationale", "applicability", "validation"):
        if not isinstance(value.get(field), str):
            raise ValueError("Invalid learner fields")
    if len(json.dumps(value)) > 16000 or not isinstance(value.get("evidence"), list):
        raise ValueError("Invalid learner evidence")
    if value["decision"] == "candidate":
        if not value["evidence"] or any(not value[k].strip() for k in ("title", "lesson", "applicability", "validation")):
            raise ValueError("Candidate lacks evidence or scope")
        for item in value["evidence"]:
            if (not isinstance(item, dict) or item.get("task_id") != task_id
                    or not isinstance(item.get("finding"), str)
                    or (item.get("seq") is not None and type(item["seq"]) is not int)):
                raise ValueError("Candidate cites a different task")
    return value


def process(api, queue, config):
    job = queue.claim()
    if not job:
        return
    if not job["run_id"]:
        if api.get("/api/config").get("server_version") != config["server_version"]:
            raise ValueError("Backend version changed; rerun learner setup to verify private-project access")
        project = api.get(f'/api/projects/{config["project_id"]}')
        if project.get("visibility") != "private":
            raise ValueError("Learning review project must remain private")
        pilot = api.get(f'/api/autopilots/{config["autopilot_id"]}')["autopilot"]
        if (pilot.get("project_id") != config["project_id"]
                or pilot.get("assignee_id") != config["agent_id"]
                or pilot.get("execution_mode") != "run_only"):
            raise ValueError("Learning Autopilot scope or assignee changed")
        if job["request"]:
            body = json.loads(job["request"])
        else:
            try:
                body = evidence(api, json.loads(job["source"]))
            except (ValueError, urllib.error.HTTPError) as error:
                if isinstance(error, urllib.error.HTTPError) and error.code >= 500:
                    raise
                queue.retry(job, "source_evidence_unavailable")
                return
            queue.update(job["task_id"], request=json.dumps(body))
        # An ambiguous HTTP failure keeps the same attempt/key/body. Never create a second run.
        reply, _ = api.request(config["webhook_path"], body,
                               f'learner:{job["task_id"]}:{job["attempt"]}')
        if not reply.get("run_id"):
            if reply.get("status") in ("ignored", "skipped", "rejected"):
                queue.retry(job, "dispatch_" + reply["status"])
            return
        queue.update(job["task_id"], run_id=reply["run_id"])
        job["run_id"] = reply["run_id"]
    run = api.get(f'/api/autopilots/{config["autopilot_id"]}/runs/{job["run_id"]}')
    if run["status"] in ("failed", "skipped", "cancelled"):
        queue.retry(job, "learner_" + run["status"])
    elif run["status"] == "completed":
        try:
            candidate = parse_candidate(run.get("result"), job["task_id"])
        except (ValueError, TypeError):
            queue.retry(job, "invalid_learner_output")
            return
        if candidate["decision"] == "candidate":
            request = json.loads(queue.db.execute("SELECT request FROM jobs WHERE task_id=?", (job["task_id"],)).fetchone()[0])
            sequences = {m.get("seq") for m in request["event_payload"]["messages"]}
            if any(e.get("seq") is not None and e["seq"] not in sequences for e in candidate["evidence"]):
                queue.retry(job, "unknown_evidence_sequence")
                return
            candidate["source_project_id"] = request["event_payload"]["issue"].get("project_id")
            candidate["source_agent_id"] = json.loads(job["source"])["agent_id"]
        queue.update(job["task_id"], state="review" if candidate["decision"] == "candidate" else "no_lesson",
                     candidate=redact(candidate), request=None, error=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "status", "candidates", "show"))
    parser.add_argument("task_id", nargs="?")
    parser.add_argument("--data", default="/data")
    parser.add_argument("--config", default="/run/learner/config.json")
    args = parser.parse_args()
    if args.action == "show" and not args.task_id:
        parser.error("show requires a source task ID")
    os.umask(0o077)
    data = Path(args.data)
    data.mkdir(parents=True, exist_ok=True)
    config = json.loads(Path(args.config).read_text())
    queue = Queue(data / "queue.sqlite3", config.get("capacity", 500))
    if args.action == "status":
        heartbeat = queue.db.execute("SELECT value FROM settings WHERE key='heartbeat'").fetchone()
        print(json.dumps({"counts": queue.status(), "heartbeat": json.loads(heartbeat[0]) if heartbeat else None}))
        return
    if args.action == "candidates":
        rows = queue.db.execute("SELECT task_id,source,candidate FROM jobs WHERE state='review' ORDER BY created")
        print(json.dumps([{"task_id": r[0], "source": json.loads(r[1]), "candidate": json.loads(r[2])} for r in rows], indent=2))
        return
    if args.action == "show":
        row = queue.db.execute("SELECT task_id,source,state,attempt,run_id,candidate,error FROM jobs WHERE task_id=?", (args.task_id,)).fetchone()
        print(json.dumps(dict(row) if row else None, indent=2))
        return
    # A kernel lock spans network requests and survives no process crash. The database also
    # enforces capacity and one active item, including concurrent setup/review commands.
    with (data / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        api = API(config)
        next_scan = 0
        while True:
            report = {"at": datetime.now(timezone.utc).isoformat()}
            if time.monotonic() >= next_scan:
                try:
                    report["scan"] = collect(api, queue, config)
                except Exception as error:
                    report["scan_error"] = type(error).__name__
                next_scan = time.monotonic() + 60
            try:
                process(api, queue, config)
                report["ok"] = True
            except Exception as error:
                # URLs, HTTP errors and provider output can contain secrets.
                report.update(ok=False, error=type(error).__name__)
            report["counts"] = queue.status()
            queue.db.execute("INSERT INTO settings VALUES('heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (json.dumps(report),))
            print(json.dumps(report), flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
